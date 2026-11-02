# Steam QR onboarding — engineering status (November 2026)

**Current product:** FreebieHarbor v0.7.0 supports multiple separately verified SteamIDs per Friends profile, individual ASF setup requests, and a shared resource-capped Friends ASF with FreePackages. **No friend bot has completed an ASF Steam login, and automatic license activation is not yet verified or enabled.**

## Confirmed upstream behavior

- Steam OpenID verifies a SteamID only. It does not grant the client session needed by ASF.
- ASF 6.3.11.7 has interactive console QR login; [upstream PR #3618](https://github.com/JustArchiNET/ArchiSteamFarm/pull/3618) explicitly states that Headless ASF does **not** initiate QR login. Friends ASF currently runs Headless.
- [node-steam-session](https://github.com/DoctorMcKay/node-steam-session) exposes `LoginSession(EAuthTokenPlatformType.SteamClient).startWithQR()`. An account owner can approve that challenge in Steam Mobile. This is a promising *separate*, temporary QR authenticator, but the resulting SteamClient session must be safely integrated into ASF before it is useful.
- ASF source [BotDatabase.cs](https://github.com/JustArchiNET/ArchiSteamFarm/blob/main/ArchiSteamFarm/Steam/Storage/BotDatabase.cs) contains the persisted `BackingRefreshToken` property. The property alone is not proof that externally obtained sessions can be imported or survive reconnection in this release.
- Steam's [Subscriber Agreement](https://store.steampowered.com/agreement/) restricts automation, including unattended interactions. Enabling third-party FreePackages on a Steam account involves account-policy risk.

## Investigated local prototype — not deployed

A separate one-shot Node QR prototype was prepared locally with a 120-second approval window, SteamClient transport, comparison of SteamID **and** account login, no logging of session tokens, and temporary private files. Offline tests exercise matching identity, format validation, migration boundaries and the ASF database-hand-off assumptions. This prototype has **not** been merged into a release or connected to live accounts; the production Friends site does not offer a functional QR button.

## Requirements before real activation

1. **Account ownership:** Each Steam account must first be linked by Steam OpenID to its own Friends user, with explicit opt-in.
2. **QR approval:** Initiate only a user-requested, short-lived QR session. Do not ask friends to type passwords or Guard codes into FreebieHarbor.
3. **Identity check:** Ensure the authenticated SteamID and login exactly match the particular opted-in Steam account. Reject mismatches, expired QR challenges, replay and cross-user access.
4. **Private handoff:** Keep the resulting client session away from the browser, ordinary Friends database, web logs, and the owner's original Steam Farm. Never expose privileged ASF IPC to portal users.
5. **Atomic import and rollback:** Pause only the Friends ASF process, back up the correct bot's private files, import the session atomically, restart, verify live ASF login against the correct SteamID, and roll back on error.
6. **Activation:** Enable FreePackages only after verified ASF sign-in. Test actual free-to-keep entitlement acquisition with a voluntarily authorized Steam account. Never report an unverified license as obtained.
7. **Revocation:** Support disconnecting one account, ending its session, removing its private state and reauthentication without disturbing other bots in the shared process.

**Resource goal:** one shared ASF process for the small invited pilot, plus a temporary QR process *only while an owner is connecting an account*. Do not run a permanent login process or container per friend.

The remaining implementation and validation are tracked in [issue #24](https://github.com/Bl0ck154/FreebieHarbor/issues/24).
## Safer production authentication direction

ASF's [official Configuration guide](https://github.com/JustArchiNET/ArchiSteamFarm/wiki/Configuration) warns that `BotName.db` is internal persistent storage and **must not be manually edited**. Consequently, injecting an externally generated `BackingRefreshToken` directly into that database is an unsupported engineering hypothesis, not a production-ready method. A safer implementation should use a short-lived interactive ASF instance for initial QR approval, let **ASF itself** save its bot database, then validate the identity and transfer only that bot's verified state under a coordinated stop/backup/restart procedure. Test data and a mocked rollback are not substitutes for verifying this exact ASF version's behavior. Keep automatic claiming disabled until the full account lifecycle can be confirmed.
