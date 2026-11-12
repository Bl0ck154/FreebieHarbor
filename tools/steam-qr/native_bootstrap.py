#!/usr/bin/env python3
"""Operator-only native ASF QR bootstrap. No Steam tokens are manually edited.

Runs a disposable, interactive ASF with one opted-in bot. ASF itself writes the
Steam session to its bot database. A successful bootstrap is copied to the
existing Friends ASF only after it has been stopped, and must pass a second
SteamID attestation before it is committed.

The owner must scan a Steam Mobile QR to perform a REAL account test. This
script cannot simulate or promise that Steam approved a login.
"""
import argparse
import fcntl
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).resolve().parents[2]
spec = spec_from_file_location("friends_qr_account", ROOT / "tools/steam-qr/account_context.py")
mod = module_from_spec(spec)
spec.loader.exec_module(mod)
IMAGE = "ghcr.io/justarchinet/archisteamfarm:6.3.11.7@sha256:b6e0f6251c745f04e2a359aab0033f1487aec2ba204141a5abaa3f5af2696e10"
SESSION_TIMEOUT = 180
STEAMID_RE = re.compile(r"7656119[0-9]{10}$")


def run_docker(*args, timeout=50):
    return subprocess.check_output(["docker", *args], stderr=subprocess.DEVNULL, timeout=timeout, text=True).strip()


