"""Validate one verified, opted-in Steam account and its disabled ASF bot."""
import json
import re
import sqlite3
from pathlib import Path

STEAM_ID = re.compile(r"7656119[0-9]{10}$")
LOGIN = re.compile(r"[A-Za-z0-9_]{3,64}$")


def context(friends_db, config_dir, account_id):
    if not isinstance(account_id, int) or account_id <= 0:
        raise ValueError("Invalid Steam account ID")
    with sqlite3.connect(Path(friends_db).resolve().as_uri()+"?mode=ro",uri=True,timeout=4) as db:
        row=db.execute("""
            SELECT a.steam_id,a.login,a.requested,u.disabled
            FROM steam_accounts a JOIN users u ON u.id=a.user_id
            WHERE a.id=?
        """,(account_id,)).fetchone()
    if not row or not row[2] or row[3]:
        raise ValueError("Unverified, disabled, or non-consenting Steam account")
    steam_id,login,_,_=row
    if not STEAM_ID.fullmatch(str(steam_id)) or not LOGIN.fullmatch(str(login or "")):
        raise ValueError("Verified SteamID or login missing")
    bot=f"fh_account_{account_id}"
    folder=Path(config_dir).resolve()
    cfg=folder/(bot+".json")
    dbfile=folder/(bot+".db")
    if cfg.is_symlink() or dbfile.is_symlink() or not cfg.is_file():
        raise ValueError("Disabled ASF bot is not provisioned")
    settings=json.loads(cfg.read_text())
    if settings.get("Enabled") is not False or settings.get("SteamLogin")!=login:
        raise ValueError("Bot must be disabled and match opted-in Steam login")
    if dbfile.exists():
        if dbfile.stat().st_size>4_000_000:
            raise ValueError("Unexpected ASF bot database size")
        state=json.loads(dbfile.read_text())
        if state.get("BackingRefreshToken"):
            raise ValueError("This Steam account already has an ASF session")
    return bot,steam_id,login,cfg,dbfile
