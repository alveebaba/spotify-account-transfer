# Spotify Account Transfer (Unofficial)

[![Tests](https://github.com/alveebaba/spotify-account-transfer/actions/workflows/tests.yml/badge.svg)](https://github.com/alveebaba/spotify-account-transfer/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Copy Liked Songs, playlists and saved library items between your own Spotify accounts.** A local browser interface, a read-only source account, a backup before copying, and resumable destination writes using Spotify's official Web API.

This is an **independent, unofficial personal utility**, not affiliated with or endorsed by Spotify. It is not a hosted service, music downloader, subscription transfer, or complete account clone. Spotify's access rules, quotas and regional availability still apply.

## Quick start

Install [Python 3.10 or newer](https://www.python.org/downloads/). No Python packages, Node.js, Docker, cloud server or background Mac are needed to use the tool.

Download and extract the [source ZIP](https://github.com/alveebaba/spotify-account-transfer/archive/refs/heads/main.zip), or:

```sh
git clone https://github.com/alveebaba/spotify-account-transfer.git
cd spotify-account-transfer
python server.py
```

On macOS/Linux, use `python3 server.py` if `python` is unavailable. On Windows, `py -3 server.py` is another option. Then open **http://127.0.0.1:8787** on that same computer.

Windows users can also run `./Start-Transfer.ps1` from PowerShell. It starts a hidden local server and opens the browser; it does not install a startup task or change the firewall. If PowerShell blocks scripts, use the Python command above instead of changing machine-wide security settings.

## Before you connect

- You must be able to sign in to both accounts.
- **Each person running this project must create their own Spotify developer app.** No shared Client ID or credentials are bundled here.
- Spotify currently requires the development app's owner to have **Premium**, permits up to five allowlisted users per development app, and applies API quotas. See [Spotify's current rules](https://developer.spotify.com/documentation/web-api/concepts/quota-modes). These are Spotify requirements, not limits imposed by this project.
- Windows has been used for a real transfer. The standard-library server is intended for Windows, macOS and Linux; automated tests are not a guarantee of live Spotify compatibility on every setup.

## One-time Spotify setup

1. Sign in to the [Spotify developer dashboard](https://developer.spotify.com/dashboard) with the account that will own your app. Either old or new account is fine if that account meets Spotify's requirements.
2. Choose **Create app**, give it a name such as **Personal Library Transfer**, and select **Web API**. Review Spotify's terms yourself.
3. Add the exact redirect URI **`http://127.0.0.1:8787/callback`**. Use `127.0.0.1`, not `localhost`.
4. Open the app's **Settings > Users Management** and allowlist **both** Spotify accounts with their correct account email addresses. Successful sign-in alone does not prove the accounts are allowlisted.
5. In **Settings**, copy the **Client ID**, paste it into the local transfer tool, and click **Save**. Never paste the Client Secret.

**The Client ID identifies your developer app, not your music account. Use the same Client ID for both sign-ins.** Access is granted separately when you connect each account. This project uses [OAuth with PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow); it does not need or collect your Client Secret or Spotify password.

## Transfer: old account to new account

Keep the local server running and use the same browser for both sign-ins. The account names and IDs are displayed before any destination write.

1. Connect the old account and confirm the displayed name and account ID.
2. Back it up. A token-free JSON snapshot is saved to `data/backup.json`.
3. Connect the new account. If Spotify automatically selects the old account, sign out at https://accounts.spotify.com first, then connect again. The tool refuses identical source/destination accounts.
4. Review inventory and warnings. Paste the destination account ID into the confirmation field. Choose whether original public playlists should remain public; the default is private.
5. Click Copy / resume. Leave both libraries unchanged during transfer. Review all reported exceptions and spot-check Liked Songs and playlist order in the new account.

Your old account is not modified. Existing content in the new account is retained. Playlist copies are private unless you explicitly select **Preserve public visibility of public playlists**.

After an interruption, restart, reconnect both accounts and resume using the **same backup and privacy choice**. Do not delete the journal or make a fresh backup just to resume: that can cause duplicate new playlists. Auth tokens stay only in process memory, so reconnecting after restart is intentional.

## Coverage and limits

- Copies Liked Songs, saved albums, followed artists, shows, saved episodes and audiobooks when accessible through the app. Audiobook purchases/access rights are not transferred.
- Recreates owned/collaborative playlists with name, description, ordered items and duplicate entries. Collaborators and custom cover artwork are not cloned.
- Follows other people's playlists rather than attempting to copy inaccessible contents. A private shared playlist may require the owner to grant the new account access separately.
- Original saved dates, listening history, recommendations, Wrapped, folders, pinned order, app settings, offline downloads and local audio files are not transferable through this tool.
- Region restrictions or unavailable tracks can prevent an exact match. Warnings and failures are not reported as complete success.

## Safety, recovery and verification

- Source OAuth scopes are read-only; the source API client rejects every write. No DELETE requests are implemented for either account.
- Destination writes are additive. Existing likes/follows are checked before adding. New playlists have a temporary marker so an ambiguous create can be reconciled without blindly creating duplicates.
- Before resuming appends, the destination must exactly match an expected prefix. Playlist item order/count is reread and verified. Library additions are reread using the contains endpoint.
- `data/transfer-*.json` records created playlist IDs and library additions. These enable a reviewed manual undo on the destination; **no automatic undo is implemented**. Preserve the journal. If you also add the same items manually during transfer, attribution is not reliable.
- On an ambiguous create with no matching marker visible, the tool intentionally stops. Inspect the new account before any further attempt; never delete the journal to force a retry.
- Quota/rate-limit pauses can require waiting until Spotify's quota resets. 403 usually warrants checking allowlisting, scopes, app-owner Premium, or endpoint access; it is not a reason to bypass Spotify restrictions.
- HTTP listens only on `127.0.0.1`; no firewall changes or LAN access. API reads require the local cookie; writes additionally require the same Origin and a CSRF token. Authorization uses PKCE and expiring single-use state. Redirects cannot forward access tokens to another API host.
- Client ID, backup and journal are local files; passwords and access/refresh tokens are not written. The backup contains private library metadata: don't publish it. Stop the service when finished and remove the app's access from both Spotify accounts if no longer needed.

## Troubleshooting

| Problem | Check |
| --- | --- |
| Same account connects twice | Sign out at [accounts.spotify.com](https://accounts.spotify.com) before connecting the other account. The tool refuses an identical source and destination. |
| `INVALID_CLIENT` or redirect error | Verify your own app's Client ID and the exact loopback redirect URI, including the port and `/callback`. |
| HTTP 403 | Check **both** users are allowlisted, app-owner Premium, granted scopes and current Spotify endpoint restrictions. |
| HTTP 429 / quota exceeded | Keep the backup and journal. Pause and resume when Spotify permits requests again. Do not create new apps to evade a quota. |
| Local-file or unavailable entries | Spotify's API cannot upload your audio files. Keep the original files and review the reported omissions. |
| Uncertain playlist creation | Inspect the destination first. Never delete the journal or force a new copy to bypass the safeguard. |
| Port already in use | Stop only the known existing instance, or run `python server.py --port 8788` and register `http://127.0.0.1:8788/callback` in Spotify. |

## Local files and privacy

| Path | Contents | Publish? |
| --- | --- | --- |
| `config.json` | Your app's Client ID | No; unnecessary personal configuration |
| `data/backup.json` | Private library snapshot and account metadata | **Never** |
| `data/transfer-*.json` | Destination IDs, additions and recovery state | **Never** |
| `*.log`, `test-results/` | Local logs and test screenshots | No |

These paths are ignored by Git. **Ignored does not mean encrypted**: anyone with access to your local files or browser session may see private metadata. Do not run this as a public server or on an untrusted shared computer. Do not attach backups, tokens, account IDs or screenshots containing personal information to public issues.

To keep state outside the checkout, use `python server.py --state-dir /path/to/private-state` (use an appropriate local path on Windows). Keep using that same directory to resume. Use separate state directories for unrelated migrations, not as a way to retry the same migration without its journal.

## Development and tests

Runtime: Python standard library only. Unit/integration tests use simulated accounts and temporary directories:

```sh
python -m unittest -v
```

Optional browser tests require Node.js 20+ and development-only Playwright:

```sh
npm ci
npx playwright install chromium
npm run test:ui
```

The browser test starts its own server on a random loopback port with temporary state. It does not connect to real Spotify accounts or reuse a running transfer. Set `SPOTIFY_TRANSFER_PYTHON` to a Python executable path if needed, and optionally set `PLAYWRIGHT_CHANNEL=msedge` to test against installed Edge instead of Playwright Chromium.

Tests cover read-only source protection, PKCE, pagination, rate limits, interrupted writes, duplicate prevention, item order, destination identity, CSRF and responsive UI behavior. Live authorization and account-specific completeness still require real sign-in and a review of transfer results.

## License and contributions

[MIT](LICENSE). Contributions and sanitized bug reports are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Spotify names and marks belong to their respective owners. The software is provided without warranty; review results before changing or closing any account.

## API references

- https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow
- https://developer.spotify.com/documentation/web-api/concepts/quota-modes
- https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide
- https://developer.spotify.com/documentation/web-api/reference/save-library-items
- https://developer.spotify.com/documentation/web-api/reference/check-library-contains
- https://developer.spotify.com/documentation/web-api/reference/create-playlist
- https://developer.spotify.com/documentation/web-api/reference/add-items-to-playlist
