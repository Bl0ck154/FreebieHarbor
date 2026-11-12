#!/usr/bin/env python3
"""Privileged, local-socket-only Steam QR onboarding for FreebieHarbor Friends.

The public Friends portal never receives Docker access, the ASF IPC password,
or Steam session tokens. One temporary ASF process handles one account's QR;
after native SteamID verification, only its ASF-generated bot files are moved
into the separate Friends ASF instance with a guarded rollback.

Requires an independent offline review before enabling on an internet-facing
Friends portal. No Steam account has been authenticated in automated tests.
"""
import base64
import fcntl
import hmac
import http.server
import importlib.util
import io
import json
import os
import pty
import secrets
import select
import socket
import socketserver
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "native_asf_bootstrap", ROOT/"tools/steam-qr/native_bootstrap.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
ACCOUNT_DB = Path(os.environ.get("QR_FRIENDS_DB", "/var/lib/docker/volumes/freebieharbor-friends_friends-data/_data/friends.db"))
ASF_CONFIG = Path(os.environ.get("QR_ASF_CONFIG", "/srv/state/freebieharbor-friends-asf/config"))
WORK_DIR = Path(os.environ.get("QR_WORK_DIR", "/srv/state/freebieharbor-friends-asf/qr-work"))
SOCKET = Path(os.environ.get("QR_SOCKET", "/run/freebieharbor-friends-qr/broker.sock"))
AUTH_FILE = Path(os.environ.get("QR_AUTH_FILE", "/run/freebieharbor-friends-qr/token"))
MAX_TIME = 190
STATES = {"starting", "qr", "transferring", "connected", "failed"}


def valid_owner(user_id, account_id):
    if not isinstance(user_id, int) or not isinstance(account_id, int):
        return False
    if user_id < 1 or account_id < 1:
        return False
    from contextlib import closing
    with closing(sqlite3.connect(ACCOUNT_DB.resolve().as_uri()+"?mode=ro",uri=True,timeout=3)) as db:
        return bool(db.execute("""
            SELECT 1 FROM steam_accounts a JOIN users u ON u.id=a.user_id
            WHERE a.id=? AND a.user_id=? AND a.requested=1
              AND u.disabled=0 AND a.steam_id IS NOT NULL AND a.login IS NOT NULL
        """,(account_id,user_id)).fetchone())


def svg_qr(url):
    # Pure Python Segno is an optional dependency of the privileged broker.
    if not (url.startswith("https://") and len(url)<1024):
        raise ValueError("Unexpected Steam QR challenge URL")
    import segno
    qr=segno.make(url,error="m")
    out=io.BytesIO()
    qr.save(out,kind="svg",scale=4,xmldecl=False)
    return base64.b64encode(out.getvalue()).decode("ascii")


def is_live_account(account_id):
    # Use ASF's actual connected SteamID rather than trusting a remembered QR.
    try:
        with sqlite3.connect(ACCOUNT_DB.resolve().as_uri()+"?mode=ro",uri=True,timeout=3) as db:
            row=db.execute("SELECT steam_id FROM steam_accounts WHERE id=?",(account_id,)).fetchone()
        if row is None:
            return False
        password=json.loads((ASF_CONFIG/"ASF.json").read_text())["IPCPassword"]
        state=native.asf_bot_response(1246,password,"fh_account_"+str(account_id))
        return native.verified_identity(state,row[0])
    except (OSError,ValueError,KeyError,sqlite3.Error):
        return False


