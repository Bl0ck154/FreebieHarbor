import http.client
import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("friends",ROOT/"friends/portal.py")
portal=importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


class QRAccessTests(unittest.TestCase):
    def test_another_member_cannot_start_qr(self):
        with tempfile.TemporaryDirectory() as tmp:
            portal.FRIENDS_DB=Path(tmp)/"friends.db"
            portal.init_db()
            one=portal.create_invite()
            two=portal.create_invite()
            portal.register(one,"test_owner","long-test-password-one")
            portal.register(two,"test_other","long-test-password-two")
            with portal.database() as db:
                owner=db.execute("SELECT id FROM users WHERE username='test_owner'").fetchone()[0]
            portal.add_verified_steam_account(owner,"76561198000000001")
            account=portal.steam_accounts(owner)[0]["id"]
            portal.request_steam_activation(owner,account,"correct_login",True)
            server=portal.ThreadingHTTPServer(("127.0.0.1",0),portal.Handler)
            origin="http://127.0.0.1:"+str(server.server_port)
            old_origin=portal.PUBLIC_ORIGIN
            portal.PUBLIC_ORIGIN=origin
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                owner_token,csrf1=portal.login("test_owner","long-test-password-one")
                other_token,csrf2=portal.login("test_other","long-test-password-two")
                def post(token,csrf):
                    conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=4)
                    body=urlencode({"csrf":csrf,"account_id":account}).encode()
                    conn.request("POST","/steam/qr/start",body=body,headers={
                        "Origin":origin,"Cookie":"fh_friend="+token,
                        "Content-Type":"application/x-www-form-urlencoded",
                    })
                    response=conn.getresponse()
                    code=response.status
                    response.read()
                    conn.close()
                    return code
                with patch.object(portal,"QR_ENABLED",True),patch.object(
                    portal,"qr_broker_call",return_value={"phase":"starting"}
                ) as backend:
                    self.assertEqual(post(other_token,csrf2),403)
                    self.assertEqual(post(owner_token,"wrong"),403)
                    backend.assert_not_called()
                    self.assertEqual(post(owner_token,csrf1),303)
                    self.assertEqual(backend.call_args.args[2]["account_id"],account)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(4)
                portal.PUBLIC_ORIGIN=old_origin
