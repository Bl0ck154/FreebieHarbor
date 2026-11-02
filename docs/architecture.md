# Architecture

    GamerPower API ----+
    FreeToKeep API ----+---> FreebieHarbor collector ---> SQLite ---> Local web dashboard
    GG.deals RSS ------+
    Alienware Arena ---+
    Reddit publisher (optional) - - - - - - - - - - - ^

    SteamSelfGifter SQLite (optional, read-only) ------> Reward stats

The core is a Python HTTP server plus a periodic polling thread. Sources are normalized and stored by external offer ID. There are no token-consuming AI calls or automated claims in the public edition.

SteamSelfGifter, ArchiSteamFarm, and FreePackages are separate applications. They are not started by this Compose file.

## SteamGifts data adapter

The optional adapter reads counts from a single SQLite file. The safest way to
integrate an existing SteamSelfGifter installation is to provide a consistent
snapshot, not a live WAL file or the entire configuration folder. Python
supports SQLite's online backup API, which can create a consistent snapshot
without copying an open database file blindly.

With an existing database at a path you control, make a snapshot:

    python3 -c 'import sqlite3; src=sqlite3.connect("file:/path/to/steamselfgifter.db?mode=ro", uri=True); dst=sqlite3.connect("/path/to/steamgifts-snapshot.db"); src.backup(dst); dst.close(); src.close()'

Keep the snapshot readable only by the host account running Docker. Then:

    STEAMGIFTS_DB_SOURCE=/path/to/steamgifts-snapshot.db docker compose -f compose.yml -f compose.steamgifts.yml up -d

Snapshots are static. Refresh them yourself if you want updated counts. Neither
the application nor its Compose overlay modifies the source database.
