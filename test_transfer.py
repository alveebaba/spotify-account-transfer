import base64
import copy
import hashlib
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from server import App, Server
from spotify import APIError, Migration, Spotify, TransferError, api_url, export_account, oauth_start, save_json


def snapshot():
    return {"version": 1, "created": "test", "source": {"id": "old", "name": "Old"},
            "library": {"tracks": [{"uri": "spotify:track:a"}, {"uri": "spotify:track:b"}]},
            "playlists": [{"id": "original", "name": "Mix", "description": "Description", "public": True,
                           "action": "copy", "items": [{"item": {"uri": uri}} for uri in
                                                       ["spotify:track:a", "spotify:track:b", "spotify:track:a"]]}],
            "warnings": []}


class Fake:
    def __init__(self):
        self.user = "new"
        self.library, self.playlists, self.calls = set(), {}, []
        self.fail_create = self.fail_append = self.fail_library = False
        self.reject_library = False
        self.create_error = None

    def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(body)))
        if path == "/me":
            return {"id": self.user, "display_name": self.user}
        if path == "/me/library/contains":
            return [uri in self.library for uri in params["uris"].split(",")]
        if path == "/me/library":
            if self.reject_library:
                raise APIError(403, "Unavailable")
            self.library.update(params["uris"].split(","))
            if self.fail_library:
                self.fail_library = False
                raise TransferError("Lost library response")
            return None
        if path == "/me/playlists" and method == "POST":
            if self.create_error:
                error, self.create_error = self.create_error, None
                raise error
            pid = "copy" + str(len(self.playlists))
            self.playlists[pid] = {"id": pid, "owner": {"id": self.user}, "items": [], **body}
            if self.fail_create:
                self.fail_create = False
                raise TransferError("Lost creation response")
            return {"id": pid}
        if path.startswith("/playlists/"):
            pid = path.split("/")[2]
            playlist = self.playlists[pid]
            if path.endswith("/items") and method == "POST":
                playlist["items"].extend(body["uris"])
                if self.fail_append:
                    self.fail_append = False
                    raise TransferError("Lost append response")
                return {"snapshot_id": "s"}
            if method == "GET":
                return copy.deepcopy(playlist)
            if method == "PUT":
                playlist.update(body)
                return None
        raise AssertionError((method, path))

    def pages(self, path, container=None):
        if path == "/me/playlists?limit=50":
            yield from copy.deepcopy(list(self.playlists.values()))
        elif path.startswith("/playlists/"):
            pid = path.split("/")[2]
            yield from ({"item": {"uri": uri}} for uri in self.playlists[pid]["items"])
        else:
            raise AssertionError(path)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.api, self.data = Fake(), snapshot()

    def migration(self):
        return Migration(self.api, self.data, self.temp.name)

    def test_source_client_rejects_all_writes(self):
        api = Spotify("id", {}, transport=lambda *args: self.fail("Network reached"))
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            with self.assertRaises(TransferError):
                api.request(method, "/me/playlists")

    def test_api_url_refuses_other_hosts(self):
        for url in ("https://evil.test/v1/me", "https://api.spotify.com.evil.test/v1/me", "https://api.spotify.com/v1/me#fragment", "https://api.spotify.com/other"):
            with self.assertRaises(TransferError):
                api_url(url)

    def test_pkce_and_source_scopes(self):
        state, verifier, url = oauth_start("id", "http://127.0.0.1:8787/callback")
        query = parse_qs(urlsplit(url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(query["state"], [state])
        self.assertNotIn("modify", query["scope"][0])

    def test_pagination_repeated_link_detected(self):
        api = Spotify("id", {"access_token": "fake", "expires_at": time.time() + 900},
                      transport=lambda *args: {"items": [], "next": "/me/tracks"})
        with self.assertRaises(TransferError):
            list(api.pages("/me/tracks"))

    def test_read_retries_but_create_does_not(self):
        calls = []
        def fail(*args):
            calls.append(args)
            raise APIError(503, "Unavailable")
        api = Spotify("id", {"access_token": "fake", "expires_at": time.time() + 900}, True, fail, lambda n: None)
        with self.assertRaises(APIError):
            api.request("GET", "/me")
        self.assertEqual(len(calls), 3)
        calls.clear()
        with self.assertRaises(APIError):
            api.request("POST", "/me/playlists", body={})
        self.assertEqual(len(calls), 1)

    def test_quota_remains_typed_and_never_retried(self):
        calls = []
        def quota(*args):
            calls.append(args)
            raise APIError(429, "Quota", "QUOTA_EXCEEDED", 1000)
        api = Spotify("id", {"access_token": "fake", "expires_at": time.time() + 900}, True, quota)
        with self.assertRaises(APIError) as caught:
            api.request("POST", "/me/playlists", body={})
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(calls), 1)

    def test_same_account_blocked_before_writes(self):
        self.api.user = "old"
        with self.assertRaises(TransferError):
            self.migration()
        self.assertTrue(all(call[0] == "GET" for call in self.api.calls))

    def test_order_duplicates_privacy_and_idempotence(self):
        self.api.library.add("spotify:track:a")
        result = self.migration().run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["added_library"], ["spotify:track:b"])
        self.assertEqual(self.api.playlists["copy0"]["items"], ["spotify:track:a", "spotify:track:b", "spotify:track:a"])
        self.assertFalse(self.api.playlists["copy0"]["public"])
        self.migration().run()
        self.assertEqual(len(self.api.playlists), 1)
        self.assertEqual(len(self.api.playlists["copy0"]["items"]), 3)

    def test_lost_create_response_reconciles_marker(self):
        self.api.fail_create = True
        with self.assertRaises(TransferError):
            self.migration().run()
        self.migration().run()
        self.assertEqual(len(self.api.playlists), 1)
        self.assertEqual(len(self.api.playlists["copy0"]["items"]), 3)

    def test_rejected_create_can_retry_without_ambiguity(self):
        self.api.create_error = APIError(429, "Quota", "QUOTA_EXCEEDED")
        with self.assertRaises(APIError):
            self.migration().run()
        self.migration().run()
        self.assertEqual(len(self.api.playlists), 1)

    def test_lost_append_response_does_not_duplicate(self):
        self.api.fail_append = True
        with self.assertRaises(TransferError):
            self.migration().run()
        self.migration().run()
        self.assertEqual(len(self.api.playlists["copy0"]["items"]), 3)

    def test_lost_library_response_reconciles_journal(self):
        self.api.fail_library = True
        with self.assertRaises(TransferError):
            self.migration().run()
        result = self.migration().run()
        self.assertEqual(set(result["added_library"]), self.api.library)
        self.assertNotIn("pending_library", result)

    def test_external_edits_are_not_overwritten(self):
        self.migration().run()
        self.api.playlists["copy0"]["items"].append("spotify:track:external")
        with self.assertRaises(TransferError):
            self.migration().run()
        self.assertEqual(self.api.playlists["copy0"]["items"][-1], "spotify:track:external")

    def test_inaccessible_library_is_not_success(self):
        self.api.reject_library = True
        result = self.migration().run()
        self.assertEqual(result["status"], "completed_with_issues")
        self.assertTrue(result["issues"])

    def test_local_tracks_skipped_with_report(self):
        self.data["playlists"][0]["items"].append({"track": {"uri": "spotify:local:a", "is_local": True}})
        self.assertEqual(self.migration().run()["status"], "completed_with_issues")

    def test_cancellation_prevents_writes(self):
        migration = Migration(self.api, self.data, self.temp.name, cancelled=lambda: True)
        with self.assertRaises(TransferError):
            migration.run()
        self.assertTrue(all(call[0] == "GET" for call in self.api.calls))

    def test_followed_playlist_is_saved_not_cloned(self):
        self.data["playlists"] = [{"id": "someone", "action": "follow", "uri": "spotify:playlist:someone", "name": "Someone else's"}]
        self.migration().run()
        self.assertIn("spotify:playlist:someone", self.api.library)
        self.assertFalse(self.api.playlists)

    def test_batch_limits(self):
        self.data["library"]["tracks"] = [{"uri": f"spotify:track:{n}"} for n in range(101)]
        self.data["playlists"][0]["items"] = [{"item": {"uri": f"spotify:track:{n}"}} for n in range(203)]
        self.migration().run()
        library = [len(call[2]["uris"].split(",")) for call in self.api.calls if call[1] == "/me/library"]
        append = [len(call[3]["uris"]) for call in self.api.calls if call[0] == "POST" and call[1].endswith("/items")]
        self.assertEqual(library, [40, 40, 21])
        self.assertEqual(append, [100, 100, 3])

    def test_resume_rejects_privacy_change(self):
        self.migration().run()
        with self.assertRaises(TransferError):
            self.migration().run(True)

    def test_old_and_new_export_shapes(self):
        class Source:
            def request(self, method, path, *args):
                if path == "/me": return {"id": "old"}
                return {"snapshot_id": "stable"}
            def pages(self, path, container=None):
                if path.startswith("/me/tracks"):
                    yield {"track": {"uri": "spotify:track:a", "name": "Track"}, "added_at": "yesterday"}
                elif path.startswith("/me/playlists"):
                    yield {"id": "source", "name": "Mix", "owner": {"id": "old"}}
                elif path.startswith("/playlists/"):
                    yield {"item": {"uri": "spotify:track:a"}}
        result = export_account(Source())
        self.assertEqual(result["library"]["tracks"][0]["added_at"], "yesterday")
        self.assertEqual(result["playlists"][0]["action"], "copy")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(self.temp.name)
        self.server = Server(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        defaults = {"Cookie": "spotify_transfer=" + self.app.session, "Origin": self.server.origin,
                    "X-CSRF-Token": self.app.csrf, "Content-Type": "application/json"}
        defaults.update(headers or {})
        connection.request(method, path, None if body is None else json.dumps(body), defaults)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_no_token_leak_in_status(self):
        self.app.accounts["source"] = {"api": {"access_token": "SECRET"}, "profile": {"id": "old", "name": "Old"}}
        status, body, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertNotIn(b"SECRET", body)

    def test_missing_cookie_and_bad_host_rejected(self):
        self.assertEqual(self.request("/api/status", headers={"Cookie": ""})[0], 403)
        self.assertEqual(self.request("/", headers={"Host": "evil.test"})[0], 403)

    def test_csrf_and_origin_required(self):
        for headers in ({"X-CSRF-Token": "wrong"}, {"Origin": "https://evil.test"}):
            self.assertEqual(self.request("/api/config", "POST", {"client_id": "a" * 32}, headers)[0], 403)
        self.assertFalse((Path(self.temp.name) / "config.json").exists())

    def test_config_saved_without_secrets(self):
        self.assertEqual(self.request("/api/config", "POST", {"client_id": "a" * 32})[0], 200)
        self.assertEqual(json.loads((Path(self.temp.name) / "config.json").read_text()), {"client_id": "a" * 32})

    def test_oauth_state_mismatch_does_not_exchange(self):
        with patch("server.token_request") as exchange:
            self.assertEqual(self.request("/callback?state=wrong&code=secret")[0], 303)
            exchange.assert_not_called()
        self.assertIn("expired", self.app.error)

    def test_transfer_requires_destination_confirmation(self):
        self.app.snapshot = snapshot()
        self.app.accounts = {"source": {"profile": {"id": "old"}}, "target": {"profile": {"id": "new"}}}
        with patch.object(self.app, "start") as start:
            status, _, _ = self.request("/api/transfer", "POST", {"confirm_target": "old", "preserve_public": False})
            self.assertEqual(status, 400)
            start.assert_not_called()

    def test_public_bind_refused(self):
        with self.assertRaises(ValueError):
            Server(("0.0.0.0", 0), self.app)

    def test_backup_survives_restart_without_tokens(self):
        save_json(Path(self.temp.name) / "data" / "backup.json", snapshot())
        restarted = App(self.temp.name)
        self.assertEqual(restarted.snapshot["source"]["id"], "old")
        self.assertEqual(restarted.accounts, {})


if __name__ == "__main__":
    unittest.main()