def asf_bot_response(endpoint, secret, bot):
    conn = http.client.HTTPConnection("127.0.0.1", endpoint, timeout=4)
    try:
        conn.request("GET", "/Api/Bot/" + quote(bot, safe=""),
                     headers={"Authentication": secret, "Accept": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            return None
        data = json.loads(response.read(200000))
        if not data.get("Success"):
            return None
        bots = data.get("Result")
        # ASF returns bot-name-indexed dictionaries (not a list) in current IPC.
        if isinstance(bots, dict):
            return bots.get(bot)
        if isinstance(bots, list) and len(bots) == 1:
            return bots[0]
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        conn.close()


def verified_identity(bot, steam_id):
    if not isinstance(bot, dict):
        return False
    try:
        reported = str(bot["SteamID"])
        return (reported == steam_id and bool(bot.get("IsConnectedAndLoggedOn"))
                and STEAMID_RE.fullmatch(reported) is not None)
    except (KeyError, TypeError, ValueError):
        return False


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_json(path, obj):
    # All bootstrap material is disposable, and is private to the worker.
    path.write_text(json.dumps(obj, indent=2) + "\n")
    path.chmod(0o600)


def prepare_bootstrap(temp, bot, login, ipc_password):
    folder = temp / "config"
    folder.mkdir(mode=0o700)
    write_json(folder / "ASF.json", {
        "Headless": False, "IPC": True, "IPCPassword": ipc_password,
        "AutoRestart": False,
    })
    write_json(folder / "IPC.config", {
        "Kestrel": {"Endpoints": {"HTTP": {"Url": "http://*:1242"}}},
    })
    write_json(folder / (bot + ".json"), {
        "Enabled": True, "SteamLogin": login,
        "EnableFreePackages": False,
    })
    return folder


def launch_interactive(container_name, config_dir, port):
    run_docker(
        "run", "-d", "-i", "-t", "--rm",
        "--name", container_name,
        "--label", "freebieharbor.qr-bootstrap=1",
        "--memory", "256m", "--cpus", "0.35",
        "--pids-limit", "96", "--cap-drop", "ALL",
        "--cap-add", "CHOWN", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "--security-opt", "no-new-privileges:true",
        "--publish", f"127.0.0.1:{port}:1242",
        "--volume", f"{config_dir}:/app/config",
        IMAGE,
    )


def await_qr_approval(container_name, port, password, bot, steam_id, timeout=SESSION_TIMEOUT):
    # ASF writes the ASCII QR into the attached console, not into a web
    # response. The operator sees it only in their private shell.
    attach = subprocess.Popen(
        ["docker", "attach", "--sig-proxy=false", container_name],
        stdin=None, stdout=None, stderr=None,
    )
    try:
        time.sleep(2)
        if attach.poll() is not None:
            raise RuntimeError("Unable to attach to the ASF QR console")
        print("When ASF asks whether to start QR login, answer Y. Scan its QR with Steam Mobile.", flush=True)
        print("The temporary account will never receive FreePackages requests.")
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            state = asf_bot_response(port, password, bot)
            if verified_identity(state, steam_id):
                return True
            if isinstance(state, dict):
                reported = str(state.get("SteamID", "0"))
                if reported not in ("0", "None", steam_id):
                    raise RuntimeError("QR login belongs to a different SteamID")
            if attach.poll() is not None:
                raise RuntimeError("Temporary ASF exited before identity confirmation")
            time.sleep(2)
        raise TimeoutError("QR login expired without verified ASF sign-in")
    finally:
        if attach.poll() is None:
            attach.terminate()
            try:
                attach.wait(timeout=3)
            except subprocess.TimeoutExpired:
                attach.kill()


def stop_temporary(name):
    try:
        run_docker("stop", "-t", "15", name, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass


def atomic_bytes(path, value):
    path = Path(path)
    if value is None:
        if path.exists():
            path.unlink()
        return
    fd, staging = tempfile.mkstemp(prefix=".fh-asf-transfer-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(staging, 0o600)
        os.replace(staging, path)
    finally:
        if os.path.exists(staging):
            os.unlink(staging)


def transfer_native_session(*, bot, steam_id, source_dir, target_dir,
                            target_port=1246, timeout=65,
                            runner=run_docker, probe=asf_bot_response):
    """Transfer ASF-generated data, never edit BotName.db fields manually.

    Stop only the secondary Friends ASF and verify it logged in to the exact
    SteamID before declaring success. On mismatch, restore all changed bytes
    and leave the bot disabled.
    """
    source_dir, target_dir = Path(source_dir), Path(target_dir)
    names = [bot + suffix for suffix in (".json", ".db", ".bin")]
    source = source_dir / (bot + ".db")
    if source.is_symlink() or not source.is_file():
        raise ValueError("ASF did not save a bot session")
    # A valid native login should have resulted in persisted ASF state.
    if not 100 <= source.stat().st_size <= 4_000_000:
        raise ValueError("Missing or unexpectedly sized ASF bot database")
    if not isinstance(json.loads(source.read_text()), dict):
        raise ValueError("Invalid ASF-generated bot database")
    config = target_dir / (bot + ".json")
    if config.is_symlink() or not config.is_file():
        raise ValueError("Missing destination config")
    original_config = json.loads(config.read_text())
    if original_config.get("Enabled") is not False:
        raise ValueError("Target bot is already active")
    for name in names:
        if (source_dir / name).is_symlink() or (target_dir / name).is_symlink():
            raise ValueError("Symlink in bot data paths")
    changed = {name: (target_dir / name).read_bytes() if (target_dir / name).exists() else None
               for name in names}
    generated = {suffix: (source_dir / (bot+suffix)).read_bytes()
                 for suffix in (".db", ".bin") if (source_dir / (bot+suffix)).is_file()}
    if ".db" not in generated:
        raise ValueError("Missing native database")
    runtime_config = json.loads(changed[bot + ".json"])
    runtime_config["Enabled"] = True
    global_config = json.loads((target_dir / "ASF.json").read_text())
    password = global_config.get("IPCPassword")
    if not isinstance(password, str) or len(password) < 24:
        raise ValueError("Friends ASF IPC authentication is not configured")
    service = "freebieharbor-friends-asf"
    was_stopped = False
    try:
        runner("stop", "-t", "20", service, timeout=40)
        was_stopped = True
        for suffix, blob in generated.items():
            atomic_bytes(target_dir / (bot + suffix), blob)
        atomic_bytes(config, (json.dumps(runtime_config, indent=2)+"\n").encode())
        runner("start", service, timeout=40)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if verified_identity(probe(target_port, password, bot), steam_id):
                return True
            time.sleep(2)
        raise RuntimeError("Friends ASF could not confirm the matching SteamID")
    except BaseException:
        if was_stopped:
            # Never restore ASF-managed data while the process may be running.
            runner("stop", "-t", "20", service, timeout=40)
            for name, content in changed.items():
                atomic_bytes(target_dir / name, content)
            runner("start", service, timeout=40)
        raise


def bootstrap_account(*, friends_db, asf_config, account_id, timeout=SESSION_TIMEOUT,
                      dry_run=True):
    # Use the same account-scoped verification as the Friends provisioner.
    bot, steam_id, login, dest_config, _dest_db = mod.context(
        friends_db, asf_config, account_id)
    if dry_run:
        return {"bot":bot, "steam_id":steam_id, "ready":True, "applied":False}
    if not os.isatty(0):
        raise RuntimeError("Native ASF QR login requires an interactive terminal")
    if os.geteuid() != 0:
        raise PermissionError("Native ASF bootstrap requires an operator with root access to coordinate container restarts and clean private files")
    lock = Path(asf_config) / ".friends-qr-onboarding.lock"
    with lock.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix="fh-native-asf-") as folder:
            temp = Path(folder)
            temp.chmod(0o700)
            password = secrets.token_urlsafe(48)
            src = prepare_bootstrap(temp, bot, login, password)
            port = free_port()
            name = "fh-qr-" + str(account_id) + "-" + secrets.token_hex(4)
            try:
                launch_interactive(name, src, port)
                if not await_qr_approval(name, port, password, bot, steam_id, timeout):
                    raise RuntimeError("Account authentication did not complete")
            finally:
                stop_temporary(name)
            # Re-read the current consent after potentially 3 minutes of QR wait.
            mod.context(friends_db, asf_config, account_id)
            transfer_native_session(
                bot=bot, steam_id=steam_id,
                source_dir=src, target_dir=Path(asf_config),
            )
            return {"bot":bot, "steam_id":steam_id, "ready":True,
                    "applied":True, "connected_verified":True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--friends-db", required=True)
    parser.add_argument("--asf-config", required=True)
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Start interactive QR and the guarded native ASF transfer")
    args = parser.parse_args()
    result = bootstrap_account(
        friends_db=args.friends_db, asf_config=args.asf_config,
        account_id=args.account_id, dry_run=not args.apply,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
