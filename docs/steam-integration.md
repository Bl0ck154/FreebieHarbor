# Steam integration: one shared ASF service, per-account bots

**One GitHub repository:** `Bl0ck154/FreebieHarbor`. The main hub, Friends portal, admin tools, and shared ASF integration live together. ASF and FreePackages remain separate third-party programs with their own upstream releases.

## Confirmed architecture

- Main hub: public giveaway feed, owner-only existing Steam Farm unchanged.
- Friends portal: invitation-only individual profiles and offer states.
- Steam identity: `Verify Steam identity` on the Account page redirects to Valve's official Steam OpenID site. After validation, FreebieHarbor stores only the verified SteamID64.
- Provisioner: on explicit request, stores a friend-supplied Steam account login name (never a password) and prepares `fh_<user_id>.json` under the **private Friends ASF** configuration directory. The bot starts **disabled**.
- Friends ASF: **one** ASF process for up to five friends initially, each with an independent bot config, with FreePackages 1.7.1.2 installed. It has its own credentials and IPC password and does not mount or reuse the owner's ASF config.

**Important: Steam OpenID is only identity verification.** Valve explicitly documents that it returns a SteamID; it does not grant a background Steam network session or authority to redeem licenses. Steam's Subscriber Agreement restricts automation. Connecting the ASF bot requires the friend to approve the **separate ASF login** through Steam Mobile/Guard after the backend can securely bind that session to the verified SteamID. This session onboarding/verification is **not yet completed**. Never call a disabled, staged bot connected or promise automatic licenses.

## Official components

- ASF stable 6.3.11.7, pinned Docker image: `ghcr.io/justarchinet/archisteamfarm:6.3.11.7` (digest fixed in `deploy/friends-asf.compose.yml`).
- FreePackages 1.7.1.2; verify the downloaded `FreePackages.zip` against the SHA-256 digest published by the GitHub release asset before extraction.
- `/app/config` is a dedicated private directory, `/app/plugins/custom/FreePackages` is a read-only plugin mount.
- ASF IPC listens only on host `127.0.0.1:1246`, protected with a private random `IPCPassword`. **Never reverse proxy this privileged IPC to the Friends site.**
- Memory limit 384 MiB, CPU limit 0.4, disk/log rotation. An idle extra ASF process was observed using about 51 MiB RAM before adding bots. Each logged-in account adds some resource and network usage; measure before allowing more users.

## Setup on your own server

1. Prepare two private directories for `config` and plugin files. Do not reuse the owner farm's config.
2. Put `ASF.json` with `Headless=true`, `IPC=true`, a generated unique `IPCPassword` in the private config; `IPC.config` should listen inside the container on port 1242 only.
3. Verify and extract FreePackages to its private plugin directory, then run `docker compose -f deploy/friends-asf.compose.yml up -d --no-build` with `FRIENDS_ASF_CONFIG` and `FRIENDS_ASF_PLUGINS` paths set locally.
4. Verify plugin startup in logs and that unauthenticated requests to `127.0.0.1:1246/Api/ASF` are rejected with HTTP 401. Do not expose the port to the internet.
5. When a friend links SteamID via OpenID, and independently opts in, preview their proposed bot configuration:

       python3 tools/provision_friends_asf.py --friends-db /path/to/friends.db --asf-config /path/to/friends/asf/config

6. Use `--apply` to atomically stage a **disabled** bot config after reviewing the request. Existing bot files are never overwritten. The friend must separately finish Steam authentication before activation can be enabled. The owner should verify the authenticated bot's SteamID matches the linked ID.

The sample commands are administration tools, **not** a working public QR-login implementation. Future GUI automation must never give friends ASF IPC access, allow them to manage other bots, or collect passwords/Steam Guard codes in FreebieHarbor.

## Resource and security plan

For 3–5 friends, one ASF process avoids the overhead of several .NET processes. Avoid a container per friend unless their credentials must be isolated at an OS boundary. Separate bot configurations alone do **not** isolate Steam credentials from the shared ASF process owner: an administrator with access to that process can access all active sessions. Friends must consent explicitly to this trust model.

Only the admin provisioning service may write ASF bot configs. Friend-facing routes can only (a) verify Steam identity, (b) request or withdraw onboarding, and (c) inspect their own status. Owner bots remain in a completely separate ASF instance.

A future friend logout/revocation must stop its bot, revoke its session, and safely delete its credentials in addition to disabling the portal login. Do not enable active accounts until this lifecycle is implemented and verified.

## External references

- Valve: https://partner.steamgames.com/doc/features/auth
- ASF config and multiple bots: https://github.com/JustArchiNET/ArchiSteamFarm/wiki/Configuration
- ASF IPC: https://github.com/JustArchiNET/ArchiSteamFarm/wiki/IPC
- FreePackages: https://github.com/Citrinate/FreePackages
