#!/usr/bin/env python3
"""Publish ONLY public game-offer fields in an atomic standalone SQLite snapshot."""
import argparse
import os
import sqlite3
import tempfile
from pathlib import Path

SELECTED = (
    "offer_id", "title", "source", "url", "platform",
    "offer_type", "ends_at", "provider", "first_seen",
)
ALLOWED_SOURCES = ("gamerpower","freetokeep","ggdeals","alienware")


def export_catalog(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError("Source and destination must differ")
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = sqlite3.connect(source.as_uri()+"?mode=ro",uri=True,timeout=10)
    fd, filename = tempfile.mkstemp(prefix=".friends-",suffix=".db",dir=destination.parent)
    os.close(fd)
    temp = Path(filename)
    try:
        original.execute("PRAGMA query_only=ON")
        with sqlite3.connect(temp) as clean:
            clean.execute("""
                CREATE TABLE external_offers (
                    offer_id TEXT PRIMARY KEY,title TEXT,source TEXT,url TEXT,
                    platform TEXT,offer_type TEXT,ends_at TEXT,provider TEXT,
                    first_seen TEXT, active INTEGER NOT NULL
                )""")
            query = (
                "SELECT "+",".join(SELECTED)+" FROM external_offers WHERE active=1 "
                "AND source IN ('gamerpower','freetokeep','ggdeals','alienware') "
                "ORDER BY first_seen DESC LIMIT 1000"
            )
            rows = original.execute(query).fetchall()
            clean.executemany(
                "INSERT INTO external_offers("+",".join(SELECTED)+",active) VALUES("+",".join("?" for _ in SELECTED)+",1)",
                rows,
            )
        os.chmod(temp,0o644)
        os.replace(temp,destination)
        return len(rows)
    finally:
        original.close()
        if temp.exists():
            temp.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    print("Public catalog offers exported:",export_catalog(args.source,args.output))


if __name__=="__main__":
    main()
