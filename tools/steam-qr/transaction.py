"""Testable, injection-only session handoff transaction.

No real Docker/Steam backend is exposed here. Production integration must
supply a backend that can stop/restart only Friends ASF and independently
attest the logged-in bot's SteamID. A checkable rollback is mandatory.
"""
import fcntl
import json
import os
import tempfile
from pathlib import Path


def atomic_restore(path, content):
    path = Path(path)
    if content is None:
        if path.exists():
            path.unlink()
        return
    fd, tmp = tempfile.mkstemp(prefix=".fh-restore-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def coordinated_handoff(*, bot_name, expected_steam_id, config_path,
                        database_path, import_session, verify_token,
                        token, backend, lock_path):
    """Atomically prepare a single bot; never succeed without live attestation.

    backend must implement stop(), start(), check_identity(bot, steam_id).
    Requires an exclusive lock, plus an injected SteamID attestor. Neither
    simulated attestation nor JWT claims are sufficient in a real deployment.
    """
    if not bot_name.startswith("fh_account_") or not bot_name[11:].isdigit():
        raise ValueError("Invalid Friends bot name")
    if not expected_steam_id.startswith("7656119") or len(expected_steam_id) != 17:
        raise ValueError("Invalid SteamID")
    verify_token(token, expected_steam_id)
    lock = Path(lock_path)
    with lock.open("a+b") as fd:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        cfg = Path(config_path)
        db = Path(database_path)
        if cfg.is_symlink() or db.is_symlink() or not cfg.is_file():
            raise ValueError("Missing or unsafe bot configuration")
        if cfg.stem != bot_name or db.stem != bot_name:
            raise ValueError("Bot path mismatch")
        original_cfg = cfg.read_bytes()
        original_db = db.read_bytes() if db.is_file() else None
        if json.loads(original_cfg).get("Enabled") is not False:
            raise ValueError("The bot must be disabled")
        stopped = False
        try:
            backend.stop()
            stopped = True
            import_session(token, expected_steam_id, cfg, db)
            config = json.loads(cfg.read_text())
            config["Enabled"] = True
            atomic_restore(cfg, (json.dumps(config, indent=2)+"\n").encode())
            backend.start()
            if not backend.check_identity(bot_name, expected_steam_id):
                raise RuntimeError("Live ASF SteamID attestation failed")
        except BaseException:
            # Stop before rollback to prevent ASF writing its state over ours.
            if stopped:
                try:
                    backend.stop()
                finally:
                    atomic_restore(cfg, original_cfg)
                    atomic_restore(db, original_db)
                    backend.start()
            raise
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        return "verified and connected"
