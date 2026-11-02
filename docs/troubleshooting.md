# Troubleshooting

## I cannot open the web dashboard

The default Docker port binds to the *server's* loopback interface. On the same machine open http://127.0.0.1:18081. To access a remote server, use a local SSH tunnel:

    ssh -L 18081:127.0.0.1:18081 user@your-server

Then open http://127.0.0.1:18081. Do not expose the dashboard directly to the internet; it is a local app without logins.

## I see zero offers

The collector needs internet access to each source. Some services reject datacenter IPs or require regional availability. Check /health (locally or through your tunnel) and the Source status section. An empty provider feed can be valid: the hub cannot guarantee live freebies.

## My browser shows "Unknown host" or "Invalid host or origin"

The dashboard accepts only hosts listed in ALLOWED_HOSTS. For a reverse proxy with *your own authentication*, add its public hostname to ALLOWED_HOSTS and forward Host and Origin correctly. The origin guard also applies to manual refresh and state changes. Do not bypass these checks by disabling authentication.

## Telegram alerts do not arrive

Set NOTIFY_NEW_OFFERS=true, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in a local .env. Your bot must be able to message the recipient. On first enablement the app silently records existing offers, and only sends new offers later. To notify on new confirmed SteamGifts wins, additionally set NOTIFY_NEW_WINS=true and mount a **refreshed** read-only SteamSelfGifter snapshot.

Never paste the bot token in an issue or public log. A blank or invalid bot token does not prevent the dashboard from working.

## Why is the SteamGifts win count not changing?

The adapter reads a user-provided snapshot. It does not fetch your SteamGifts account or automatically update the source database. Create a fresh consistent SQLite snapshot as described in architecture.md and restart the container if the bind-mounted file was atomically replaced.

## What does "ASF packages seen" mean?

It is a counter from the optional FreePackages plugin JSON snapshot. It does **not** mean that this number of games is owned or redeemed. Do not share ASF.db or an ASF account database, which may contain authentication material.

## Can it automatically enter giveaways or claim games?

No. FreebieHarbor tracks offers, manual actions, and optional external statistics. SteamSelfGifter and ArchiSteamFarm are independent tools, with their own licenses, permissions and usage rules.

## How do I run a fully offline preview?

    DATA_DIR=./demo-data python3 tools/seed_demo.py
    DATA_DIR=./demo-data PORT=8080 python3 app/hub.py

This shows clearly labeled fictional offers and disables network polling. The seed script refuses to overwrite an existing database.

## How do I run it without building a Docker image?

Use the public `ghcr.io/bl0ck154/freebieharbor:v0.4.3` image on Linux amd64. The README shows a complete `docker run` command with a local-only port and persistent volume. For ARM devices, use the source Compose build until a native image is published.

## How do I update the prebuilt Docker container?

Your named Docker volume stores the database. Pull the desired new release tag and recreate the container with the **same** volume name. Do not delete `freebieharbor-data`. For example:

    docker pull ghcr.io/bl0ck154/freebieharbor:v0.4.3
    docker stop freebieharbor
    docker rm freebieharbor
    docker run -d --name freebieharbor --restart unless-stopped -p 127.0.0.1:18081:8080 -v freebieharbor-data:/data ghcr.io/bl0ck154/freebieharbor:v0.4.3

For upgrades from 0.3.x, the experimental Reddit routes and endpoints are retired. The previous SQLite database is not deleted by this change.

## Does GHCR publishing use my server?

No. Tagged images are built on GitHub-hosted runners by the repository's release workflow. The production Steam Farm is separate and unchanged.
