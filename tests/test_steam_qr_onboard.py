import base64
import importlib.util
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('steam_qr_onboard', ROOT/'tools/steam-qr/onboard.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def dummy_token(sub, audience='client'):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return enc({'alg':'RS256'})+'.'+enc({'sub':sub,'aud':[audience],'exp':int(time.time())+7200})+'.'+'x'*90


class SteamQRHandoffTests(unittest.TestCase):
    def test_accepts_only_matching_client_identity(self):
        expected='76561198000000001'
        self.assertTrue(module.validate_refresh_token(dummy_token(expected),expected))
        for mismatch in (dummy_token('76561198000000002'), dummy_token(expected,'web')):
            with self.assertRaises(ValueError):
                module.validate_refresh_token(mismatch,expected)

    def test_import_preserves_unrelated_bot_data(self):
        with tempfile.TemporaryDirectory() as dirname:
            path=Path(dirname)
            cfg=path/'fh_account_9.json'
            data=path/'fh_account_9.db'
            cfg.write_text(json.dumps({'SteamLogin':'sample_account','Enabled':False}))
            data.write_text(json.dumps({'OtherSavedField':42}))
            module.import_session(dummy_token('76561198000000001'),'76561198000000001',cfg,data)
            persisted=json.loads(data.read_text())
            self.assertEqual(persisted['OtherSavedField'],42)
            self.assertTrue(persisted['BackingRefreshToken'])
            self.assertFalse(json.loads(cfg.read_text())['Enabled'])
            with self.assertRaises(ValueError):
                module.import_session(dummy_token('76561198000000001'),'76561198000000001',cfg,data)

    def test_only_opted_in_and_disabled_bots_are_eligible(self):
        with tempfile.TemporaryDirectory() as dirname:
            path=Path(dirname)
            source=path/'friends.db'
            target=path/'asf'
            target.mkdir()
            with sqlite3.connect(source) as conn:
                conn.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,disabled INTEGER)')
                conn.execute('CREATE TABLE steam_accounts(id INTEGER PRIMARY KEY,user_id INTEGER,steam_id TEXT,login TEXT,requested INTEGER)')
                conn.execute('INSERT INTO users VALUES(1,0)')
                conn.execute('INSERT INTO steam_accounts VALUES(9,1,?,?,1)',('76561198000000001','sample_account'))
            config=target/'fh_account_9.json'
            config.write_text(json.dumps({'SteamLogin':'sample_account','Enabled':False}))
            self.assertEqual(module.context(source,target,9)[1],'76561198000000001')
            with sqlite3.connect(source) as conn:
                conn.execute('UPDATE steam_accounts SET requested=0 WHERE id=9')
            with self.assertRaises(ValueError):
                module.context(source,target,9)
