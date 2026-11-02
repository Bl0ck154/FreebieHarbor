#!/usr/bin/env python3
"""Local-only Friends administrator commands. Does not expose an admin web API."""
import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("friends_portal", ROOT/"friends/portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    invite = sub.add_parser("invite",help="Create a single-use invite")
    invite.add_argument("--hours",type=int,default=48)
    sub.add_parser("list-users",help="Show usernames and account states")
    disable = sub.add_parser("disable",help="Disable one profile and revoke sessions")
    disable.add_argument("username")
    args = parser.parse_args()
    portal.init_db()
    if args.command == "invite":
        token = portal.create_invite(args.hours)
        # One-time secret printed only to the admin terminal, never logged by HTTP server.
        print("Sign-up page:", portal.PUBLIC_ORIGIN + "/signup")
        print("One-time invitation code:", token)
    if args.command == "list-users":
        with portal.database() as db:
            for row in db.execute("SELECT username,disabled FROM users ORDER BY username"):
                print(row["username"],"disabled" if row["disabled"] else "enabled")
    if args.command == "disable":
        with portal.LOCK, portal.database() as db:
            row = db.execute("SELECT id FROM users WHERE username=?", (args.username.lower(),)).fetchone()
            if not row:
                raise SystemExit("No such user")
            db.execute("UPDATE users SET disabled=1 WHERE id=?", (row["id"],))
            db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        print("Profile disabled and all sessions revoked")


if __name__=="__main__":
    main()
