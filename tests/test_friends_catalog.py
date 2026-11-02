import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("public_export",ROOT/"tools/export_friends_catalog.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class CatalogTests(unittest.TestCase):
    def test_sanitized_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            src,dst=Path(d)/"source.db",Path(d)/"public"/"giveaways.db"
            with sqlite3.connect(src) as conn:
                conn.execute("CREATE TABLE external_offers(offer_id TEXT,title TEXT,source TEXT,url TEXT,platform TEXT,offer_type TEXT,ends_at TEXT,provider TEXT,first_seen TEXT,active INTEGER,user_state TEXT,raw_json TEXT)")
                for name,source,active in (("Game","freetokeep",1),("Hidden","reddit",1),("Old","ggdeals",0)):
                    conn.execute("INSERT INTO external_offers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(name,name,source,"https://example.org", "PC","Free", "","Steam","2026-11-02",active,"claimed", "PRIVATE_OWNER_SECRET"))
            self.assertEqual(mod.export_catalog(src,dst),1)
            with sqlite3.connect(dst) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM external_offers").fetchone()[0],1)
                columns=[r[1] for r in conn.execute("PRAGMA table_info(external_offers)")]
                self.assertNotIn("raw_json",columns)
                self.assertNotIn("user_state",columns)
                self.assertEqual(conn.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertNotIn(b"PRIVATE_OWNER_SECRET",dst.read_bytes())
            self.assertEqual(mod.export_catalog(src,dst),1)
