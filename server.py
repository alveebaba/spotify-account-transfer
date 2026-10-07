"""Loopback-only UI. OAuth tokens remain in memory, never in files or logs."""

import argparse
import json
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from spotify import Migration, Spotify, TransferError, export_account, inventory, oauth_start, profile, save_json, token_request

ROOT = Path(__file__).resolve().parent


class App:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.session, self.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.accounts, self.pending = {}, {}
        self.cancelled = threading.Event()
        self.busy, self.message, self.error = False, "Connect your accounts", ""
        self.result, self.snapshot = None, None
        self.client_id = ""
        config = self.root / "config.json"
        backup = self.root / "data" / "backup.json"
        if config.exists():
            self.client_id = json.loads(config.read_text(encoding="utf-8"))["client_id"]
        if backup.exists():
            self.snapshot = json.loads(backup.read_text(encoding="utf-8"))

    def progress(self, message):
        with self.lock:
            self.message = message

    def work(self, kind, preserve_public=False):
        try:
            if kind == "export":
                snapshot = export_account(self.accounts["source"]["api"], self.progress, self.cancelled.is_set)
                save_json(self.root / "data" / "backup.json", snapshot)
                with self.lock:
                    self.snapshot = snapshot
                    self.message = "Backup saved. Review the destination before copying."
            else:
                migration = Migration(self.accounts["target"]["api"], self.snapshot, self.root / "data",
                                      self.progress, self.cancelled.is_set)
                result = migration.run(preserve_public)
                with self.lock:
                    self.result = {"status": result["status"], "issues": result["issues"],
                                   "playlists": len(result["playlists"]), "library_added": len(result["added_library"])}
                    self.message = "Finished with items to review" if result["status"] == "completed_with_issues" else "Transfer verified"
        except TransferError as error:
            with self.lock:
                self.error = str(error)
                self.message = "Paused; no success assumed"
        except Exception:
            with self.lock:
                self.error = "Unexpected local error. Progress is saved. No success was assumed; keep the data folder."
                self.message = "Stopped"
        finally:
            with self.lock:
                self.busy = False

    def start(self, kind, preserve_public=False):
        if self.busy:
            raise TransferError("A job is already running")
        self.busy, self.error, self.result = True, "", None
        self.message = "Starting " + kind
        self.cancelled.clear()
        threading.Thread(target=self.work, args=(kind, preserve_public), daemon=True).start()

    def status(self):
        return {"client_id": self.client_id, "csrf": self.csrf, "busy": self.busy,
                "message": self.message, "error": self.error, "result": self.result,
                "accounts": {role: value["profile"] for role, value in self.accounts.items()},
                "inventory": inventory(self.snapshot) if self.snapshot else None}


class Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, app):
        if address[0] != "127.0.0.1":
            raise ValueError("Only IPv4 loopback binding is permitted")
        self.app = app
        super().__init__(address, Handler)
        self.origin = "http://127.0.0.1:" + str(self.server_port)
        self.redirect = self.origin + "/callback"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # OAuth callback query strings contain authorization codes.

    def reply(self, status, body=b"", mime="application/json", cookie=False, location=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header("Set-Cookie", "spotify_transfer=" + self.server.app.session + "; HttpOnly; SameSite=Lax; Path=/")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") == urlsplit(self.server.origin).netloc

    def session_ok(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            value = cookies.get("spotify_transfer")
            return bool(value and secrets.compare_digest(value.value, self.server.app.session))
        except Exception:
            return False

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "Invalid host"})
        parsed = urlsplit(self.path)
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/style.css": ("style.css", "text/css")}
        if parsed.path in assets:
            name, mime = assets[parsed.path]
            return self.reply(200, (ROOT / name).read_bytes(), mime, cookie=parsed.path == "/")
        if not self.session_ok():
            return self.reply(403, {"error": "Open the local tool first in this browser"})
        if parsed.path == "/callback":
            return self.callback(parse_qs(parsed.query))
        with self.server.app.lock:
            if parsed.path == "/api/status":
                return self.reply(200, self.server.app.status())
            if parsed.path == "/api/backup" and self.server.app.snapshot:
                return self.reply(200, self.server.app.snapshot)
        return self.reply(404, {"error": "Not found"})

    def callback(self, query):
        app = self.server.app
        try:
            with app.lock:
                pending = app.pending.pop(query.get("state", [""])[0], None)
                if not pending or time.time() - pending["time"] > 600:
                    raise TransferError("Sign-in expired or did not originate here. Connect again.")
                if app.busy:
                    raise TransferError("Finish the current job before changing accounts")
                if query.get("error") or not query.get("code"):
                    raise TransferError("Spotify sign-in was cancelled or denied")
                token = token_request({"grant_type": "authorization_code", "client_id": app.client_id,
                                       "code": query["code"][0], "redirect_uri": self.server.redirect,
                                       "code_verifier": pending["verifier"]})
                role = pending["role"]
                api = Spotify(app.client_id, token, writable=role == "target")
                identity = profile(api)
                other = app.accounts.get("source" if role == "target" else "target")
                if other and other["profile"]["id"] == identity["id"]:
                    raise TransferError("That is the same account. Sign out on Spotify's site and connect the other account.")
                if role == "source" and app.snapshot and app.snapshot["source"]["id"] != identity["id"]:
                    raise TransferError("This source differs from the saved backup. Keep that backup; use a separate tool folder for another source.")
                app.accounts[role] = {"api": api, "profile": identity}
                app.error = ""
                app.message = ("Old" if role == "source" else "New") + " account connected: " + identity["name"]
        except TransferError as error:
            with app.lock:
                app.error = str(error)
        except Exception:
            with app.lock:
                app.error = "Sign-in could not be completed. Confirm the Client ID, redirect URI and app user allowlist."
        self.reply(303, location="/")

    def do_POST(self):
        app = self.server.app
        if (not self.valid_host() or not self.session_ok()
                or self.headers.get("Origin") != self.server.origin
                or not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), app.csrf)):
            return self.reply(403, {"error": "Local session or request verification failed"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096 or self.headers.get("Content-Type") != "application/json":
                return self.reply(400, {"error": "Expected a small JSON request"})
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError()
            with app.lock:
                if self.path == "/api/cancel":
                    app.cancelled.set()
                    app.message = "Pausing after the current request"
                    return self.reply(200, {"ok": True})
                if app.busy:
                    raise TransferError("Wait for the current job or pause it first")
                if self.path == "/api/config":
                    client_id = str(body.get("client_id", "")).strip()
                    if not re.fullmatch(r"[a-fA-F0-9]{32}", client_id):
                        raise TransferError("Enter the 32-character Spotify Client ID, not a Client Secret")
                    save_json(app.root / "config.json", {"client_id": client_id})
                    app.client_id, app.accounts, app.pending = client_id, {}, {}
                    app.error = ""
                    return self.reply(200, {"ok": True})
                if self.path == "/api/connect":
                    role = body.get("role")
                    if role not in ("source", "target") or not app.client_id:
                        raise TransferError("Save the Client ID first, then select old or new account")
                    state, verifier, url = oauth_start(app.client_id, self.server.redirect, role == "target")
                    app.pending = {state: {"role": role, "verifier": verifier, "time": time.time()}}
                    return self.reply(200, {"url": url})
                if self.path == "/api/disconnect":
                    app.accounts, app.pending = {}, {}
                    app.message = "Accounts disconnected; backups retained"
                    return self.reply(200, {"ok": True})
                if self.path == "/api/export":
                    if "source" not in app.accounts:
                        raise TransferError("Connect the old account first")
                    if app.snapshot:
                        raise TransferError("A backup already exists. Resume with it to avoid duplicate copies.")
                    app.start("export")
                    return self.reply(202, {"ok": True})
                if self.path == "/api/transfer":
                    target, source = app.accounts.get("target"), app.accounts.get("source")
                    if not target or not source or not app.snapshot:
                        raise TransferError("Connect both accounts and back up the old one first")
                    if source["profile"]["id"] != app.snapshot["source"]["id"]:
                        raise TransferError("Source account does not match the backup")
                    if target["profile"]["id"] == source["profile"]["id"]:
                        raise TransferError("Source and destination must be different accounts")
                    if body.get("confirm_target") != target["profile"]["id"]:
                        raise TransferError("Confirm the exact destination account ID")
                    if type(body.get("preserve_public")) is not bool:
                        raise TransferError("Choose a playlist privacy setting")
                    app.start("transfer", body["preserve_public"])
                    return self.reply(202, {"ok": True})
                if self.path == "/api/shutdown":
                    app.accounts, app.pending = {}, {}
                    self.reply(200, {"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
            self.reply(404, {"error": "Not found"})
        except (ValueError, TypeError):
            self.reply(400, {"error": "Invalid request"})
        except TransferError as error:
            self.reply(400, {"error": str(error)})
        except Exception:
            self.reply(500, {"error": "Local operation failed; no success assumed"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--state-dir", type=Path, default=ROOT,
                        help="Private directory for config.json and data/ (default: project folder)")
    args = parser.parse_args()
    with Server(("127.0.0.1", args.port), App(args.state_dir)) as server:
        print("Spotify transfer tool: " + server.origin, flush=True)
        print("Redirect URI: " + server.redirect, flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
