#!/usr/bin/env python3
"""Export narrow read-only FreebieHarbor snapshots without account credentials.

The source files are *read only*. The destination contains only the fields
needed by FreebieHarbor. Never export the original ASF account database.
"""
import argparse
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path


def _temporary_file(directory, suffix):
    fd, path = tempfile.mkstemp(prefix=".freebieharbor-", suffix=suffix, dir=directory)
    os.close(fd)
    return Path(path)


def export_steamgifts(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError("Source and destination must differ")
    source_db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10)
    temp = _temporary_file(output.parent, ".db")
    try:
        source_db.execute("PRAGMA query_only=ON")
        with sqlite3.connect(temp) as cleaned:
            cleaned.executescript("""
                CREATE TABLE entries(status TEXT NOT NULL);
                CREATE TABLE giveaways(code TEXT,game_name TEXT,is_won INTEGER,won_at TEXT);
            """)
            cleaned.executemany(
                "INSERT INTO entries(status) VALUES(?)",
                ((row[0] if row[0] in {"success", "failed"} else "other",)
                 for row in source_db.execute("SELECT status FROM entries")),
            )
            giveaway_rows = [
                (
                    str(code or "") if re.fullmatch(r"[A-Za-z0-9]{5}", str(code or "")) else "",
                    str(name or "")[:160],
                    1 if bool(won) else 0,
                    str(timestamp or "")[:40],
                )
                for code, name, won, timestamp in source_db.execute(
                    "SELECT code,game_name,is_won,won_at FROM giveaways"
                )
            ]
            cleaned.executemany("INSERT INTO giveaways VALUES(?,?,?,?)", giveaway_rows)
        os.chmod(temp, 0o644)
        os.replace(temp, output)
    finally:
        source_db.close()
        if temp.exists():
            temp.unlink()


def export_asf(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError("Source and destination must differ")
    if source.stat().st_size > 4_000_000:
        raise ValueError("ASF source is unexpectedly large")
    data = json.loads(source.read_text(encoding="utf-8"))
    seen = data.get("SeenPackages")
    owned = data.get("NewOwnedPackages")
    if not isinstance(seen, list) or not isinstance(owned, list):
        raise ValueError("ASF FreePackages metadata is missing")
    clean = {
        "schema_version": 1,
        "seen_packages_count": len(seen),
        "new_owned_queue_count": len(owned),
    }
    temp = _temporary_file(output.parent, ".json")
    try:
        temp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
        os.chmod(temp, 0o644)
        os.replace(temp, output)
    finally:
        if temp.exists():
            temp.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steamgifts-source", type=Path)
    parser.add_argument("--asf-source", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.steamgifts_source and not args.asf_source:
        parser.error("Specify at least one read-only source")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.steamgifts_source:
        export_steamgifts(args.steamgifts_source, args.output_dir / "steamgifts-snapshot.db")
        print("SteamGifts summary updated")
    if args.asf_source:
        export_asf(args.asf_source, args.output_dir / "asf-freepackages.json")
        print("ASF package counters updated")


if __name__ == "__main__":
    main()