class Engine:
    def __init__(self):
        self.lock=threading.Lock()
        self.active=None
        self.items={}

    def start(self,user_id,account_id):
        if not valid_owner(user_id,account_id):
            raise PermissionError("Account not owned or no active consent")
        with self.lock:
            if self.active is not None:
                task=self.items.get(self.active)
                if task and task["phase"] not in ("connected","failed"):
                    raise RuntimeError("Another QR onboarding session is in progress")
            key=secrets.token_urlsafe(24)
            self.items[key]={"user_id":user_id,"account_id":account_id,
                             "phase":"starting","svg":None,"reason":None,
                             "created":time.monotonic()}
            self.active=key
            threading.Thread(target=self._work,args=(key,),daemon=True).start()
            return key

    def _set(self,key,**fields):
        with self.lock:
            if key in self.items:
                self.items[key].update(fields)

    def status(self,user_id,account_id):
        if not valid_owner(user_id,account_id):
            raise PermissionError("Account not owned")
        if is_live_account(account_id):
            return {"phase":"connected"}
        with self.lock:
            data=[v for v in self.items.values()
                  if v["user_id"]==user_id and v["account_id"]==account_id]
            if not data:
                return {"phase":"not_started"}
            record=data[-1]
            result={"phase":record["phase"],"svg":record.get("svg")}
            if record["phase"]=="failed":
                result["reason"]=record.get("reason","Onboarding failed")
            return result

    def _work(self,key):
        with self.lock:
            user_id=self.items[key]["user_id"]
            account_id=self.items[key]["account_id"]
        name=None
        attach=None
        master=None
        try:
            # Stage any just-requested bot immediately; the periodic timer is
            # only a fallback and users need not wait five minutes.
            from importlib.util import spec_from_file_location, module_from_spec
            ps=spec_from_file_location("friends_provisioner",ROOT/"tools/provision_friends_asf.py")
            pm=module_from_spec(ps)
            ps.loader.exec_module(pm)
            pm.provision(ACCOUNT_DB,ASF_CONFIG,apply=True,limit=5)
            bot,steam_id,login,_,_=native.mod.context(ACCOUNT_DB,ASF_CONFIG,account_id)
            if not valid_owner(user_id,account_id):
                raise PermissionError("Owner withdrew the Steam setup request")
            WORK_DIR.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="fh-auth-",dir=WORK_DIR) as tmp:
                folder=Path(tmp)
                folder.chmod(0o700)
                ipc_password=secrets.token_urlsafe(48)
                config=native.prepare_bootstrap(folder,bot,login,ipc_password)
                port=native.free_port()
                name="fh-qr-"+str(account_id)+"-"+secrets.token_hex(4)
                native.launch_interactive(name,config,port)
                master,slave=pty.openpty()
                attach=subprocess.Popen(
                    ["docker","attach","--sig-proxy=false",name],
                    stdin=slave,stdout=slave,stderr=slave,
                )
                os.close(slave)
                sent=0
                last_url=None
                deadline=time.monotonic()+MAX_TIME
                while time.monotonic()<deadline:
                    # Drain console output so ASF never blocks writing its QR.
                    if select.select([master],[],[],0)[0]:
                        try:
                            os.read(master,16384)  # Never log QR URLs or tokens
                        except OSError:
                            pass
                    state=native.asf_bot_response(port,ipc_password,bot)
                    if native.verified_identity(state,steam_id):
                        break
                    if isinstance(state,dict):
                        current_id=str(state.get("SteamID","0"))
                        if current_id not in ("0","None",steam_id):
                            raise PermissionError("Steam account identity mismatch")
                        url=state.get("QrChallengeURL")
                        if url and url!=last_url:
                            self._set(key,phase="qr",svg=svg_qr(str(url)))
                            last_url=url
                        elif not url and sent<3 and time.monotonic()+10<deadline:
                            # ASF expects Y on its private console to begin QR.
                            # A bounded input is safe; never enter passwords.
                            if attach.poll() is None:
                                os.write(master,b"Y\n")
                                sent+=1
                    if attach.poll() is not None:
                        raise RuntimeError("Temporary ASF console stopped")
                    time.sleep(2)
                else:
                    raise TimeoutError("Steam QR challenge timed out")
                self._set(key,phase="transferring",svg=None)
                if not valid_owner(user_id,account_id):
                    raise PermissionError("Steam owner cancelled onboarding")
                native.mod.context(ACCOUNT_DB,ASF_CONFIG,account_id)
                # Ensure the temporary ASF is stopped before its native files
                # are read or moved into the separate Friends ASF.
                if attach and attach.poll() is None:
                    attach.terminate()
                    attach.wait(timeout=3)
                if master is not None:
                    os.close(master)
                    master=None
                native.stop_temporary(name)
                name=None
                with (ASF_CONFIG / ".friends-qr-onboarding.lock").open("a+b") as fd:
                    fcntl.flock(fd.fileno(),fcntl.LOCK_EX)
                    native.mod.context(ACCOUNT_DB,ASF_CONFIG,account_id)
                    if not valid_owner(user_id,account_id):
                        raise PermissionError("Onboarding consent withdrawn")
                    native.transfer_native_session(
                        bot=bot,steam_id=steam_id,source_dir=config,target_dir=ASF_CONFIG)
            self._set(key,phase="connected",svg=None)
        except BaseException as exc:
            # Do not put credentials, raw exception paths or QR links in portal.
            self._set(key,phase="failed",svg=None,reason=type(exc).__name__)
        finally:
            if attach and attach.poll() is None:
                attach.terminate()
                try: attach.wait(timeout=3)
                except subprocess.TimeoutExpired: attach.kill()
            if master is not None:
                os.close(master)
            if name:
                native.stop_temporary(name)


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads=True
    allow_reuse_address=True


