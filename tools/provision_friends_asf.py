#!/usr/bin/env python3
"""Synchronize opt-in Steam accounts into a shared ASF server, always disabled.

No credentials are copied. Account authentication and activation require a
separate verified onboarding process, never initiated by this provisioner.
"""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

STEAM_ID = re.compile(r"7656119[0-9]{10}$")
LOGIN = re.compile(r"[A-Za-z0-9_]{3,64}$")


def atomic_json(path, value):
    fd, tmp = tempfile.mkstemp(prefix=".fh_pending_", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _provision_locked(db_path, config_dir, apply=False, limit=5):
    config_dir = Path(config_dir).resolve()
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
        rows = db.execute("""
            SELECT a.id, a.user_id, a.steam_id, a.login, a.requested, u.disabled
            FROM steam_accounts AS a JOIN users AS u ON u.id=a.user_id
            ORDER BY a.id
        """).fetchall()
    requested = [r for r in rows if r[4] == 1 and r[5] == 0]
    if len(requested) > limit:
        raise ValueError("Pilot account capacity reached")
    # Staging takes place in a dedicated config directory, never in owner's ASF.
    report = []
    for account_id, user_id, steam_id, login, enabled_request, user_disabled in rows:
        path = config_dir / f"fh_account_{account_id}.json"
        eligible = bool(enabled_request) and not bool(user_disabled)
        if eligible and (not STEAM_ID.fullmatch(str(steam_id)) or not LOGIN.fullmatch(str(login))):
            raise ValueError("Invalid verified Steam account record")
        if not path.exists():
            if not eligible:
                continue
            settings = {
                "Enabled": False,
                "SteamLogin": login,
                "EnableFreePackages": True,
                "PauseFreePackagesWhilePlaying": True,
                "FreePackagesLimit": 15,
                "FreePackagesFilters": [{
                    "NoCostOnly": True,
                    "IgnoreFreeWeekends": True,
                    "IgnoredTypes": ["Demo"],
                    "PlaytestMode": 0,
                }],
            }
            if apply:
                config_dir.mkdir(parents=True, exist_ok=True)
                atomic_json(path, settings)
            report.append({"bot": path.stem, "status": "staged disabled; Steam authorization required"})
            continue
        # Keep existing authorized bot configs unchanged. For withdrawn requests,
        # disable them but never silently delete session or account data.
        if not eligible:
            settings = json.loads(path.read_text())
            if settings.get("Enabled"):
                settings["Enabled"] = False
                if apply:
                    atomic_json(path, settings)
                report.append({"bot": path.stem, "status": "disabled after opt-out"})
            else:
                report.append({"bot": path.stem, "status": "opted out; already disabled"})
        else:
            settings = json.loads(path.read_text())
            if settings.get("SteamLogin") != login:
                if settings.get("Enabled") is not False:
                    raise ValueError("A live bot cannot be re-bound to another Steam login")
                db_path = config_dir / f"fh_account_{account_id}.db"
                if db_path.exists():
                    raise ValueError("Old bot state must be revoked before re-linking")
                settings["SteamLogin"] = login
                if apply:
                    atomic_json(path, settings)
                report.append({"bot": path.stem, "status": "re-enrolled disabled; authorization required"})
            else:
                report.append({"bot": path.stem, "status": "present; unchanged"})
    return report


def provision(db_path, config_dir, apply=False, limit=5):
    if not apply:
        return _provision_locked(db_path, config_dir, apply=False, limit=limit)
    directory = Path(config_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".friends-qr-onboarding.lock").open("a+b") as fd:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        try:
            return _provision_locked(db_path, directory, apply=True, limit=limit)
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--friends-db", required=True)
    parser.add_argument("--asf-config", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(provision(args.friends_db, args.asf_config, args.apply, args.limit), indent=2))


if __name__ == "__main__":
    main()
