import http.client
import importlib.util
import re
import sqlite3
import tempfile
import threading
from unittest.mock import patch
from datetime import datetime,timezone
from urllib.parse import urlsplit,parse_qs
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("friends_portal", ROOT / "friends/portal.py")
portal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal)


class FriendsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        portal.FRIENDS_DB = base / "friends.db"
        portal.HUB_DB = base / "offers.db"
        portal.FAILED_LOGINS.clear()
        portal.init_db()
        with sqlite3.connect(portal.HUB_DB) as c:
            c.execute("CREATE TABLE external_offers (offer_id TEXT PRIMARY KEY,title TEXT,source TEXT,url TEXT,platform TEXT,offer_type TEXT,ends_at TEXT,active INTEGER,provider TEXT,first_seen TEXT)")
            c.execute("INSERT INTO external_offers VALUES(?,?,?,?,?,?,?,?,?,?)", ("demo:1","Test Free Game","gamerpower","https://store.steampowered.com/app/123","Steam","Free to keep","",1,"Steam","2026-11-02"))
            c.execute("INSERT INTO external_offers VALUES(?,?,?,?,?,?,?,?,?,?)", ("hidden:2","Hidden offer","reddit","https://example.org","Steam","Giveaway","",1,"Reddit","2026-11-02"))

    def tearDown(self):
        self.temp.cleanup()

    def test_invite_single_use_and_password_hash(self):
        invite = portal.create_invite(1)
        self.assertTrue(portal.register(invite, "friend_one", "my-strong-password-123"))
        self.assertFalse(portal.register(invite, "friend_two", "another-strong-password"))
        session = portal.login("friend_one", "my-strong-password-123")
        self.assertIsNotNone(session)
        self.assertIsNone(portal.login("friend_one", "wrong-password"))
        self.assertIsNotNone(portal.get_session(session[0]))
        portal.logout(session[0])
        self.assertIsNone(portal.get_session(session[0]))
        data = portal.FRIENDS_DB.read_bytes()
        self.assertNotIn(invite.encode(), data)
        self.assertNotIn(b"my-strong-password-123", data)

    def test_user_data_isolation(self):
        invite1,invite2 = portal.create_invite(),portal.create_invite()
        self.assertTrue(portal.register(invite1,"alice","very-long-secret-one"))
        self.assertTrue(portal.register(invite2,"bob","very-long-secret-two"))
        with portal.database() as db:
            ids = {r["username"]:r["id"] for r in db.execute("SELECT id,username FROM users")}
        self.assertTrue(portal.update_offer_state(ids["alice"],"demo:1","claimed"))
        self.assertEqual(portal.shared_offers(ids["alice"])[0]["state"],"claimed")
        self.assertEqual(portal.shared_offers(ids["bob"])[0]["state"],"new")
        self.assertEqual(len(portal.shared_offers(ids["bob"])),1)
        self.assertFalse(portal.update_offer_state(ids["alice"],"hidden:2","claimed"))
        self.assertFalse(portal.update_offer_state(ids["alice"],"demo:1","entered"))

    def test_disabled_account_revokes_authentication(self):
        invite=portal.create_invite()
        portal.register(invite,"charlie","very-long-secret-three")
        token,_csrf=portal.login("charlie","very-long-secret-three")
        with portal.database() as db:
            db.execute("UPDATE users SET disabled=1 WHERE username='charlie'")
        self.assertIsNone(portal.get_session(token))

    def test_expired_invitation_is_rejected(self):
        invite=portal.create_invite(1)
        with portal.database() as db:
            db.execute("UPDATE invites SET expires_at=1")
        self.assertFalse(portal.register(invite,"late_user","strong-test-password"))

    def test_untrusted_external_offer_link_is_not_followed(self):
        self.assertEqual(portal.safe_link("javascript:alert(1)"),"#")
        self.assertEqual(portal.safe_link("http://127.0.0.1/private"),"#")
        self.assertEqual(portal.safe_link("https://store.steampowered.com/app/123"),"https://store.steampowered.com/app/123")

    def test_asf_fragment_has_no_steam_credentials(self):
        spec2=importlib.util.spec_from_file_location("asf_friend_config",ROOT/"tools/asf_freepackages_profile.py")
        module=importlib.util.module_from_spec(spec2)
        spec2.loader.exec_module(module)
        settings=module.profile()
        self.assertTrue(settings["EnableFreePackages"])
        self.assertTrue(settings["FreePackagesFilters"][0]["NoCostOnly"])
        self.assertTrue(settings["FreePackagesFilters"][0]["IgnoreFreeWeekends"])
        self.assertNotIn("SteamPassword",settings)
        self.assertNotIn("SteamLogin",settings)
        self.assertEqual(settings["FreePackagesLimit"],15)

    def test_admin_invite_roles_and_member_access(self):
        admin_invite = portal.create_invite(is_admin=True)
        member_invite = portal.create_invite()
        self.assertTrue(portal.register(admin_invite,"owner_user","strong-admin-password"))
        self.assertTrue(portal.register(member_invite,"member_user","strong-member-password"))
        admin_session = portal.get_session(portal.login("owner_user","strong-admin-password")[0])
        member_session = portal.get_session(portal.login("member_user","strong-member-password")[0])
        self.assertEqual(admin_session["is_admin"],1)
        self.assertEqual(member_session["is_admin"],0)
        self.assertIn("Create invite",portal.admin_content(admin_session))
        self.assertEqual(portal.admin_content(member_session),"")
        self.assertNotIn('href="/admin"',portal.page("Test","",member_session["username"],False))
        self.assertIn('href="/admin"',portal.page("Test","",admin_session["username"],True))

    def test_link_steam_openid_rejects_bad_endpoint(self):
        inv = portal.create_invite()
        portal.register(inv,"another_friend","long-good-password")
        user = portal.get_session(portal.login("another_friend","long-good-password")[0])
        url = portal.steam_link_begin(user["id"])
        ret = parse_qs(urlsplit(url).query)["openid.return_to"][0]
        state = parse_qs(urlsplit(ret).query)["state"][0]
        data = {"state":[state],"openid.mode":["id_res"],
                "openid.op_endpoint":["https://wrong.example"],
                "openid.return_to":[ret]}
        with patch.object(portal,"urlopen") as request:
            self.assertFalse(portal.verify_steam_openid(user["id"],data))
            request.assert_not_called()

    def test_verified_steam_id_is_saved_once(self):
        inv=portal.create_invite()
        portal.register(inv,"steamtest","long-password-for-testing")
        uid=portal.get_session(portal.login("steamtest","long-password-for-testing")[0])["id"]
        url=portal.steam_link_begin(uid)
        ret=parse_qs(urlsplit(url).query)["openid.return_to"][0]
        state=parse_qs(urlsplit(ret).query)["state"][0]
        cid="https://steamcommunity.com/openid/id/76561198000000001"
        nonce=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")+"uniqueval123"
        callback={"state":[state],"openid.mode":["id_res"],
          "openid.op_endpoint":[portal.STEAM_OPENID_ENDPOINT],
          "openid.return_to":[ret],"openid.claimed_id":[cid],"openid.identity":[cid],
          "openid.response_nonce":[nonce],
          "openid.signed":["return_to,claimed_id,identity,response_nonce,op_endpoint"]}
        class Reply:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return b"is_valid:true\n"
        with patch.object(portal,"urlopen",return_value=Reply()) as call:
            self.assertTrue(portal.verify_steam_openid(uid,callback))
            self.assertFalse(portal.verify_steam_openid(uid,callback))
            self.assertEqual(call.call_count,1)
        with portal.database() as conn:
            self.assertEqual(conn.execute("SELECT steam_id FROM users WHERE id=?",(uid,)).fetchone()[0],cid.rsplit("/",1)[-1])

    def test_http_auth_and_csrf(self):
        server=portal.ThreadingHTTPServer(("127.0.0.1",0),portal.Handler)
        portal.PUBLIC_ORIGIN="http://127.0.0.1:"+str(server.server_port)
        portal.COOKIE_SECURE=False
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            invite=portal.create_invite()
            portal.register(invite,"demo_user","very-long-password")
            def req(method,path,data=None,headers=None):
                conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=4)
                head={"Origin":portal.PUBLIC_ORIGIN,"Content-Type":"application/x-www-form-urlencoded"}
                head.update(headers or {})
                conn.request(method,path,body=data,headers=head)
                response=conn.getresponse()
                out=(response.status,response.read().decode(),response.getheader("Set-Cookie"))
                conn.close()
                return out
            status,body,cookie=req("POST","/login","username=demo_user&password=very-long-password")
            self.assertEqual(status,303)
            self.assertIn("HttpOnly",cookie)
            session_cookie=cookie.split(";",1)[0]
            status,body,_=req("GET","/offers",headers={"Cookie":session_cookie})
            self.assertEqual(status,200)
            self.assertIn("Test Free Game",body)
            csrf=re.search(r'name="csrf" value="([^"]+)"',body).group(1)
            form="offer_id=demo%3A1&state=claimed&csrf="+csrf
            self.assertEqual(req("POST","/state",form,{"Cookie":session_cookie,"Origin":"https://bad.example"})[0],403)
            self.assertEqual(req("POST","/state","offer_id=demo%3A1&state=claimed&csrf=bad",{"Cookie":session_cookie})[0],403)
            self.assertEqual(req("POST","/state",form,{"Cookie":session_cookie})[0],303)
            self.assertIn("claimed",req("GET","/offers",headers={"Cookie":session_cookie})[1])
            self.assertEqual(req("GET","/account")[0],303)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(4)


if __name__=="__main__":
    unittest.main()
