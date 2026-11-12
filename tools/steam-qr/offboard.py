#!/usr/bin/env python3
"""Remove local ASF session state for a disabled or opted-out Friends account.

Only fh_account_<id> bots in the separate Friends ASF may be touched. A
temporary stop of Friends ASF is required before deleting persistent sessions.
"""
import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

BOT_RE = re.compile(r"fh_account_([1-9][0-9]*)\.json$")


def offboarding_plan(friends_db, config_dir):
    with sqlite3.connect(Path(friends_db).resolve().as_uri()+"?mode=ro",uri=True) as conn:
        rows = conn.execute("""
            SELECT a.id, a.requested, u.disabled
            FROM steam_accounts a JOIN users u ON u.id=a.user_id
        """).fetchall()
    active = {int(uid) for uid, requested, disabled in rows if requested and not disabled}
    configs = Path(config_dir).resolve()
    stale = []
    for filename in configs.glob("fh_account_*.json"):
        m = BOT_RE.fullmatch(filename.name)
        if not m or filename.is_symlink():
            continue
        uid = int(m.group(1))
        if uid not in active:
            stale.append((uid, filename))
    return sorted(stale)


def purge_offboarded(friends_db, config_dir, *, apply=False, runner=None):
    config_dir = Path(config_dir).resolve()
    stale = offboarding_plan(friends_db, config_dir)
    if not stale:
        return []
    report = [f"fh_account_{uid}" for uid,_ in stale]
    if not apply:
        return report
    if os.geteuid() != 0:
        raise PermissionError("Only a server administrator may purge sessions")
    run = runner or (lambda *args: subprocess.check_call(["docker",*args], stdout=subprocess.DEVNULL))
    lock_path = config_dir/".friends-qr-onboarding.lock"
    with lock_path.open("a+b") as fd:
        fcntl.flock(fd.fileno(),fcntl.LOCK_EX)
        # Re-evaluate while holding the lock; never purge a freshly re-enabled bot.
        stale = offboarding_plan(friends_db,config_dir)
        if not stale:
            return []
        snapshot = {}
        for uid, config in stale:
            prefix = f"fh_account_{uid}"
            for ext in (".json",".db",".bin"):
                path=config_dir/(prefix+ext)
                if path.is_symlink():
                    raise ValueError("Refusing symlink in ASF bot files")
                if path.exists():
                    snapshot[path]=path.read_bytes()
        # Offboard individual accounts, never the owner's Steam Farm.
        run("stop","-t","20","freebieharbor-friends-asf")
        try:
            for uid, config in stale:
                prefix=f"fh_account_{uid}"
                for ext in (".db",".bin"):
                    path=config_dir/(prefix+ext)
                    if path.exists():
                        path.unlink()
                state=json.loads(config.read_text())
                state["Enabled"]=False
                # Remove account credentials but retain harmless config metadata.
                for field in ("SteamPassword","SteamLogin","SteamParentalCode"):
                    state.pop(field,None)
                tmp=config_dir/(".fh-purge-"+prefix+".tmp")
                tmp.write_text(json.dumps(state,indent=2)+"\n")
                tmp.chmod(0o600)
                tmp.replace(config)
            run("start","freebieharbor-friends-asf")
        except BaseException:
            for path,contents in snapshot.items():
                path.write_bytes(contents)
                path.chmod(0o600)
            run("start","freebieharbor-friends-asf")
            raise
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--friends-db",required=True)
    parser.add_argument("--asf-config",required=True)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    print("Accounts requiring local session purge:", purge_offboarded(
        args.friends_db,args.asf_config,apply=args.apply))


if __name__=="__main__":
    main()
