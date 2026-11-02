# Security

FreebieHarbor is a local self-hosted dashboard, not a public multi-user web service. It has state-changing endpoints and does not implement logins. Docker Compose binds the port to 127.0.0.1 by default. The application rejects unknown Host headers and cross-origin POSTs; ALLOWED_HOSTS explicitly permits additional hostnames for an authenticated proxy.

Do not expose it directly to the public internet. If you use a reverse proxy, protect it with HTTPS, authentication, and CSRF protections.

- Never upload account cookies, Steam session files, production databases, or access tokens.
- Mount the SteamSelfGifter SQLite file read-only and only if you need the stats adapter.
- The starter install does not require account credentials or AI keys.
- Report vulnerabilities privately when they contain sensitive data. Do not post user records or tokens in public issues.

Telegram alerts are opt-in. Store the bot token only in the ignored .env file or your secret manager; never in a public issue, screenshot, or CI logs.

The direct Python development command listens on 127.0.0.1 by default. Docker uses 0.0.0.0 inside its isolated container, while Compose binds the host port to 127.0.0.1.

For ASF integration, mount only a FreePackages plugin JSON snapshot. Never mount ASF.db or an ASF bot account database: they can contain Steam access tokens.
