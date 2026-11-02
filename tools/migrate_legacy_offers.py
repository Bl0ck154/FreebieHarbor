#!/usr/bin/env python3
"""Import historical public offers from a legacy Giveaway Inbox to FreebieHarbor.

Read-only source; dry-run by default. Import only compatible public providers,
without private Reddit data, IndieGala entries, cookies or raw JSON payloads.
Existing target rows retain their live state and timestamps.
"""
import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

SOURCES = ("gamerpower", "freetokeep", "ggdeals")
COLUMNS = (
    "offer_id", "source", "title", "url", "image_url", "offer_type", "platform",
    "provider", "description", "instructions", "original_price", "published_at",
    "starts_at", "ends_at", "active", "user_state", "first_seen", "last_seen",
    "raw_json",
)


def valid_public_url(value):
    try:
        parsed = urlsplit(str(value or ""))
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not (
            parsed.username or parsed.password
        )
    except ValueError:
        return False


def migrate(source, target, *, apply=False):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError("Source and target must differ")
    if not source.is_file() or not target.is_file():
        raise FileNotFoundError("Both databases must exist")
    imported = duplicates = skipped = preserved_states = 0
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=15)
    dst = sqlite3.connect(target, timeout=20)
    try:
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        expected = {c for c in COLUMNS}
        for conn in (src, dst):
            cols = {row[1] for row in conn.execute("PRAGMA table_info(external_offers)")}
            if not expected <= cols:
                raise ValueError("Incompatible external_offers schema")
        # Collect only narrow offer fields, never data from Reddit/AI/settings tables.
        rows = src.execute(
            "SELECT " + ",".join(COLUMNS[:-1]) +
            " FROM external_offers WHERE source IN ('gamerpower','freetokeep','ggdeals')"
        )
        existing = {row[0]:row[1] for row in dst.execute(
            "SELECT offer_id,user_state FROM external_offers"
        )}
        staged = []
        for row in rows:
            offer_id, provider, title, url = (row[x] for x in ("offer_id","source","title","url"))
            if not offer_id or not title or not valid_public_url(url):
                skipped += 1
                continue
            state = row["user_state"] if row["user_state"] in ("new","claimed","ignored") else "new"
            if offer_id in existing:
                duplicates += 1
                if state != "new" and existing[offer_id] == "new":
                    preserved_states += 1
                    staged.append(("state", offer_id, state))
                continue
            archive_row = []
            for field in COLUMNS:
                if field == "active":
                    archive_row.append(0)
                elif field == "user_state":
                    archive_row.append(state)
                elif field == "raw_json":
                    archive_row.append("{}")
                elif field == "image_url":
                    archive_row.append(row[field] if valid_public_url(row[field]) else "")
                else:
                    archive_row.append(row[field])
            staged.append(("insert", archive_row))
            imported += 1
        if apply:
            dst.execute("BEGIN IMMEDIATE")
            try:
                for item in staged:
                    if item[0] == "insert":
                        dst.execute(
                            "INSERT OR IGNORE INTO external_offers(" + ",".join(COLUMNS) +
                            ") VALUES(" + ",".join("?" for _ in COLUMNS) + ")", item[1]
                        )
                    else:
                        dst.execute(
                            "UPDATE external_offers SET user_state=? WHERE offer_id=? AND user_state='new'",
                            (item[2], item[1])
                        )
                dst.commit()
            except Exception:
                dst.rollback()
                raise
        return {
            "mode": "applied" if apply else "dry-run",
            "archived_rows": imported,
            "already_present": duplicates,
            "preserved_user_states": preserved_states,
            "skipped_invalid": skipped,
            "sources": list(SOURCES),
        }
    finally:
        src.close()
        dst.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply after backing up the target")
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target, apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
