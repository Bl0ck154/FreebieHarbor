import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_asf_bootstrap", ROOT / "tools/steam-qr/native_bootstrap.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
ID = "76561198000000001"


class NativeTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.src, self.dst = root / "bootstrap", root / "shared"
        self.src.mkdir()
        self.dst.mkdir()
        self.bot = "fh_account_32"
        self.config = self.dst / (self.bot + ".json")
        self.db = self.dst / (self.bot + ".db")
        self.config.write_text(json.dumps({
            "SteamLogin": "account_32", "Enabled": False, "EnableFreePackages": True,
        }))
        self.db.write_text(json.dumps({"old_state": "keep"}))
        (self.dst / "ASF.json").write_text(json.dumps({
            "IPCPassword": "long-unique-IPC-password-credentials-for-mock",
        }))
        (self.src / (self.bot + ".db")).write_text(
            json.dumps({"native_auth_data": "placeholder", "padding": "x" * 150}))
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, *args, **kwargs):
        self.calls.append(args[:2])
        return "ok"

    def make_call(self, identity=ID, timeout=1):
        return native.transfer_native_session(
            bot=self.bot, steam_id=ID, source_dir=self.src,
            target_dir=self.dst, timeout=timeout,
            runner=self.runner,
            probe=lambda port, password, bot: {
                "SteamID": identity, "IsConnectedAndLoggedOn": True,
            },
        )

    def test_native_files_transferred_and_only_target_enabled(self):
        self.assertTrue(self.make_call())
        self.assertTrue(json.loads(self.config.read_text())["Enabled"])
        self.assertIn("native_auth_data", json.loads(self.db.read_text()))
        self.assertIn("EnableFreePackages", json.loads(self.config.read_text()))
        self.assertEqual(self.calls, [("stop", "-t"), ("start", "freebieharbor-friends-asf")])

    def test_identity_mismatch_restores_all_original_files(self):
        original_cfg = self.config.read_bytes()
        original_db = self.db.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "SteamID"):
            self.make_call(identity="76561198000000002", timeout=0)
        self.assertEqual(original_cfg, self.config.read_bytes())
        self.assertEqual(original_db, self.db.read_bytes())
        self.assertEqual(len(self.calls), 4)

    def test_no_target_file_created_if_source_is_invalid(self):
        (self.src / (self.bot + ".db")).write_text("not-json")
        with self.assertRaises(ValueError):
            self.make_call()
        self.assertEqual(self.calls, [])

    def test_mismatched_steam_id_is_not_attested(self):
        self.assertFalse(native.verified_identity({
            "SteamID": "76561198000000002", "IsConnectedAndLoggedOn": True,
        }, ID))
        self.assertFalse(native.verified_identity({
            "SteamID": ID, "IsConnectedAndLoggedOn": False,
        }, ID))
        self.assertTrue(native.verified_identity({
            "SteamID": ID, "IsConnectedAndLoggedOn": True,
        }, ID))


if __name__ == "__main__":
    unittest.main()