class Handler(http.server.BaseHTTPRequestHandler):
    engine=Engine()

    def log_message(self, fmt, *args):
        # URLs and tokens can be sensitive: never log request paths or bodies.
        return

    def reply(self,code,payload):
        body=json.dumps(payload,separators=(",",":")).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        supplied=self.headers.get("Authorization","")
        try:
            expected=AUTH_FILE.read_text().strip()
        except OSError:
            return False
        return bool(expected) and hmac.compare_digest(supplied,expected)

    def do_GET(self):
        if not self.authorized():
            return self.reply(403,{"error":"unauthorized"})
        parsed=urlsplit(self.path)
        if parsed.path=="/health":
            return self.reply(200,{"ok":True})
        if parsed.path!="/status":
            return self.reply(404,{"error":"unknown"})
        try:
            params=parse_qs(parsed.query,max_num_fields=3)
            user_id=int(params["user_id"][0])
            account_id=int(params["account_id"][0])
            status=self.engine.status(user_id,account_id)
            return self.reply(200,status)
        except (ValueError,KeyError,IndexError,PermissionError):
            return self.reply(403,{"error":"not allowed"})
        except sqlite3.Error:
            return self.reply(503,{"error":"unavailable"})

    def do_POST(self):
        if not self.authorized():
            return self.reply(403,{"error":"unauthorized"})
        if self.path!="/start":
            return self.reply(404,{"error":"unknown"})
        try:
            size=int(self.headers.get("Content-Length","0"))
            if not 1<=size<=4096:
                return self.reply(413,{"error":"invalid request"})
            data=json.loads(self.rfile.read(size))
            account_id=data["account_id"]
            user_id=data["user_id"]
            if type(account_id) is not int or type(user_id) is not int:
                return self.reply(400,{"error":"invalid account"})
            self.engine.start(user_id,account_id)
            return self.reply(202,{"phase":"starting"})
        except (ValueError,KeyError,TypeError,PermissionError):
            return self.reply(403,{"error":"not allowed"})
        except RuntimeError:
            return self.reply(409,{"error":"qr session busy"})
        except sqlite3.Error:
            return self.reply(503,{"error":"unavailable"})


def cleanup_stale_bootstraps():
    # A crashed broker must not leave a temporary Steam QR process alive.
    try:
        names=subprocess.check_output([
            "docker","ps","--filter","label=freebieharbor.qr-bootstrap=1",
            "--format","{{.Names}}"],text=True,timeout=8).splitlines()
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
        return
    for name in names:
        if name.startswith("fh-qr-"):
            native.stop_temporary(name)


def main():
    if os.geteuid()!=0:
        raise PermissionError("This service owns only the dedicated Friends ASF Docker process")
    if not AUTH_FILE.exists() or AUTH_FILE.stat().st_mode & 0o077:
        raise PermissionError("Broker authentication file must be private")
    cleanup_stale_bootstraps()
    SOCKET.parent.mkdir(parents=True,exist_ok=True)
    if SOCKET.exists():
        if not SOCKET.is_socket():
            raise ValueError("Broker socket path is not a socket")
        SOCKET.unlink()
    with UnixHTTPServer(str(SOCKET),Handler) as server:
        os.chmod(SOCKET,0o660)
        # A narrow deployment-created group allows only the Friends app to connect.
        gid=os.environ.get("QR_SOCKET_GID")
        if gid:
            os.chown(SOCKET,0,int(gid))
        try:
            server.serve_forever(poll_interval=0.4)
        finally:
            SOCKET.unlink(missing_ok=True)


if __name__=="__main__":
    main()
