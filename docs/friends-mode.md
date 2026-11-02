# Friends mode: private multi-user tracking

**Status:** proposed design, not implemented. Start with a small, invited group before considering public registration.

## What a friend can do

1. Accept a one-time invitation and create their own FreebieHarbor profile.
2. See the same public game offers collected once by the shared server.
3. Save personal filters, wishlists, and claimed/ignored statuses independently.
4. Receive optional notifications for matching new offers.
5. Open the source site to claim or enter manually using their own account.

This supports automatically finding and notifying users about public offers. It does **not** automate SteamGifts entries, Steam store transactions, Steam account creation, key redemption or gameplay rewards.

## Why auto-entry must not be part of the hosted service

SteamGifts rules explicitly prohibit scripts that automatically enter giveaways, including entry while the user is away. The rules also limit each person to one account and prohibit account sharing. Steam's Subscriber Agreement restricts unauthorized automation. Hosting another person's login sessions and auto-entering for them creates account, security and platform-compliance risks.

Read the official rules before adding a provider:
- https://www.steamgifts.com/about/guidelines
- https://store.steampowered.com/agreement/

## Implementation stages

### Stage 1 — accounts and data isolation

- Invite-only registration, no open signups; owner can revoke access.
- One user per session; secure password hashing or established external identity provider, protected cookies (HttpOnly, Secure, SameSite) and CSRF tokens.
- User-owned tables for subscriptions, offer states, alerts and optional linked identities.
- Database ownership checks on every query, export and action.
- Global `external_offers` and source polling remain shared and read-only to all users.
- Per-user rate limits, secure invite expiry, minimal audit logs, backups and account deletion.
- No stored Steam, SteamGifts or storefront passwords, cookies or access tokens.

### Stage 2 — notifications

- Per-user opt-in Telegram destination or email address after verification.
- Each user's own filters, quiet hours, and notification history.
- Queue and deduplicate notifications; never send one user's data to another user.
- One provider poll serves all users. Never fan out account logins to upstream sites.

### Stage 3 — small invite-only pilot

- Up to 3–5 trusted friends, separate profiles, no automatic game entries.
- Test revoked users, session expiry, simultaneous edits, migrations and accidental cross-user data exposure.
- Keep old Steam Farm/owner credentials entirely isolated from the new multi-user service.

## Deployment boundary

The existing FreebieHarbor app is an *unauthenticated single-owner local dashboard*, protected externally by reverse proxy Basic Auth. It is **not suitable for opening to public registrations today**. Build and test friends mode in a separate service and database. Keep the current proxy protected until individual-user login, data isolation and security tests have been reviewed.

## Optional compliant future integrations

Only use provider operations explicitly allowed by the platform and supported by its official API/auth mechanisms. Any feature that submits a giveaway entry or claims a Steam license must be reviewed against current platform rules before implementation.
