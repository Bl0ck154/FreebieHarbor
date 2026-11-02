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


if __name__ == "__main__":
    unittest.main()
