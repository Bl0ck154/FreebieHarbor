import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATA = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = DATA.name
os.environ.pop("PUBLISHER_BASE", None)
os.environ.pop("INDIEGALA_WORKER", None)
spec = importlib.util.spec_from_file_location("freebieharbor_hub", ROOT / "app" / "hub.py")
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)


class HubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        hub.init_db()

    def test_standalone_without_reddit_server(self):
        self.assertEqual(hub.PUBLISHER_BASE, "")
        self.assertEqual(hub.poll_subreddit("steam_giveaway"), 0)

    def test_url_validation(self):
        self.assertEqual(hub.safe_public_url("mailto:user@example.org"), "")
        self.assertEqual(hub.safe_public_url("https://example.org/game"), "https://example.org/game")

    def test_reddit_classification(self):
        post = {"title": "Steam key giveaway", "selftext": "Leave a comment below to enter.",
                "url": "https://www.reddit.com/r/example/comments/1"}
        self.assertTrue(hub.is_easy_reddit_giveaway(post))
        post["selftext"] = "Join Discord to enter."
        self.assertFalse(hub.is_easy_reddit_giveaway(post))

    def test_dashboard_without_dependencies(self):
        page = hub.render_page("/dashboard", {})
        self.assertIn("FreebieHarbor", page)
        self.assertNotIn("Generate AI reply", page)

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
        self.assertEqual(hub.safe_reddit_url("/r/test/comments/1"), "https://www.reddit.com/r/test/comments/1")
        self.assertEqual(hub.safe_reddit_url("javascript:alert(1)"), "")
        self.assertEqual(hub.safe_reddit_url("https://evil.example/r/test"), "")
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


    def test_reddit_data_pruned_after_24_hours(self):
        with hub.db() as conn:
            conn.execute(
                "INSERT INTO posts (post_id,subreddit,title,body,author,permalink,"
                "requirement_type,first_seen,last_seen,created_utc) VALUES "
                "(?,?,?,?,?,?,?,?,?,?)",
                ("test-expired-reddit", "steam_giveaway", "Old post", "Old body", "anon",
                 "/r/test/comments/old", "simple_comment",
                 hub.now_iso(), hub.now_iso(), 1),
            )
        self.assertGreaterEqual(hub.prune_old_reddit_posts(), 1)
        with hub.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM posts WHERE post_id='test-expired-reddit'").fetchone()[0]
        self.assertEqual(count, 0)

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
                base + "/state", data=b"id=does-not-exist&state=ignored",
                headers={"Origin": base, "Content-Type": "application/x-www-form-urlencoded"},
            )
            with urlopen(request, timeout=4) as result:
                self.assertEqual(result.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=4)


if __name__ == "__main__":
    unittest.main()
