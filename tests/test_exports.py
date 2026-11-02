import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rewards_exporter", ROOT / "tools/export_rewards.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class SanitizedExportsTests(unittest.TestCase):
    def test_steam_snapshot_contains_only_required_columns(self):
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            source, output = base / "source.db", base / "export.db"
            with sqlite3.connect(source) as conn:
                conn.executescript("""
                    CREATE TABLE entries(status TEXT, secret_cookie TEXT);
                    CREATE TABLE giveaways(code TEXT,game_name TEXT,is_won INTEGER,won_at TEXT, secret_token TEXT);
                    CREATE TABLE private_settings(api_key TEXT);
                    INSERT INTO entries VALUES('success','PRIVATE_COOKIE_VALUE');
                    INSERT INTO giveaways VALUES('abc12','Fictional Game',1,'2026-11-02','PRIVATE_TOKEN_VALUE');
                    INSERT INTO private_settings VALUES('PRIVATE_SECRET_VALUE');
                """)
            exporter.export_steamgifts(source, output)
            with sqlite3.connect(output) as conn:
                tables = [row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )]
                self.assertEqual(tables, ["entries", "giveaways"])
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE status='success'"
                ).fetchone()[0], 1)
                self.assertEqual(conn.execute(
                    "SELECT code,game_name FROM giveaways WHERE is_won=1"
                ).fetchone(), ("abc12", "Fictional Game"))
            exported_bytes = output.read_bytes()
            for secret in (b"PRIVATE_COOKIE_VALUE", b"PRIVATE_TOKEN_VALUE", b"PRIVATE_SECRET_VALUE"):
                self.assertNotIn(secret, exported_bytes)
            with sqlite3.connect(source) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM private_settings").fetchone()[0], 1)

    def test_asf_snapshot_keeps_only_aggregate_counts(self):
        with tempfile.TemporaryDirectory() as dirname:
            base = Path(dirname)
            source, output = base / "source.json", base / "export.json"
            source.write_text(json.dumps({
                "SeenPackages": [10, 20, 30],
                "NewOwnedPackages": [40],
                "Token": "PRIVATE_SECRET_VALUE",
                "OwnerID": 123,
            }))
            exporter.export_asf(source, output)
            self.assertEqual(json.loads(output.read_text()), {
                "schema_version": 1,
                "seen_packages_count": 3,
                "new_owned_queue_count": 1,
            })
            self.assertNotIn("PRIVATE_SECRET_VALUE", output.read_text())

    def test_source_and_destination_must_differ(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "data.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                exporter.export_asf(path, path)


if __name__ == "__main__":
    unittest.main()
