# Changelog

All notable public changes are documented here. Release notes on GitHub describe exact commits.

## Unreleased

No unreleased changes yet.

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
