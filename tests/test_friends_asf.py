import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provision", ROOT / "tools/provision_friends_asf.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionTests(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory)/"users.db"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,disabled INTEGER)")
            db.execute("CREATE TABLE steam_accounts(id INTEGER PRIMARY KEY,user_id INTEGER,steam_id TEXT,login TEXT,requested INTEGER)")
            db.executemany("INSERT INTO users VALUES(?,?)", [(1,0),(2,0)])
            db.executemany("INSERT INTO steam_accounts VALUES(?,?,?,?,?)", [
                (101,1,"76561198000000001","friend_first",1),
                (102,1,"76561198000000002","friend_second",1),
                (103,2,"76561198000000003","other_friend",1),
            ])
        return path

    def test_multiple_accounts_share_process_but_have_separate_disabled_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            db = self.fixture(directory)
            target = Path(directory)/"config"
            self.assertEqual(len(provision.provision(db,target)),3)
            self.assertFalse(target.exists())
            self.assertEqual(len(provision.provision(db,target,apply=True)),3)
            files = sorted(target.glob("fh_account_*.json"))
            self.assertEqual(len(files),3)
            self.assertEqual([f.stem for f in files], ["fh_account_101","fh_account_102","fh_account_103"])
            settings = [json.loads(f.read_text()) for f in files]
            self.assertTrue(all(not cfg["Enabled"] for cfg in settings))
            self.assertTrue(all(cfg["FreePackagesFilters"][0]["NoCostOnly"] for cfg in settings))
            self.assertTrue(all("SteamPassword" not in cfg for cfg in settings))
            self.assertTrue(all("SteamGuard" not in cfg for cfg in settings))
            self.assertTrue(all(entry["status"] == "present; unchanged"
                                for entry in provision.provision(db,target,apply=True)))

    def test_withdrawal_disables_previously_enabled_bot_without_other_bot_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            db=self.fixture(directory)
            target=Path(directory)/"config"
            provision.provision(db,target,apply=True)
            first=target/"fh_account_101.json"
            second=target/"fh_account_102.json"
            first_data=json.loads(first.read_text())
            first_data["Enabled"]=True
            first_data["SteamPassword"]="do-not-lose-existing-session-data"
            first.write_text(json.dumps(first_data))
            original_second=second.read_bytes()
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE steam_accounts SET requested=0 WHERE id=101")
            self.assertIn("disabled after opt-out", [r["status"] for r in provision.provision(db,target,apply=True)])
            result=json.loads(first.read_text())
            self.assertFalse(result["Enabled"])
            self.assertEqual(result["SteamPassword"],"do-not-lose-existing-session-data")
            self.assertEqual(second.read_bytes(), original_second)

    def test_total_bot_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            db=self.fixture(directory)
            with self.assertRaises(ValueError):
                provision.provision(db, Path(directory)/"config", limit=2)
