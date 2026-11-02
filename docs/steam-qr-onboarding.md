# Steam QR onboarding — Friends pilot (v0.8.0)

**Implementation:** available as an optional service. **Real Steam account test:** NOT performed. The owner does not want to supply a second account, so do not report successful authentication or license acquisition without evidence.

## Architecture

1. Each Friends member verifies their own SteamIDs through official Steam OpenID, separately, and opts in for each account.
2. Friends portal checks user session and CSRF, then communicates over a private authenticated Unix socket with the local QR broker. No user can access Docker or privileged ASF IPC.
3. Broker runs at most one temporary **official ASF** container (interactive Headless=false), with a single, matching bot. It displays its native Steam QR challenge as a locally generated SVG. This temporary ASF runs without FreePackages.
4. After QR approval, the broker checks the actual authenticated SteamID against the verified OpenID SteamID and requires successful Steam login.
5. Only ASF-generated bot database files are transferred; no tokens are manually injected into BotName.db. Shared Friends ASF is stopped briefly while the target files are replaced. It is restarted and the exact SteamID and IsConnectedAndLoggedOn state are checked through its private IPC.
6. On failure, prior target bot files are restored and shared Friends ASF is restarted. Withdrawing consent disables only that bot and removes its local session files.
7. The original owner's Steam Farm remains isolated and unchanged.

## Resource limits

The shared Friends ASF is one process: 384 MiB RAM / 0.4 CPU; pilot limit five requested Steam bots total. QR bootstrap only runs on demand: 256 MiB RAM / 0.35 CPU with a three-minute timeout and one concurrent request. Broker has low CPU/RAM limits. No permanent container per Steam account.

## Server setup (opt-in only)

- Requirements: Docker with the pinned official ASF version, Python 3.12+, separate Friends ASF installed.
- Install pure-Python, pinned local QR renderer in a private virtual environment:

    python3 -m venv /srv/state/freebieharbor-friends-asf/qr-venv
    /srv/state/freebieharbor-friends-asf/qr-venv/bin/pip install -r tools/steam-qr/requirements-broker.txt

- Create a private directory /run/freebieharbor-friends-qr and a random private token file (owner UID 100, mode 0400); never log this secret. Start the provided deploy/friends-qr-broker.service using systemd.
- Bind-mount the Unix socket directory read-only into the Friends container as /qr. In its server-specific Compose override only, set:

    FRIENDS_QR_BROKER_SOCKET=/qr/broker.sock
    FRIENDS_QR_AUTH_FILE=/qr/token

- The broker listens ONLY on a Unix socket and authenticates all requests. Test HTTP 403 without the secret, login redirects, per-account ownership and CSRF.
- Run tools/steam-qr/offboard.py every five minutes after provisioning to purge a withdrawn local ASF session. Never expose the full ASF API on the web.

When configured, Friends shows an individual Steam QR button for every verified and opted-in account. Without those two environment variables, the QR button is hidden.

## Verification and limitations

All automated tests use fake accounts, mocked ASF status/rollback, and a temporary disabled test bot. A disposable official ASF was observed to start and expose its bot status, but no Steam Mobile QR approval has occurred. Actual login, the session transfer and package acquisition must be verified by an account owner before this pilot can be declared production-tested.

A host operator can access sessions held by the shared ASF process; friends must consent to that trust model. Steam restricts automation in its Subscriber Agreement and third-party FreePackages is not officially endorsed.

Docs:
- ASF: https://github.com/JustArchiNET/ArchiSteamFarm/wiki/Configuration
- ASF native QR: https://github.com/JustArchiNET/ArchiSteamFarm/pull/3618
- FreePackages: https://github.com/Citrinate/FreePackages
- Steam agreement: https://store.steampowered.com/agreement/
- Tracking: https://github.com/Bl0ck154/FreebieHarbor/issues/24
