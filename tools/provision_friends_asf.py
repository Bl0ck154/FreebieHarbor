#!/usr/bin/env python3
"""Root/admin-only: stage verified Friends accounts in ONE dedicated ASF process.

No Steam password, guard token or private owner config is copied. Bots are
created disabled; Steam session authorization is a separate explicit step.
"""
import argparse
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path


def provision(db_path,config_dir,apply=False,limit=5):
    config_dir=Path(config_dir).resolve()
    conn=sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro",uri=True,timeout=5)
    try:
        rows=conn.execute("SELECT id,steam_id,steam_login FROM users WHERE disabled=0 AND steam_automation=1 AND steam_id IS NOT NULL AND steam_login IS NOT NULL ORDER BY id").fetchall()
    finally:
        conn.close()
    if len(rows)>limit:
        raise ValueError("Pilot capacity reached; refusing to provision additional accounts")
    staged=[]
    for uid,steam_id,login in rows:
        if not re.fullmatch(r"7656119[0-9]{10}",str(steam_id)) or not re.fullmatch(r"[A-Za-z0-9_]{3,64}",login):
            raise ValueError("Invalid linked account information")
        name="fh_"+str(uid)
        path=config_dir/(name+".json")
        if path.exists():
            staged.append({"bot":name,"status":"already configured; unchanged"})
            continue
        bot={
          "Enabled":False,
          "SteamLogin":login,
          "EnableFreePackages":True,
          "PauseFreePackagesWhilePlaying":True,
          "FreePackagesLimit":15,
          "FreePackagesFilters":[{"NoCostOnly":True,"IgnoreFreeWeekends":True,"IgnoredTypes":["Demo"],"PlaytestMode":0}],
        }
        if apply:
            config_dir.mkdir(parents=True,exist_ok=True)
            fd,temp=tempfile.mkstemp(prefix=".staging_",dir=config_dir)
            try:
                with os.fdopen(fd,"w") as output:
                    json.dump(bot,output,indent=2)
                    output.write("\n")
                os.chmod(temp,0o600)
                os.replace(temp,path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        staged.append({"bot":name,"status":"staged disabled; awaits Steam authorization"})
    return staged


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--friends-db",required=True)
    parser.add_argument("--asf-config",required=True)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--limit",type=int,default=5)
    args=parser.parse_args()
    print(json.dumps(provision(args.friends_db,args.asf_config,args.apply,args.limit),indent=2))

if __name__=="__main__":main()
