# ⚓ FreebieHarbor

**A self-hosted radar for free PC games and giveaways.**

FreebieHarbor collects free-game offers in a local searchable dashboard. It keeps a history of offers, supports optional read-only SteamGifts statistics and uses no AI tokens, Steam credentials or paid APIs.

This is an early public preview of the original giveaway dashboard from a private Steam automation stack. It does not automatically enter giveaways.

## Quick start

    git clone https://github.com/Bl0ck154/FreebieHarbor.git
    cd FreebieHarbor
    docker compose up -d

Open http://127.0.0.1:18081 on your server. The port is intentionally localhost-only. From another computer:

    ssh -L 18081:127.0.0.1:18081 user@your-server

Then visit http://127.0.0.1:18081.

## Features

- GamerPower, FreeToKeep, GG.deals RSS and Alienware Arena offer discovery.
- Search, source filters, offer history and manual refresh.
- Periodic collection and local SQLite storage.
- Optional Reddit discovery through a compatible external Publisher API (off by default).
- Optional read-only SteamSelfGifter statistics (entries and recorded wins).
- JSON endpoints: /health, /api/external-offers, /api/posts and /api/rewards.
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

The Reddit adapter expects a compatible backend implementing /internal/reddit/subreddit and /internal/reddit/preflight. A first-party standalone Reddit adapter is planned. Without that backend, the rest of the radar works.

For SteamGifts stats, mount the existing SteamSelfGifter SQLite DB read-only into the container, and point STEAMGIFTS_DB to that single mounted file. Do not mount or share the containing config directory, as it can hold Steam session data.

## Related projects

- SteamSelfGifter: https://github.com/kernelcoffee/SteamSelfGifter
- ArchiSteamFarm: https://github.com/JustArchiNET/ArchiSteamFarm
- FreePackages: https://github.com/Citrinate/FreePackages

These are upstream tools, not dependencies installed or bundled by this Compose file. See THIRD_PARTY.md.

## Development

Python 3.13 standard library, no pip dependencies.

    python3 -m unittest discover -s tests -v
    DATA_DIR=./local-data PORT=8080 python3 app/hub.py

## Roadmap

Native Reddit integration; richer SteamSelfGifter and ASF read-only adapters; notification delivery; source health metrics; integration tests; source refresh reliability.

For the current design, see docs/architecture.md.

This project is not affiliated with Steam, SteamGifts, IndieGala or any game publisher. MIT license.
