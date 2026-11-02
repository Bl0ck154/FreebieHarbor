# Contributing

FreebieHarbor is deliberately small: one standard-library Python application, local SQLite, a Docker image and optional source adapters. You do not need Steam credentials or external accounts to run the tests.

## Make a change

1. Open an issue explaining the defect or use case. For new sources, include official API documentation and any authentication/rate limits.
2. Work in a short branch and submit a pull request against main.
3. Run the regression suite before opening the PR:

       python3 -m unittest discover -s tests -v
       python3 -m py_compile app/hub.py
       docker compose config -q

4. Add mocked tests for your changes. Do not use personal sessions, real giveaway entries or network requests in unit tests.
5. Update the README and CHANGELOG when a user-visible feature or setting changes.

GitHub Actions runs the Python suite automatically. Maintainers can also run the on-demand Docker smoke workflow, which builds the image away from production. No release is published directly from a contributor branch.

## Scope

The core can collect **publicly accessible** offers, remember local state, and read user-mounted snapshots. It does not log in to Steam, enter restricted contests, scrape sites without permission or claim game keys automatically. Keep external applications in their upstream repositories and submit useful fixes there as independent PRs.

Respect copyright and licensing. The original code is MIT; linked third-party tools retain their own licenses. Never commit credentials, tokens, private hosts or production databases.

## Reports and discussions

Use the [bug template](https://github.com/Bl0ck154/FreebieHarbor/issues/new/choose) for reproducible failures and [Discussions](https://github.com/Bl0ck154/FreebieHarbor/discussions) for questions. Report security issues without disclosing private information publicly. See [SECURITY.md](SECURITY.md).
