"""Official Spotify API export and additive, resumable account migration."""

import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


READ_SCOPES = "user-library-read playlist-read-private playlist-read-collaborative user-follow-read"
WRITE_SCOPES = READ_SCOPES + " user-library-modify user-follow-modify playlist-modify-private playlist-modify-public"
KINDS = {"tracks": "track", "albums": "album", "shows": "show", "episodes": "episode", "audiobooks": "audiobook"}
LIMITATIONS = [
    "Listening history, Wrapped, recommendation history and original saved dates are not transferred.",
    "Playlist folders, pinned order, app settings, offline downloads and local audio files need separate handling.",
    "Playlist IDs, followers, collaborators and custom covers are not cloned. Collaborators must be invited again.",
    "Other people's playlists are followed, not copied. Regional or unavailable items may be skipped.",
]


class TransferError(Exception):
    pass


class APIError(TransferError):
    def __init__(self, status, message, reason="", retry_after=0):
        self.status, self.reason, self.retry_after = status, reason, retry_after
        super().__init__(f"Spotify HTTP {status}: {message}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def api_url(path, params=None):
    url = path if path.startswith("https://") else "https://api.spotify.com/v1" + path
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "api.spotify.com"
            or not parsed.path.startswith("/v1/") or parsed.fragment):
        raise TransferError("Refused a URL outside Spotify's official API")
    return url + (("&" if parsed.query else "?") + urlencode(params) if params else "")


def send(method, url, headers=None, data=None):
    request = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with build_opener(NoRedirect()).open(request, timeout=35) as response:
            raw = response.read(20_000_001)
            if len(raw) > 20_000_000:
                raise TransferError("Spotify response exceeded the safety limit")
            return json.loads(raw) if raw else None
    except HTTPError as error:
        try:
            body = json.loads(error.read(8192))
        except (ValueError, OSError):
            body = {}
        detail = body.get("error", {})
        message = detail.get("message", "Request rejected") if isinstance(detail, dict) else "Authorization rejected"
        reason = detail.get("reason", "") if isinstance(detail, dict) else str(detail)
        try:
            retry = float(error.headers.get("Retry-After", 0))
        except ValueError:
            retry = 0
        raise APIError(error.code, message, reason, retry) from None
    except (URLError, TimeoutError, OSError):
        raise TransferError("Network request failed. A write may have reached Spotify; use Resume to reconcile it.") from None


def token_request(parameters):
    result = send("POST", "https://accounts.spotify.com/api/token",
                  {"Content-Type": "application/x-www-form-urlencoded"}, urlencode(parameters).encode())
    if not isinstance(result, dict) or not result.get("access_token"):
        raise TransferError("Spotify did not issue an access token")
    result["expires_at"] = time.time() + result.get("expires_in", 3600) - 60
    return result


def oauth_start(client_id, redirect_uri, writable=False):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
              "scope": WRITE_SCOPES if writable else READ_SCOPES, "state": state,
              "code_challenge_method": "S256", "code_challenge": challenge, "show_dialog": "true"}
    return state, verifier, "https://accounts.spotify.com/authorize?" + urlencode(params)


