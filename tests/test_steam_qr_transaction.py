import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('qr_transaction', ROOT/'tools/steam-qr/transaction.py')
tx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tx)


class FakeASF:
    def __init__(self, matches):
        self.matches = matches
        self.events = []
    def stop(self):
        self.events.append('stop')
    def start(self):
        self.events.append('start')
    def check_identity(self, name, steam_id):
        self.events.append('verify')
        return self.matches


class TransactionTests(unittest.TestCase):
    steam_id = '76561198000000001'

    def run_transaction(self, verified):
        with tempfile.TemporaryDirectory() as dirname:
            root=Path(dirname)
            config=root/'fh_account_12.json'
            database=root/'fh_account_12.db'
            lock=root/'handoff.lock'
            config.write_text(json.dumps({'Enabled':False,'SteamLogin':'alice'}))
            database.write_text(json.dumps({'ExistingState':'keep'}))
            original_config=config.read_bytes()
            original_database=database.read_bytes()
            backend=FakeASF(verified)
            def import_session(token, sid, cfg, db):
                data=json.loads(db.read_text())
                data['BackingRefreshToken']=token
                db.write_text(json.dumps(data))
            def verify_token(token, sid):
                self.assertEqual(token,'mock-secret-token')
                self.assertEqual(sid,self.steam_id)
            def action():
                return tx.coordinated_handoff(
                    bot_name='fh_account_12', expected_steam_id=self.steam_id,
                    config_path=config, database_path=database,
                    import_session=import_session, verify_token=verify_token,
                    token='mock-secret-token',backend=backend,lock_path=lock)
            if verified:
                self.assertEqual(action(),'verified and connected')
                self.assertTrue(json.loads(config.read_text())['Enabled'])
                self.assertEqual(json.loads(database.read_text())['ExistingState'],'keep')
                self.assertEqual(backend.events,['stop','start','verify'])
            else:
                with self.assertRaisesRegex(RuntimeError,'attestation'):
                    action()
                self.assertEqual(config.read_bytes(),original_config)
                self.assertEqual(database.read_bytes(),original_database)
                self.assertEqual(backend.events,['stop','start','verify','stop','start'])

    def test_attested_session(self):
        self.run_transaction(True)
    def test_mismatched_session_rolls_back_exactly(self):
        self.run_transaction(False)
