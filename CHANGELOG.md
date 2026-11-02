# Changelog

All notable public changes are documented here. Release notes on GitHub describe exact commits.

## Unreleased

No unreleased changes yet.

## v0.6.0 — 2026-11-02

- Add official Steam OpenID identity verification for invited users; verify the assertion with Steam, validate return-to/origin, bind it to the initiating user's session, prevent nonce replay and store only SteamID64.
- Add per-user explicit opt-in requests for one shared ASF-based free-to-keep pilot. No Steam passwords, Guard codes or cookies are collected by FreebieHarbor.
- Add a conservative provisioner that stages only disabled, per-user FreePackages bot configs, with five-member pilot cap.
- Add a separate, resource-capped Friends ASF Compose definition with localhost authenticated IPC and officially versioned ASF/FreePackages components. Steam bot login and account-level license activation are not automatically enabled by this release.
- Document credential custody, account verification, revocation, update and resource costs.

## v0.5.1 — 2026-11-02

- Add an administrator-only web page for one-time invitations and member disable actions.
- Administrator role can only be bootstrapped from the server CLI; invitations made in the web interface create regular users.
- Provide a member Steam setup page explaining independently managed ASF/FreePackages and account risks.

## v0.5.0 — 2026-11-02

- Add a separate invitation-only Friends portal with independent user profiles, sessions and offer states.
- Read one shared public catalog while storing account states in a separate SQLite volume.
- Add per-bot FreePackages configuration generator; Steam credentials and claiming remain outside the shared portal.
- Keep the owner's SteamGifts, IndieGala and ASF deployment unchanged.

## v0.4.3 — 2026-11-02

- Add a History filter for archived game offers while keeping current offers easy to find.
- Add opt-in, dry-run-first migration of legacy GamerPower, FreeToKeep and GG.deals history.
- Preserve existing current offers and user claim choices; never import Reddit, IndieGala, credentials or raw source payloads.
- Document a separate, authenticated HTTPS route for existing deployments.

## v0.4.2 — 2026-11-02

- Add an optional, credential-free rewards exporter for existing SteamSelfGifter and ASF installations.
- Produce narrow read-only SQLite/JSON snapshots using atomic file replacement; never copy account-session files or ASF package IDs.
- Allow the isolated server canary to read these sanitized snapshots while retaining separate storage and disabled notifications.

## v0.4.1 — 2026-11-02

- Manual Refresh now returns immediately while public sources update in a separate thread.
- Dashboard and health response expose refresh state; dashboard footer shows the running version.
- Added an isolated server-canary Compose profile using the version-pinned GHCR image, without touching a legacy Steam Farm.
- Documented the difference between upstream GitHub releases and manually upgraded server containers, including an explicit rollback procedure.

## v0.4.0 — 2026-11-02

- Retired the experimental Reddit source, routes and UI, and stopped advertising it.
- Rebuilt README as a user-facing landing page, bringing the dashboard screenshot and setup instructions to the top.
- Added an accessible, crawlable GitHub Pages product site with canonical metadata, Open Graph and SoftwareApplication structured data.
- Refined discovery-oriented descriptions, documentation and feature positioning.
- Preserved public giveaway sources, local claim tracking, Telegram alerts and read-only SteamGifts/ASF adapters.

## v0.3.1 — 2026-11-02

- Public GHCR image publishing for stable release tags, with source version checks.
- Documented prebuilt image installation and persistent Docker volume upgrades.
- Linux amd64 prebuilt image; local Compose builds remain available for other supported architectures.

## v0.3.0 — 2026-11-02

- Optional read-only ASF FreePackages counters and SteamGifts win history.
- Opt-in win notifications with silent initial baseline and duplicate suppression.
- Source status displays provider failures separately from offer counts.
- Version reported by /health; aligned release tag and Docker image metadata.
- Public issue templates, support documentation and repository cleanup.

## v0.2.1-preview — 2026-11-02

- Docker-tested self-hosted dashboard with optional Telegram new-offer alerts.
- Safe local-only defaults, origin checks and URL validation.
- Read-only SteamGifts database snapshot overlay.
- Offline fictional demo and genuine dashboard screenshot.
- Automated Python tests and on-demand Docker/image preview checks.

## v0.1.0-preview — 2026-11-02

- First standalone FreebieHarbor extraction from the author's private Steam Farm stack.
- Public giveaway sources, local SQLite data, search/filter dashboard.
- No bundled third-party bots or AI service calls.
