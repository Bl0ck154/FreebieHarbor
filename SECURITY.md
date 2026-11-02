# Security

FreebieHarbor is a local self-hosted dashboard, not a public multi-user web service. It has state-changing endpoints and does not implement logins. Docker Compose binds the port to 127.0.0.1 by default.

Do not expose it directly to the public internet. If you use a reverse proxy, protect it with HTTPS, authentication, and CSRF protections.

- Never upload account cookies, Steam session files, production databases, or access tokens.
- Mount the SteamSelfGifter SQLite file read-only and only if you need the stats adapter.
- The starter install does not require account credentials or AI keys.
- Report vulnerabilities privately when they contain sensitive data. Do not post user records or tokens in public issues.
