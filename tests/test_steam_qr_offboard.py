import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qr_offboard',ROOT/'tools/steam-qr/offboard.py')
off=importlib.util.module_from_spec(spec);spec.loader.exec_module(off)


class OffboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.db=root/'users.db'
        self.config=root/'config'
        self.config.mkdir()
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,disabled INTEGER)')
            c.execute('CREATE TABLE steam_accounts(id INTEGER PRIMARY KEY,user_id INTEGER,requested INTEGER)')
            c.executemany('INSERT INTO users VALUES(?,?)',[(1,0),(2,0)])
            c.executemany('INSERT INTO steam_accounts VALUES(?,?,?)',[(10,1,0),(11,1,1),(12,2,1)])
        for i in (10,11,12):
            (self.config/f'fh_account_{i}.json').write_text(json.dumps({'Enabled':True,'SteamLogin':'demo','EnableFreePackages':True}))
            (self.config/f'fh_account_{i}.db').write_text(json.dumps({'BackingRefreshToken':'SYNTHETIC_TEST_ONLY'}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_opted_out_bot_cleared(self):
        before11=(self.config/'fh_account_11.db').read_bytes()
        before12=(self.config/'fh_account_12.db').read_bytes()
        events=[]
        with patch.object(off.os,'geteuid',return_value=0):
            result=off.purge_offboarded(self.db,self.config,apply=True,runner=lambda *a:events.append(a))
        self.assertEqual(result,['fh_account_10'])
        self.assertFalse((self.config/'fh_account_10.db').exists())
        conf=json.loads((self.config/'fh_account_10.json').read_text())
        self.assertFalse(conf['Enabled'])
        self.assertNotIn('SteamLogin',conf)
        self.assertEqual((self.config/'fh_account_11.db').read_bytes(),before11)
        self.assertEqual((self.config/'fh_account_12.db').read_bytes(),before12)
        self.assertEqual([a[0] for a in events],['stop','start'])

    def test_reenrollment_recovers_after_purge(self):
        with patch.object(off.os,'geteuid',return_value=0):
            off.purge_offboarded(self.db,self.config,apply=True,runner=lambda *args:None)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE steam_accounts SET requested=1 WHERE id=10')
        spec=importlib.util.spec_from_file_location('friend_provisioner',ROOT/'tools/provision_friends_asf.py')
        importer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(importer)
        # Extend the legacy fixture with the verified account fields.
        with sqlite3.connect(self.db) as conn:
            conn.execute('ALTER TABLE steam_accounts ADD COLUMN steam_id TEXT')
            conn.execute('ALTER TABLE steam_accounts ADD COLUMN login TEXT')
            conn.execute('UPDATE steam_accounts SET steam_id=?, login=? WHERE id=10',
                         ('76561198000000010','reenrolled_name'))
            conn.execute('UPDATE steam_accounts SET steam_id=?, login=? WHERE id=11',
                         ('76561198000000011','demo'))
            conn.execute('UPDATE steam_accounts SET steam_id=?, login=? WHERE id=12',
                         ('76561198000000012','demo'))
        changed=importer.provision(self.db,self.config,apply=True)
        self.assertTrue(any(r['bot']=='fh_account_10' and 're-enrolled' in r['status'] for r in changed))
        obj=json.loads((self.config/'fh_account_10.json').read_text())
        self.assertEqual(obj['SteamLogin'],'reenrolled_name')
        self.assertFalse(obj['Enabled'])

    def test_dry_run_does_not_touch_anything(self):
        original=(self.config/'fh_account_10.db').read_bytes()
        self.assertEqual(off.purge_offboarded(self.db,self.config),['fh_account_10'])
        self.assertEqual((self.config/'fh_account_10.db').read_bytes(),original)
