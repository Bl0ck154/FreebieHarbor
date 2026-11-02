# Architecture

    GamerPower API ----+
    FreeToKeep API ----+---> FreebieHarbor collector ---> SQLite ---> Local web dashboard
    GG.deals RSS ------+
    Alienware Arena ---+
    Reddit publisher (optional) - - - - - - - - - - - ^

    SteamSelfGifter SQLite (optional, read-only) ------> Reward stats

The core is a Python HTTP server plus a periodic polling thread. Sources are normalized and stored by external offer ID. There are no token-consuming AI calls or automated claims in the public edition.

SteamSelfGifter, ArchiSteamFarm, and FreePackages are separate applications. They are not started by this Compose file.
