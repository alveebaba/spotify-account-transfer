# Contributing

Open an issue describing the problem or proposed change before undertaking a large feature. Keep reports sanitized: never upload real backups, journals, tokens, Client Secrets, account emails or personal screenshots.

## Local checks

1. Install Python 3.10+ and run `python -m unittest -v`.
2. For UI changes, install Node.js 20+, run `npm ci`, `npx playwright install chromium`, then `npm run test:ui`.
3. Keep changes narrow and add focused tests. Tests must use fake accounts and temporary state, never personal Spotify credentials.
4. Check staged files before committing. `config.json`, `data/`, logs, environment files and screenshots must remain private.

## Invariants

- Old/source accounts stay read-only.
- Do not silently overwrite, clear or delete destination content.
- Do not bypass Spotify restrictions or scrape cookies from a browser.
- Preserve resumable journals and reconcile uncertain writes before retrying.
- Report partial results honestly; tests alone do not prove a complete live transfer.
- Keep runtime dependencies at the Python standard library unless a change has a clear justification.

Contributions are provided under the project's MIT license. Security concerns should use the private process in SECURITY.md.