class Spotify:
    def __init__(self, client_id, token, writable=False, transport=send, sleeper=time.sleep):
        self.client_id, self.token, self.writable = client_id, token, writable
        self.transport, self.sleeper = transport, sleeper

    def request(self, method, path, params=None, body=None):
        if method not in ("GET", "PUT", "POST") or (method != "GET" and not self.writable):
            raise TransferError("Source account is read-only; destructive methods are disabled")
        url = api_url(path, params)
        if self.token.get("expires_at", 0) <= time.time():
            refreshed = token_request({"grant_type": "refresh_token", "client_id": self.client_id,
                                       "refresh_token": self.token["refresh_token"]})
            self.token.update(refreshed)
        headers = {"Authorization": "Bearer " + self.token["access_token"], "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        for attempt in range(3):
            try:
                return self.transport(method, url, headers, data)
            except APIError as error:
                if error.status == 429 and (error.reason == "QUOTA_EXCEEDED" or error.retry_after > 30):
                    raise APIError(429, "Quota exhausted. Progress is saved; resume after the quota resets.",
                                   error.reason, error.retry_after) from None
                retryable = error.status == 429 or error.status >= 500
                if method == "POST" or not retryable or attempt == 2:
                    raise
                self.sleeper(max(1, min(30, error.retry_after or 2 ** attempt)))

    def pages(self, path, container=None):
        seen = set()
        while path:
            if path in seen:
                raise TransferError("Spotify returned a repeated pagination URL")
            seen.add(path)
            page = self.request("GET", path)
            if container:
                page = page[container]
            if not isinstance(page, dict) or not isinstance(page.get("items"), list):
                raise TransferError("Spotify returned an incomplete page; no partial export was assumed complete")
            yield from page["items"]
            path = page.get("next")


def now():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def profile(api):
    me = api.request("GET", "/me")
    if not me.get("id"):
        raise TransferError("Spotify did not identify the signed-in account")
    return {"id": me["id"], "name": me.get("display_name") or me["id"],
            "url": me.get("external_urls", {}).get("spotify", "")}


def valid_uri(uri, kinds=None):
    allowed = kinds or ("track", "episode", "album", "show", "audiobook", "artist", "playlist")
    return isinstance(uri, str) and bool(re.fullmatch(r"spotify:(" + "|".join(allowed) + r"):[A-Za-z0-9]+", uri))


def playlist_items(api, playlist_id):
    if not isinstance(playlist_id, str) or not re.fullmatch(r"[A-Za-z0-9]+", playlist_id):
        raise TransferError("Invalid playlist ID")
    return list(api.pages(f"/playlists/{playlist_id}/items?limit=50"))


def item_track(row):
    return (row.get("item") if "item" in row else row.get("track")) or {}


def export_account(api, progress=lambda message: None, cancelled=lambda: False):
    def step(message):
        if cancelled():
            raise TransferError("Paused before the next request")
        progress(message)
    owner = profile(api)
    result = {"version": 1, "created": now(), "source": owner, "library": {},
              "playlists": [], "warnings": [], "limitations": LIMITATIONS}
    for category, field in KINDS.items():
        step("Backing up " + category)
        try:
            rows = list(api.pages(f"/me/{category}?limit=50"))
        except APIError as error:
            if category == "tracks" or error.status not in (403, 404):
                raise
            result["warnings"].append(f"{category}: unavailable through this app ({error.status})")
            continue
        result["library"][category] = []
        for row in rows:
            entity = row.get(field, row) or {}
            result["library"][category].append({"uri": entity.get("uri"), "name": entity.get("name", ""),
                                                 "added_at": row.get("added_at"), "local": entity.get("is_local", False)})
    step("Backing up followed artists")
    try:
        result["library"]["artists"] = [{"uri": a.get("uri"), "name": a.get("name", "")}
                                           for a in api.pages("/me/following?type=artist&limit=50", "artists")]
    except APIError as error:
        if error.status not in (403, 404):
            raise
        result["warnings"].append("Artist follows could not be read")
    step("Reading playlist list")
    for playlist in api.pages("/me/playlists?limit=50"):
        if not playlist:
            result["warnings"].append("Spotify returned an unavailable playlist")
            continue
        step("Backing up playlist: " + playlist.get("name", "Unnamed"))
        saved = {k: playlist.get(k) for k in ("id", "name", "description", "public", "collaborative", "uri")}
        saved["owned"] = playlist.get("owner", {}).get("id") == owner["id"]
        saved["action"] = "copy" if saved["owned"] or saved["collaborative"] else "follow"
        saved["items"] = []
        if saved["action"] == "copy":
            before = api.request("GET", "/playlists/" + saved["id"])
            try:
                saved["items"] = playlist_items(api, saved["id"])
            except APIError as error:
                if error.status not in (403, 404):
                    raise
                saved["action"] = "unavailable"
                result["warnings"].append(f"Playlist {saved['name']}: contents inaccessible ({error.status})")
            after = api.request("GET", "/playlists/" + saved["id"])
            if before.get("snapshot_id") != after.get("snapshot_id"):
                raise TransferError(f"Playlist {saved['name']} changed during backup. Pause edits and back up again.")
            saved["snapshot_id"] = after.get("snapshot_id")
        result["playlists"].append(saved)
    return result


def inventory(snapshot):
    return {"source": snapshot["source"], "created": snapshot["created"],
            "library": {k: len(v) for k, v in snapshot["library"].items()},
            "playlists": [{"name": p["name"], "action": p["action"], "items": len(p["items"])} for p in snapshot["playlists"]],
            "warnings": snapshot["warnings"], "limitations": LIMITATIONS}


class Migration:
    def __init__(self, api, snapshot, directory, progress=lambda message: None, cancelled=lambda: False):
        self.api, self.snapshot, self.directory = api, snapshot, Path(directory)
        self.progress, self.cancelled = progress, cancelled
        self.target = profile(api)
        if self.target["id"] == snapshot["source"]["id"]:
            raise TransferError("Old and new accounts are identical. No writes were made.")
        identity = json.dumps([snapshot, self.target["id"]], sort_keys=True).encode()
        self.job_id = hashlib.sha256(identity).hexdigest()[:20]
        self.path = self.directory / ("transfer-" + self.job_id + ".json")
        self.journal = json.loads(self.path.read_text()) if self.path.exists() else {
            "version": 1, "source": snapshot["source"], "target": self.target,
            "created": now(), "playlists": {}, "added_library": [], "issues": [], "status": "ready"}
        self.checkpoint()

    def checkpoint(self):
        self.journal["updated"] = now()
        save_json(self.path, self.journal)

    def step(self, message):
        if self.cancelled():
            raise TransferError("Paused. Resume checks destination state before adding more items.")
        self.progress(message)

    def issue(self, message):
        if message not in self.journal["issues"]:
            self.journal["issues"].append(message)
        self.checkpoint()

    def contains(self, uris):
        response = self.api.request("GET", "/me/library/contains", {"uris": ",".join(uris)})
        if not isinstance(response, list) or len(response) != len(uris) or not all(type(x) is bool for x in response):
            raise TransferError("Spotify returned an invalid library verification result")
        return response

    def copy_library(self, category, entries):
        unique = []
        seen = set()
        for row in entries:
            uri = row.get("uri")
            if not valid_uri(uri) or row.get("local"):
                self.issue(f"{category}: skipped unavailable/local item {row.get('name', '')}")
            elif uri not in seen:
                unique.append(uri)
                seen.add(uri)
        # Oldest-first insertion is best effort; Spotify does not accept original saved dates.
        unique.reverse()
        for offset in range(0, len(unique), 40):
            self.step(f"Copying {category}: {offset}/{len(unique)} checked")
            batch = unique[offset:offset + 40]
            try:
                flags = self.contains(batch)
                missing = [uri for uri, present in zip(batch, flags) if not present]
                if missing:
                    self.journal["pending_library"] = missing
                    self.checkpoint()
                    self.api.request("PUT", "/me/library", {"uris": ",".join(missing)})
                    if not all(self.contains(missing)):
                        raise TransferError(f"Spotify accepted {category}, but verification failed. Resume later.")
                    self.journal["added_library"] = list(dict.fromkeys(self.journal["added_library"] + missing))
                    self.journal.pop("pending_library", None)
                    self.checkpoint()
            except APIError as error:
                if error.status not in (400, 403, 404):
                    raise
                self.issue(f"{category}: Spotify rejected a batch ({error.status}); this category is incomplete")
                return

    def reconcile_library(self):
        pending = self.journal.get("pending_library", [])
        if pending:
            flags = self.contains(pending)
            added = [uri for uri, present in zip(pending, flags) if present]
            self.journal["added_library"] = list(dict.fromkeys(self.journal["added_library"] + added))
            self.journal.pop("pending_library", None)
            self.checkpoint()

    def copy_playlist(self, playlist, preserve_public=False):
        pid = playlist["id"]
        self.step("Copying playlist: " + playlist["name"])
        expected = []
        for position, row in enumerate(playlist["items"]):
            track = item_track(row)
            uri = track.get("uri")
            if valid_uri(uri, ("track", "episode")) and not row.get("is_local") and not track.get("is_local"):
                expected.append(uri)
            else:
                self.issue(f"{playlist['name']}: item {position + 1} unavailable/local; omitted")
        records = self.journal["playlists"]
        record = records.setdefault(pid, {"phase": "new", "name": playlist["name"]})
        marker = f"[account-transfer:{self.job_id}:{pid}]"
        if record["phase"] == "creating":
            matches = [p for p in self.api.pages("/me/playlists?limit=50")
                       if p and p.get("owner", {}).get("id") == self.target["id"] and marker in (p.get("description") or "")]
            if len(matches) != 1:
                raise TransferError("Previous playlist creation has an uncertain result. No duplicate was created; retry after checking Spotify.")
            record.update({"id": matches[0]["id"], "phase": "created"})
            self.checkpoint()
        if record["phase"] == "new":
            record["phase"] = "creating"
            self.checkpoint()
            try:
                created = self.api.request("POST", "/me/playlists", body={"name": playlist["name"], "description": marker, "public": False})
            except APIError as error:
                if 400 <= error.status < 500:
                    record["phase"] = "new"
                    self.checkpoint()
                raise
            if not isinstance(created, dict) or not created.get("id"):
                raise TransferError("Playlist creation returned no ID. Resume will reconcile before another write.")
            record.update({"id": created["id"], "phase": "created"})
            self.checkpoint()
        target_id = record["id"]
        target_meta = self.api.request("GET", "/playlists/" + target_id)
        if target_meta.get("owner", {}).get("id") != self.target["id"]:
            raise TransferError("Destination playlist is not owned by the verified new account")
        current = [item_track(row).get("uri") for row in playlist_items(self.api, target_id)]
        if len(current) > len(expected) or current != expected[:len(current)]:
            raise TransferError(f"Destination playlist {playlist['name']} differs from the expected prefix. It was not overwritten.")
        for offset in range(len(current), len(expected), 100):
            self.step(f"Adding to {playlist['name']}: {offset}/{len(expected)}")
            self.api.request("POST", f"/playlists/{target_id}/items", body={"uris": expected[offset:offset + 100]})
            record["added_through"] = min(len(expected), offset + 100)
            self.checkpoint()
        actual = [item_track(row).get("uri") for row in playlist_items(self.api, target_id)]
        if actual != expected:
            raise TransferError(f"Final item order/count mismatch in {playlist['name']}; no success assumed")
        self.api.request("PUT", "/playlists/" + target_id, body={"name": playlist["name"],
                         "description": playlist.get("description") or "", "public": bool(preserve_public and playlist.get("public"))})
        record.update({"phase": "verified", "items": len(actual)})
        self.checkpoint()

    def run(self, preserve_public=False):
        try:
            if "preserve_public" in self.journal and self.journal["preserve_public"] != preserve_public:
                raise TransferError("Resume must use the original playlist privacy setting")
            self.journal["preserve_public"] = preserve_public
            self.journal["status"] = "running"
            self.checkpoint()
            self.reconcile_library()
            for category, entries in self.snapshot["library"].items():
                self.copy_library(category, entries)
            followed = []
            for playlist in self.snapshot["playlists"]:
                if playlist["action"] == "copy":
                    self.copy_playlist(playlist, preserve_public)
                elif playlist["action"] == "follow":
                    followed.append(playlist)
                else:
                    self.issue("Playlist contents unavailable: " + playlist["name"])
            self.copy_library("followed playlists", followed)
            self.journal["status"] = "completed_with_issues" if self.journal["issues"] or self.snapshot["warnings"] else "completed"
        except Exception:
            self.journal["status"] = "paused"
            raise
        finally:
            self.checkpoint()
        return self.journal
