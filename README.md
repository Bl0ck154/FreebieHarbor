# ⚓ FreebieHarbor

[![Python checks](https://github.com/Bl0ck154/FreebieHarbor/actions/workflows/tests.yml/badge.svg)](https://github.com/Bl0ck154/FreebieHarbor/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/Bl0ck154/FreebieHarbor?include_prereleases)](https://github.com/Bl0ck154/FreebieHarbor/releases)
[![License MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)


**A self-hosted radar for free PC games and giveaways.**

FreebieHarbor collects free-game offers in a local searchable dashboard. It keeps a history of offers, supports optional read-only SteamGifts and ASF package statistics and uses no AI tokens, Steam credentials or paid APIs.

This is an early public preview of the original giveaway dashboard from a private Steam automation stack. It does not automatically enter giveaways.

## Quick start

    git clone https://github.com/Bl0ck154/FreebieHarbor.git
    cd FreebieHarbor
    docker compose up -d

Open http://127.0.0.1:18081 on your server. The port is intentionally localhost-only. From another computer:

    ssh -L 18081:127.0.0.1:18081 user@your-server

Then visit http://127.0.0.1:18081.

## Dashboard preview

![FreebieHarbor dashboard using explicitly fictional demo offers](docs/assets/dashboard.png)

*Screenshot from the real local web app, seeded with fictional sample offers. No personal accounts or game claims are shown.*

## At a glance

| Source / integration | In the default install? | What it does |
| --- | --- | --- |
| GamerPower | Yes | Tracks active game giveaways |
| FreeToKeep.gg | Yes | Tracks free-to-keep offers |
| GG.deals RSS | Yes | Discovers limited-time PC game freebies |
| Alienware Arena | Best effort | Finds public key drops; some VPS IPs are blocked |
| Reddit | Optional | Requires a compatible, authorized Publisher API |
| SteamSelfGifter | Optional | Read-only entry and win counts from a SQLite snapshot |
| ArchiSteamFarm / FreePackages | Optional | Read-only plugin package counters from a separate JSON snapshot |
| IndieGala | No | Manual visit/discovery only; no automated entries |

## Features

- GamerPower, FreeToKeep, GG.deals RSS and Alienware Arena offer discovery.
- Search, source filters, offer history and manual refresh.
- Optional Telegram notifications for *new* offers, grouped into one message per refresh. First enabled scan establishes a baseline with no alert flood.
- Periodic collection and local SQLite storage.
- Optional Reddit discovery through a compatible external Publisher API (off by default).
- Optional read-only SteamSelfGifter statistics, recent wins, and Telegram win notifications.
- Optional ASF FreePackages snapshot counters; no ASF credentials needed.
- JSON endpoints: /health, /api/external-offers, /api/posts, /api/wins and /api/rewards.
- No external AI, Steam password or API key required.

Availability of public sources varies by region and IP. Certain providers block datacenter traffic. No prizes or free-game claims are guaranteed.

## Configuration

Copy .env.example to .env only if you need to change defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| FREEBIEHARBOR_PORT | 18081 | Local dashboard port |
| POLL_SECONDS | 1200 | Refresh cycle, minimum 300 |
| PUBLISHER_BASE | blank | Optional compatible Reddit API |
| INDIEGALA_WORKER | blank | Optional separate discovery worker |
| STEAMGIFTS_DB | blank | Optional read-only SteamSelfGifter DB path |
| ASF_PACKAGES_DB | blank | Optional read-only FreePackages metadata JSON path |
| NOTIFY_NEW_OFFERS | false | Opt in to new-offer Telegram alerts |
| NOTIFY_NEW_WINS | false | Opt in to Telegram alerts for new recorded SteamGifts wins |
| TELEGRAM_BOT_TOKEN | blank | Bot token; never commit or log it |
| TELEGRAM_CHAT_ID | blank | Telegram recipient ID |
| ALLOWED_HOSTS | localhost,127.0.0.1,::1 | Hostnames allowed to access the dashboard |

### Telegram alerts

Set NOTIFY_NEW_OFFERS=true plus TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your local .env, then restart with docker compose up -d. The first successful refresh builds the baseline without sending old results. Later new offers are delivered in a single HTML-formatted message (up to six links, with a total count). Failures retry on the following poll. Tokens are never returned by the dashboard. To receive win notifications, enable NOTIFY_NEW_WINS=true and connect the SteamSelfGifter snapshot. Win notifications start with a silent baseline and then notify only newly recorded wins as the snapshot changes.

### Reddit availability

Reddit has announced changes to its public Data API and the end of traditional RSS support on November 13, 2026. We do not rely on unauthenticated Reddit scraping or RSS in the default release. See the official Reddit Developer Platform for future supported access.

The Reddit adapter expects a compatible backend implementing /internal/reddit/subreddit and /internal/reddit/preflight. A first-party standalone Reddit adapter is planned. Without that backend, the rest of the radar works. Reddit posts are pruned after 24 hours; a compatible backend must also remove deleted content promptly to meet Reddit requirements.

For SteamGifts stats, make a consistent SQLite snapshot and use the optional overlay:

    STEAMGIFTS_DB_SOURCE=/absolute/path/to/steamgifts-snapshot.db docker compose -f compose.yml -f compose.steamgifts.yml up -d

See [the architecture notes](docs/architecture.md) for the safe snapshot command. Do not mount or publish the SteamSelfGifter config directory, as it can hold account sessions.

This project attributes GamerPower for its public [free games API](https://www.gamerpower.com/api-read).

### ASF FreePackages read-only adapter

The optional ASF adapter can read the `SeenPackages` and `NewOwnedPackages` array counts from a *snapshot* of the FreePackages plugin database (JSON file). These fields represent plugin bookkeeping, **not proof of Steam ownership or newly activated games**. Never mount or publish the main ASF configuration, which can include package tokens.

    ASF_PACKAGES_SOURCE=/absolute/path/to/freepackages-snapshot.json docker compose -f compose.yml -f compose.asf.yml up -d

The stats appear under the **Rewards** tab and in `/api/rewards`. The file must exist before enabling the overlay.

## Related projects

- SteamSelfGifter: https://github.com/kernelcoffee/SteamSelfGifter
- ArchiSteamFarm: https://github.com/JustArchiNET/ArchiSteamFarm
- FreePackages: https://github.com/Citrinate/FreePackages

These are upstream tools, not dependencies installed or bundled by this Compose file. See THIRD_PARTY.md.

## Project documentation

- [Architecture and safe database snapshots](docs/architecture.md)
- [Setup troubleshooting](docs/troubleshooting.md)
- [Change history](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security and safe deployment](SECURITY.md)
- [Third-party attribution](THIRD_PARTY.md)

Questions and ideas: use [GitHub Discussions](https://github.com/Bl0ck154/FreebieHarbor/discussions). Bugs and supported source requests have guided [issue templates](https://github.com/Bl0ck154/FreebieHarbor/issues/new/choose).

## Development

Python 3.13 standard library, no pip dependencies.

    python3 -m unittest discover -s tests -v
    DATA_DIR=./local-data PORT=8080 python3 app/hub.py

## Try the dashboard with fictional demo data

You can preview the layout without any external API requests or account data:

    DATA_DIR=./demo-data python3 tools/seed_demo.py
    DATA_DIR=./demo-data PORT=8080 python3 app/hub.py

Open http://127.0.0.1:8080/dashboard. The demo data is explicitly labeled and the source poller is disabled. The fixture script refuses to overwrite an existing database.

## Health and tests

The HTTP health endpoint is available at /health. Run local tests with Python 3.13; GitHub Actions runs the same suite on pushes and pull requests. Docker Compose config validation is separate from a full image smoke test.

## Roadmap

Native Reddit integration (subject to provider permission); verified ASF license ownership adapter; source health metrics; more provider mocks; source refresh reliability.

For the current design, see docs/architecture.md.

This project is not affiliated with Steam, SteamGifts, IndieGala or any game publisher. MIT license.
