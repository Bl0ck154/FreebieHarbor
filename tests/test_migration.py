import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("legacy_import", ROOT / "tools/migrate_legacy_offers.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

COLUMNS = "offer_id TEXT PRIMARY KEY,source TEXT,title TEXT,url TEXT,image_url TEXT,offer_type TEXT,platform TEXT,provider TEXT,description TEXT,instructions TEXT,original_price TEXT,published_at TEXT,starts_at TEXT,ends_at TEXT,active INTEGER,user_state TEXT,first_seen TEXT,last_seen TEXT,raw_json TEXT"


class MigrationTests(unittest.TestCase):
    def test_dry_run_archives_only_supported_sources_without_raw_json(self):
        with tempfile.TemporaryDirectory() as dirname:
            source, target = Path(dirname)/"old.db", Path(dirname)/"new.db"
            for path in (source, target):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE external_offers (" + COLUMNS + ")")
            with sqlite3.connect(source) as conn:
                for ident, origin, state in (
                    ("gamerpower:1","gamerpower","claimed"),
                    ("reddit:1","reddit","new"),
                    ("indiegala:1","indiegala","new"),
                    ("ggdeals:2","ggdeals","new"),
                ):
                    conn.execute(
                        "INSERT INTO external_offers(offer_id,source,title,url,active,user_state,first_seen,last_seen,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (ident,origin,"Fictional game","https://example.test/game",1,state,"2026-10-01","2026-10-01",'{"token":"PRIVATE_SECRET"}'),
                    )
            report = module.migrate(source,target)
            self.assertEqual(report["archived_rows"],2)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM external_offers").fetchone()[0],0)
            report = module.migrate(source,target,apply=True)
            self.assertEqual(report["archived_rows"],2)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM external_offers").fetchone()[0],2)
                self.assertEqual(conn.execute("SELECT count(*) FROM external_offers WHERE active=0").fetchone()[0],2)
                self.assertEqual(conn.execute("SELECT user_state FROM external_offers WHERE offer_id='gamerpower:1'").fetchone()[0],"claimed")
                self.assertEqual(conn.execute("SELECT raw_json FROM external_offers LIMIT 1").fetchone()[0],"{}")
            self.assertNotIn(b"PRIVATE_SECRET",target.read_bytes())
            self.assertEqual(module.migrate(source,target,apply=True)["archived_rows"],0)

    def test_existing_live_offer_and_claim_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as dirname:
            source,target = Path(dirname)/"old.db",Path(dirname)/"new.db"
            for path in (source,target):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE external_offers ("+COLUMNS+")")
            with sqlite3.connect(source) as conn:
                conn.execute(
                    "INSERT INTO external_offers(offer_id,source,title,url,active,user_state,first_seen,last_seen,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("same","gamerpower","Old name","https://example.test/old",0,"claimed","old","old","{}"),
                )
            with sqlite3.connect(target) as conn:
                conn.execute(
                    "INSERT INTO external_offers(offer_id,source,title,url,active,user_state,first_seen,last_seen,raw_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    ("same","gamerpower","Current name","https://example.test/new",1,"new","new","new","{}"),
                )
            self.assertEqual(module.migrate(source,target,apply=True)["preserved_user_states"],1)
            with sqlite3.connect(target) as conn:
                self.assertEqual(conn.execute(
                    "SELECT title,active,user_state FROM external_offers WHERE offer_id='same'"
                ).fetchone(),("Current name",1,"claimed"))


if __name__ == "__main__":
    unittest.main()

class ArchiveViewTests(unittest.TestCase):
    def test_historical_offers_are_filterable_without_affecting_active(self):
        import importlib.util
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            os.environ['DATA_DIR'] = directory
            spec = importlib.util.spec_from_file_location('freebieharbor_history',ROOT/'app/hub.py')
            app = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(app)
            app.init_db()
            with app.db() as db:
                for ident,active in (('archive:1',0),('current:1',1)):
                    db.execute('INSERT INTO external_offers(offer_id,source,title,url,first_seen,last_seen,active) VALUES(?,?,?,?,?,?,?)', (ident,'gamerpower','Demo game','https://example.org/game','2026-10-01','2026-10-01',active))
            live = app.external_offers_data(view='active')
            archive = app.external_offers_data(view='history')
            self.assertEqual(live[1],1)
            self.assertEqual(archive[1],1)
            self.assertIn('History',app.render_offers_content({'view':['history']}))
