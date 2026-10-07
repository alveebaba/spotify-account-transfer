# Changelog

## 0.1.0 - 2026-10-07

Initial public release of Spotify Account Transfer (Unofficial).

- Local-only browser interface with separate old/source and new/destination sign-ins.
- OAuth PKCE with no Client Secret and no tokens stored on disk.
- Library backup, additive copying, resumable journals and destination verification.
- Liked Songs, saved albums, artist follows, supported podcast/audiobook library items, playlist copies and playlist follows.
- Private playlist copies by default, explicit account confirmation and read-only source protection.
- Python standard-library runtime, Windows launcher, setup and troubleshooting documentation.
- Simulated-account unit/integration tests and isolated desktop/mobile browser tests.

Known limits: no original saved dates, listening history, recommendations, folders, local audio uploads, downloaded audio, custom covers, collaborator invitations or automatic undo. Each user needs their own eligible Spotify developer app. Spotify quotas and endpoint restrictions still apply.
