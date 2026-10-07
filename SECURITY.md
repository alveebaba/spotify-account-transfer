# Security and privacy

This is a single-user, loopback-only desktop utility, not a multi-user web service. Do not expose it through a reverse proxy, public tunnel or forwarded port. Local files and browser sessions are trusted; the tool does not protect against malware or another person with access to your OS account.

## Support limitations

This repository is shared as-is and has no dedicated security support or response commitment. Do not assume an issue or message will receive a response. Do not put exploitable details, real account data, OAuth codes, access/refresh tokens, credentials or library backups into a public issue. Use fake data for any shared reproduction and assess the security of your own copy or fork before use.

## Credentials and data

- OAuth tokens are kept in process memory, not written to disk. The Client ID is not a secret but local configuration is still excluded from Git.
- Source access is read-only. Destination writes require an explicit account-ID confirmation. No DELETE API operations are implemented.
- API URLs are restricted to Spotify's official host, and HTTP redirects cannot forward a bearer token to another origin.
- The callback uses PKCE and expiring single-use state. Local writes require the session cookie, matching Origin and CSRF token.
- Backups and journals contain private metadata and are not encrypted. Store them privately, preserve them for recovery and keep them out of public repositories and bug reports.
- Stop the service after use. Removing the app under Spotify's account app-access settings revokes its authorization.

This project is not actively maintained; no security updates are promised. Automated tests and these safeguards are not a formal security audit or a guarantee against every failure.
