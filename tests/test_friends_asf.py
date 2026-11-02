import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('provision', ROOT/'tools/provision_friends_asf.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)

class ProvisionTests(unittest.TestCase):
    def test_verified_bot_is_disabled_and_without_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            db,config=root/'users.db',root/'config'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE users(id INTEGER,steam_id TEXT,steam_login TEXT,steam_automation INTEGER,disabled INTEGER)')
                c.execute('INSERT INTO users VALUES(1,?,?,1,0)',('76561198000000001','verified_friend'))
                c.execute('INSERT INTO users VALUES(2,?,?,1,1)',('76561198000000002','disabled_friend'))
            self.assertEqual(len(provision.provision(db,config)),1)
            self.assertFalse(config.exists())
            self.assertEqual(len(provision.provision(db,config,apply=True)),1)
            files=list(config.glob('*.json'))
            self.assertEqual(len(files),1)
            data=json.loads(files[0].read_text())
            self.assertFalse(data['Enabled'])
            self.assertTrue(data['EnableFreePackages'])
            self.assertTrue(data['FreePackagesFilters'][0]['NoCostOnly'])
            self.assertNotIn('SteamPassword',data)
            self.assertEqual(provision.provision(db,config,apply=True)[0]['status'],'already configured; unchanged')
    def test_limit_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d)
            with sqlite3.connect(base/'users.db') as c:
                c.execute('CREATE TABLE users(id INTEGER,steam_id TEXT,steam_login TEXT,steam_automation INTEGER,disabled INTEGER)')
                for i in range(1,4):c.execute('INSERT INTO users VALUES(?,?,?,?,?)',(i,'76561198000000001','friend',1,0))
            with self.assertRaises(ValueError):provision.provision(base/'users.db',base/'config',limit=2)
