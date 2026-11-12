# FreebieHarbor Friends — invitation-only preview

Friends mode runs as an optional second service. Users register with a single-use invite and a password, then browse the same public offer feed with their own claim and ignore history.

The existing owner-only Steam Farm stays completely separate. Friends accounts do not have SteamGifts or IndieGala automated entry, and the portal never receives Steam passwords, Steam Guard codes, browser sessions or ASF tokens.

## Available in this preview

- One-time invitations administered locally.
- Independent profiles, password hashing with scrypt, random session cookies, CSRF checks and individual offer states.
- Sanitized public game catalog exported from the existing FreebieHarbor database.
- Login rate limiting and administrator controls to invite or disable accounts from a dedicated Admin page, with server-only bootstrap of the initial administrator.

Steam OpenID identity linking and a separate ASF connection request are available in the next release. The second Steam/ASF authorization is **not yet** complete, so no automatic licenses are claimed for friends. Per-user notifications, recovery, self-signup and billing remain out of scope.

## Deployment and invitations

The dedicated deployment is in deploy/friends.compose.yml and listens on localhost port 18084. It has its own database volume, and mounts a sanitized export of the public catalog read-only. It is not enabled by the default compose.

For local preview, launch from the repository checkout with:

    docker compose -f deploy/friends.compose.yml up -d --no-build

For public HTTPS use, set FRIENDS_PUBLIC_ORIGIN to the exact HTTPS origin and FRIENDS_COOKIE_SECURE=true. Forward the original Host and set X-Real-IP from the real request address. Never publish port 8088 directly.

To create a single-use invitation:

    docker exec freebieharbor-friends python3 /app/tools/friends_admin.py invite --hours 48

For the first administrator, use the same command with --admin. Only a server administrator can create this special invite. After the administrator signs in, the Admin page can create normal member invitations and disable accounts without command-line access. The command prints a sign-up URL and a separate invite code. Send the code privately. It must never appear in a URL or public issue. Disable accounts and revoke sessions with:

    docker exec freebieharbor-friends python3 /app/tools/friends_admin.py disable username

The private user database belongs to the friends-data Docker volume. Back it up separately.

## Free-to-keep Steam licenses

The independent FreePackages plugin for ArchiSteamFarm supports per-bot free-package discovery and activation. It has filters including NoCostOnly, IgnoreFreeWeekends and PauseFreePackagesWhilePlaying. This is the mechanism already available for the owner's private Steam automation, and it need not be reimplemented.

The sample, password-free configuration fragment is produced by:

    python3 tools/asf_freepackages_profile.py

A friend can install ASF and the FreePackages plugin on their own computer or private server, confirm Steam login in their own environment and choose their own activation filters. FreebieHarbor does not host their account sessions.

Steam's Subscriber Agreement restricts automated interactions; this third-party plugin is not endorsed by Valve and may carry account-restriction risk. The central friends service only discovers offers and tracks personal states. No central automatic Steam claiming is available at this stage.

References: https://github.com/Citrinate/FreePackages and https://store.steampowered.com/agreement/

## Next priorities

Invite pilot security review; verified user notifications and recovery; optionally supported account integrations only after confirming platform permissions and secure credential isolation.

## Shared server-side ASF (experimental)

A single, separate ASF process can manage multiple friend bots without a container per friend. Each friend may link several distinct SteamIDs separately through Valve and submit a Steam login name and explicit opt-in for each. The system stages a disabled bot configuration; it does not collect a Steam password. Consult [the ASF integration lifecycle](steam-integration.md) before enabling bots.

**QR login limitation:** ASF currently does not initiate QR login in Headless mode. Our shared server is Headless; an optional interactive one-time ASF login broker is now implemented (see [Steam QR onboarding](steam-qr-onboarding.md)), but it has not passed a real Steam account test. Automatic claiming must not be described as verified.
