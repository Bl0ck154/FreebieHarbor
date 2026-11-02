# Server deployments and upstream sync

There are **three separate layers**:

1. **FreebieHarbor GitHub repository** is the source of truth for the public Python app.
2. **GHCR** holds versioned Docker images built from tagged GitHub releases (for example `ghcr.io/bl0ck154/freebieharbor:v0.4.3`).
3. **An installed server container** uses whichever image version its Compose file explicitly pins. It **does not automatically update** when code is pushed to GitHub.

This is intentional: a developer's push should not silently restart a real user's server or migrate their database.

## Standalone canary alongside an existing Steam Farm

Use `deploy/canary.compose.yml` to run FreebieHarbor separately, on **127.0.0.1:18083**, with its own `freebieharbor-canary_data` volume. It does not use the legacy Steam Farm database, accounts, Reddit publisher, IndieGala cookies or notification credentials.

    mkdir -p exports
    docker compose -f deploy/canary.compose.yml pull
    docker compose -f deploy/canary.compose.yml up -d --no-build
    curl --fail http://127.0.0.1:18083/health

The canary makes occasional, rate-limited public-source checks. Telegram messages are disabled by default; the poll interval is two hours to avoid duplicating production traffic. The dashboard is localhost-only.

From another computer, open an SSH tunnel:

    ssh -L 18083:127.0.0.1:18083 user@your-server

Then browse http://127.0.0.1:18083.

## Updating a canary from upstream

1. Confirm a new **stable GitHub release** and successful GHCR publication.
2. Change only the image tag in `deploy/canary.compose.yml` to the verified version. Do not track mutable `:latest`.
3. Preserve the `data` volume and take a backup if you intend to keep its history.
4. Run `docker compose -f deploy/canary.compose.yml pull` and `up -d --no-build`.
5. Verify `/health` reports the intended version and browse the dashboard. If needed, pin the previous tag and redeploy.

The Compose source itself can be synchronized with `git fetch` + `git pull --ff-only`; that **does not** update running Docker containers. There is no auto-update job.

## Why the old Steam Farm is not upgraded in place

Existing ArchiSteamFarm, SteamSelfGifter and IndieGala services are independent upstream applications with their own code, databases and policies. The old local giveaway inbox may also have private integrations or schema differences. **Do not** mount that live database into the public hub, switch its production port, replace its image or copy credentials as an upgrade shortcut.

A future production migration should compare schemas, take a consistent backup, test the import offline, preserve manual claim history and use an explicit rollback. The canary is deliberately not that migration.

## Safe statistics from an existing Steam Farm

The server canary has read-only mounts for two **sanitized exports**. They are separate from live account files and contain no Steam tokens or ASF package IDs.

Run the included standard-library exporter **as a user with read access to the source files**, replacing paths for your own installation:

    python3 tools/export_rewards.py \
      --steamgifts-source /path/to/steamselfgifter.db \
      --asf-source /path/to/FreePackages.db \
      --output-dir exports

It writes only `entries(status)`, `giveaways(code,game_name,is_won,won_at)`, and a small ASF JSON document containing aggregate counts. Both writes use atomic replacement. The source files are opened read-only. Keep the exports directory out of Git. Restart the canary once after adding the read-only volume mount; later exports can refresh the files in place. These are **recorded** wins and plugin counters; they do not verify Steam library ownership.

## Migrating the old public-offer history

The optional `tools/migrate_legacy_offers.py` script preserves only historical
GamerPower, FreeToKeep and GG.deals offers. It excludes Reddit, IndieGala and
all source `raw_json` payloads. It also excludes AI metadata and credentials.

Take a consistent SQLite backup of the *target* database first, using the
Python SQLite `backup()` API. Then preview:

    python3 tools/migrate_legacy_offers.py --source /path/to/legacy/giveaways.db --target /path/to/new/giveaways.db

If the reported counts and provider list look right, apply:

    python3 tools/migrate_legacy_offers.py --source /path/to/legacy/giveaways.db --target /path/to/new/giveaways.db --apply

The source is read-only. Existing target offers keep their metadata and
current/active status; previously unseen offers are imported as inactive history.
An earlier user choice of `claimed` or `ignored` may be transferred only
when the destination is still marked `new`. The import is idempotent. Archived
entries can be inspected in Free games → History.

Do not use this script to migrate Steam account sessions or external giveaway
automation. The old production service can continue working independently.
