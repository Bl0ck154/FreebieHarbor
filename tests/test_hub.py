import importlib.util
import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATA = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = DATA.name
os.environ.pop("INDIEGALA_WORKER", None)
spec = importlib.util.spec_from_file_location("freebieharbor_hub", ROOT / "app" / "hub.py")
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)


class HubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hub.init_db()


    def test_url_validation(self):
        self.assertEqual(hub.safe_public_url("mailto:user@example.org"), "")
        self.assertEqual(hub.safe_public_url("https://example.org/game"), "https://example.org/game")


    def test_dashboard_without_dependencies(self):
        page = hub.render_page("/dashboard", {})
        self.assertIn("FreebieHarbor", page)
        self.assertNotIn("Generate AI reply", page)

    def test_release_version_matches_software(self):
        self.assertEqual(hub.APP_VERSION, "0.8.0")

    def test_optional_rewards_not_configured(self):
        with patch.dict(os.environ, {"STEAMGIFTS_DB": ""}):
            self.assertFalse(hub.rewards_summary()["connected"])


    def test_new_offer_notification_baseline_and_dedupe(self):
        with patch.object(hub, "NOTIFY_NEW_OFFERS", True), \
             patch.object(hub, "TELEGRAM_BOT_TOKEN", "test-value"), \
             patch.object(hub, "TELEGRAM_CHAT_ID", "12345"), \
             patch.object(hub, "_send_telegram") as send:
            with hub.db() as conn:
                conn.execute("DELETE FROM offer_notifications")
                conn.execute("DELETE FROM meta WHERE key='offer_notifications_initialized'")
            first = hub.notify_new_offers()
            self.assertTrue(first["baseline"])
            send.assert_not_called()
            with hub.db() as conn:
                conn.execute(
                    "INSERT INTO external_offers (offer_id,source,title,url,first_seen,last_seen) "
                    "VALUES (?,?,?,?,?,?)",
                    ("test:new-1", "gamerpower", "Test Game", "https://example.org/game",
                     hub.now_iso(), hub.now_iso()),
                )
            next_cycle = hub.notify_new_offers()
            self.assertEqual(next_cycle["sent"], 1)
            send.assert_called_once()
            self.assertIn("Test Game", send.call_args.args[0])
            self.assertEqual(hub.notify_new_offers()["sent"], 0)
            send.assert_called_once()

    def test_cross_origin_post_is_rejected(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_port)
            request = Request(
                base + "/refresh", data=b"return_to=%2Fdashboard",
                headers={"Origin": "https://other.example",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            with self.assertRaises(HTTPError) as failed:
                urlopen(request, timeout=3)
            self.assertEqual(failed.exception.code, 403)
            with urlopen(base + "/health", timeout=3) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


    def test_url_and_redirect_safety(self):
        self.assertEqual(hub._safe_return_to("/offers?page=2"), "/offers?page=2")
        self.assertEqual(hub._safe_return_to("/\\evil.example"), "/dashboard")
        self.assertEqual(hub._safe_return_to("//evil.example"), "/dashboard")

    def test_provider_offer_saved(self):
        response = [{
            "id": 982311, "title": "Example game", "type": "Game",
            "open_giveaway_url": "https://www.gamerpower.com/example",
            "image": "https://www.gamerpower.com/picture.jpg",
            "platforms": "Steam", "worth": "10", "end_date": "N/A",
        }]
        with patch.object(hub, "fetch_url_json", return_value=response):
            self.assertEqual(hub.poll_gamerpower(), 1)
        rows, _, _, _ = hub.external_offers_data(source="gamerpower")
        self.assertTrue(any(x["title"] == "Example game" for x in rows))

    def test_delivery_retry_after_temporary_error(self):
        with patch.object(hub, "NOTIFY_NEW_OFFERS", True), \
             patch.object(hub, "TELEGRAM_BOT_TOKEN", "test-token"), \
             patch.object(hub, "TELEGRAM_CHAT_ID", "123"), \
             patch.object(hub, "_send_telegram") as send:
            with hub.db() as conn:
                conn.execute("DELETE FROM offer_notifications")
                conn.execute("DELETE FROM meta WHERE key='offer_notifications_initialized'")
            self.assertTrue(hub.notify_new_offers()["baseline"])
            with hub.db() as conn:
                conn.execute(
                    "INSERT INTO external_offers(offer_id,source,title,url,first_seen,last_seen) "
                    "VALUES (?,?,?,?,?,?)",
                    ("test:retry-2", "gamerpower", "Retry game",
                     "https://example.org/retry", hub.now_iso(), hub.now_iso())
                )
            send.side_effect = RuntimeError("Telegram network error")
            self.assertEqual(hub.notify_new_offers()["sent"], 0)
            send.side_effect = None
            self.assertGreaterEqual(hub.notify_new_offers()["sent"], 1)
            self.assertEqual(hub.notify_new_offers()["sent"], 0)



    def test_valid_local_post(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import Request, urlopen
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_port)
            request = Request(
                base + "/external-state", data=b"id=does-not-exist&state=ignored",
                headers={"Origin": base, "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urlopen(request, timeout=4) as result:
                self.assertEqual(result.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=4)


    def test_demo_poll_is_disabled(self):
        with patch.object(hub, "get_meta", return_value="1"):
            self.assertTrue(hub.poll_all()["demo"])


    def test_asf_snapshot_counts_only(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "freepackages.json"
            path.write_text(json.dumps({
                "SeenPackages": [123, 456, 789],
                "NewOwnedPackages": [654],
                "BackingAccessToken": "a-test-secret-that-must-not-be-exposed",
            }))
            with patch.dict(os.environ, {"ASF_PACKAGES_DB": str(path)}):
                report = hub.asf_summary()
            self.assertEqual(report, {
                "connected": True, "seen_packages": 3, "new_owned_queue": 1,
            })
            self.assertNotIn("a-test-secret", str(report))

    def test_asf_invalid_snapshot_has_safe_error(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "invalid.json"
            path.write_text('{"BackingAccessToken":"secret"}')
            with patch.dict(os.environ, {"ASF_PACKAGES_DB": str(path)}):
                self.assertEqual(hub.asf_summary()["error"], "ASF snapshot unavailable")

    def test_win_notification_baseline_new_wins_retry_and_dedup(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "wins snapshot.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                "CREATE TABLE giveaways (code TEXT, game_name TEXT, is_won INTEGER, won_at TEXT);"
                "INSERT INTO giveaways VALUES('abc12','Old Game',1,'2026-10-30');"
                "CREATE TABLE entries (status TEXT);"
                "INSERT INTO entries VALUES('success');"
            )
            conn.commit()
            with patch.dict(os.environ, {"STEAMGIFTS_DB": str(path)}), \
                 patch.object(hub, "NOTIFY_NEW_WINS", True), \
                 patch.object(hub, "TELEGRAM_BOT_TOKEN", "demo-token"), \
                 patch.object(hub, "TELEGRAM_CHAT_ID", "example"), \
                 patch.object(hub, "_send_telegram") as send:
                with hub.db() as internal:
                    internal.execute("DELETE FROM win_notifications")
                    internal.execute("DELETE FROM meta WHERE key='win_notifications_initialized'")
                self.assertTrue(hub.notify_new_wins()["baseline"])
                send.assert_not_called()
                conn.execute(
                    "INSERT INTO giveaways VALUES('xyz87','Test & Win',1,'2026-11-02')"
                )
                conn.commit()
                self.assertEqual(len(hub.steamgifts_wins()), 2)
                send.side_effect = RuntimeError("Telegram network error")
                self.assertEqual(hub.notify_new_wins()["sent"], 0)
                send.side_effect = None
                self.assertEqual(hub.notify_new_wins()["sent"], 1)
                self.assertIn("Test &amp; Win", send.call_args.args[0])
                self.assertEqual(hub.notify_new_wins()["sent"], 0)
            conn.close()

    def test_rewards_page_without_config(self):
        self.assertIn("Reward tracking", hub.render_page("/rewards", {}))


    def test_retired_reddit_route_and_public_menu(self):
        self.assertIsNone(hub.render_page("/reddit", {}))
        page = hub.render_page("/dashboard", {})
        self.assertIn('href="/offers"', page)
        self.assertIn('href="/rewards"', page)
        self.assertNotIn('href="/reddit"', page)
        self.assertNotIn("Reddit posts", page)

    def test_retired_reddit_http_api_is_not_exposed(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.error import HTTPError
        from urllib.request import urlopen
        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(HTTPError) as err:
                urlopen("http://127.0.0.1:" + str(server.server_port) + "/api/posts", timeout=4)
            self.assertEqual(err.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=4)

    def test_landing_page_has_real_seo_metadata(self):
        import json
        import re
        content = (ROOT / "docs/site/index.html").read_text()
        self.assertIn('rel="canonical"', content)
        self.assertIn('property="og:title"', content)
        self.assertIn('property="og:image"', content)
        self.assertIn('name="description"', content)
        result = re.search(r'<script type="application/ld\+json">(.*?)</script>', content, re.S)
        self.assertIsNotNone(result)
        data = json.loads(result.group(1))
        self.assertEqual(data["@type"], "SoftwareApplication")
        self.assertEqual(data["softwareVersion"], hub.APP_VERSION)


    def test_refresh_endpoint_responds_without_waiting_for_network(self):
        import http.client
        import time
        from threading import Event, Thread
        from http.server import ThreadingHTTPServer

        entered = Event()
        release = Event()

        def stalled_source_scan():
            entered.set()
            release.wait(timeout=4)
            return {"ok": True}

        server = ThreadingHTTPServer(("127.0.0.1", 0), hub.Handler)
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        try:
            with patch.object(hub, "poll_all", side_effect=stalled_source_scan), \
                 patch.object(hub, "get_meta", return_value="0"):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                start = time.monotonic()
                connection.request(
                    "POST", "/refresh", body="return_to=%2Fdashboard",
                    headers={
                        "Origin": "http://127.0.0.1:" + str(server.server_port),
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                self.assertLess(time.monotonic() - start, 1.0)
                self.assertTrue(entered.wait(timeout=2))
                response.read()
                connection.close()
        finally:
            release.set()
            if hub._refresh_thread is not None:
                hub._refresh_thread.join(timeout=3)
            server.shutdown()
            server.server_close()
            serving.join(timeout=3)

    def test_release_version_in_dashboard_and_user_agent(self):
        self.assertIn("FreebieHarbor v0.8.0", hub.render_page("/dashboard", {}))
        self.assertIn("0.8.0", hub.EXTERNAL_USER_AGENT)


    def test_sanitized_asf_count_format(self):
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "aggregate.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "seen_packages_count": 116,
                "new_owned_queue_count": 0,
            }))
            with patch.dict(os.environ, {"ASF_PACKAGES_DB": str(path)}):
                self.assertEqual(hub.asf_summary(), {
                    "connected": True, "seen_packages": 116, "new_owned_queue": 0
                })


if __name__ == "__main__":
    unittest.main()
