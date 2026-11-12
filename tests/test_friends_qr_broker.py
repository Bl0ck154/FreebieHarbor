import http.client
import importlib.util
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qr_broker',ROOT/'friends/qr_broker.py')
broker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.db=root/'friends.db'
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,disabled INTEGER)')
            db.execute('CREATE TABLE steam_accounts(id INTEGER PRIMARY KEY,user_id INTEGER,requested INTEGER,steam_id TEXT,login TEXT)')
            db.executemany('INSERT INTO users VALUES(?,?)',[(1,0),(2,0)])
            db.executemany('INSERT INTO steam_accounts VALUES(?,?,?,?,?)',[
                (11,1,1,'76561198000000001','one_login'),
                (12,1,1,'76561198000000002','two_login'),
                (21,2,1,'76561198000000003','three_login'),
            ])
        self.orig_db=broker.ACCOUNT_DB
        broker.ACCOUNT_DB=self.db

    def tearDown(self):
        broker.ACCOUNT_DB=self.orig_db
        self.temp.cleanup()

    def test_svg_qr_stays_local_and_rejects_unsafe_url(self):
        import base64
        with self.assertRaises(ValueError):
            broker.svg_qr("javascript:alert(1)")
        try:
            import segno
        except ImportError:
            self.skipTest("segno unavailable in this local interpreter")
        image=base64.b64decode(broker.svg_qr("https://s.team/q/example-code"))
        self.assertIn(b"<svg",image)
        self.assertNotIn(b"<script",image)

    def test_owner_and_second_account_are_distinct(self):
        self.assertTrue(broker.valid_owner(1,11))
        self.assertTrue(broker.valid_owner(1,12))
        self.assertFalse(broker.valid_owner(2,12))
        self.assertFalse(broker.valid_owner(1,21))
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE steam_accounts SET requested=0 WHERE id=12')
        self.assertFalse(broker.valid_owner(1,12))

    def test_one_active_qr_session_and_no_leaking_to_other_user(self):
        engine=broker.Engine()
        with patch.object(engine,'_work',return_value=None),patch.object(broker,'is_live_account',return_value=False):
            engine.start(1,11)
            self.assertEqual(engine.status(1,11)['phase'],'starting')
            with self.assertRaises(RuntimeError):
                engine.start(2,21)
            with self.assertRaises(PermissionError):
                engine.status(2,11)
            with sqlite3.connect(self.db) as db:
                db.execute('UPDATE steam_accounts SET requested=0 WHERE id=11')
            with self.assertRaises(PermissionError):
                engine.status(1,11)

    def test_auth_required_for_unix_socket_http(self):
        root=Path(self.temp.name)
        token=root/'token'
        token.write_text('secure-secret-for-broker-unit-test-only')
        path=root/'broker.sock'
        original_auth=broker.AUTH_FILE
        broker.AUTH_FILE=token
        server=broker.UnixHTTPServer(str(path),broker.Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            class C(http.client.HTTPConnection):
                def connect(self):
                    import socket
                    self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                    self.sock.connect(str(path))
            def get(headers):
                connection=C('localhost',timeout=3)
                try:
                    connection.request('GET','/health',headers=headers)
                    response=connection.getresponse()
                    return response.status,json.loads(response.read())
                finally:
                    connection.close()
            self.assertEqual(get({})[0],403)
            self.assertEqual(get({'Authorization':token.read_text()})[0],200)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
            broker.AUTH_FILE=original_auth


if __name__=='__main__':
    unittest.main()
