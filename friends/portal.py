#!/usr/bin/env python3
"""Optional invite-only FreebieHarbor portal. No Steam sessions or game-claim actions."""
import hashlib
import json
import hmac
import html
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import cookies
import http.client
import socket
from contextlib import closing
from urllib.request import Request, urlopen
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode

FRIENDS_DB = Path(os.getenv("FRIENDS_DB", "./friends-data/friends.db"))
HUB_DB = Path(os.getenv("HUB_DB", "./demo-data/giveaways.db"))
HOST = os.getenv("FRIENDS_HOST", "127.0.0.1")
PORT = int(os.getenv("FRIENDS_PORT", "18084"))
PUBLIC_ORIGIN = os.getenv("FRIENDS_PUBLIC_ORIGIN", "http://127.0.0.1:18084").rstrip("/")
COOKIE_SECURE = os.getenv("FRIENDS_COOKIE_SECURE", "true").lower() not in ("false", "0")
QR_SOCKET = os.getenv("FRIENDS_QR_BROKER_SOCKET", "")
QR_TOKEN_FILE = os.getenv("FRIENDS_QR_AUTH_FILE", "")
QR_ENABLED = bool(QR_SOCKET and QR_TOKEN_FILE)
SESSION_SECONDS = 86400 * 7
LOCK = threading.RLock()
FAILED_LOGINS = {}
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,23}$")
MAX_STEAM_ACCOUNTS_PER_USER = 5
MAX_STEAM_ACCOUNTS_TOTAL = 10


class UnixBrokerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(4)
        self.sock.connect(QR_SOCKET)


def qr_broker_call(method, path, values=None):
    if not QR_ENABLED:
        raise RuntimeError("Steam QR login is not configured")
    secret=Path(QR_TOKEN_FILE).read_text().strip()
    if len(secret)<32:
        raise RuntimeError("Steam QR authentication is unavailable")
    payload=json.dumps(values).encode() if values is not None else None
    conn=UnixBrokerConnection("localhost",timeout=5)
    try:
        conn.request(method,path,body=payload,headers={
            "Authorization":secret,
            "Content-Type":"application/json",
        })
        response=conn.getresponse()
        body=response.read(150000)
        if response.status not in (200,202):
            raise RuntimeError("Steam QR service cannot process this request")
        return json.loads(body)
    finally:
        conn.close()


def database():
    conn = sqlite3.connect(FRIENDS_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    FRIENDS_DB.parent.mkdir(parents=True, exist_ok=True)
    with LOCK, database() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            disabled INTEGER NOT NULL DEFAULT 0,
            is_admin INTEGER NOT NULL DEFAULT 0,
            steam_id TEXT,
            steam_login TEXT,
            steam_automation INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS invites (
            token_hash TEXT PRIMARY KEY,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            claimed_by INTEGER REFERENCES users(id),
            is_admin INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS offer_states (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            offer_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('new','claimed','ignored')),
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(user_id,offer_id)
        );
        CREATE TABLE IF NOT EXISTS steam_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            steam_id TEXT NOT NULL UNIQUE,
            login TEXT,
            requested INTEGER NOT NULL DEFAULT 0 CHECK(requested IN (0,1)),
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS steam_accounts_owner ON steam_accounts(user_id);
        CREATE TABLE IF NOT EXISTS steam_link_requests (
            state_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS steam_openid_nonces (
            nonce_hash TEXT PRIMARY KEY,
            expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        """)
        for name in ("users","invites"):
            cols = {row[1] for row in conn.execute("PRAGMA table_info("+name+")")}
            if "is_admin" not in cols:
                conn.execute("ALTER TABLE "+name+" ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        if "steam_id" not in {row[1] for row in conn.execute("PRAGMA table_info(users)")}:
            conn.execute("ALTER TABLE users ADD COLUMN steam_id TEXT")
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "steam_login" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN steam_login TEXT")
        if "steam_automation" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN steam_automation INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_steam_id ON users(steam_id)")
        # One-time migration: the old 1-account profile fields become the first
        # independently addressable Steam account. Re-running is idempotent.
        for legacy in conn.execute("""
            SELECT id,steam_id,steam_login,steam_automation FROM users
            WHERE steam_id IS NOT NULL AND steam_id != ''
        """).fetchall():
            conn.execute("""
                INSERT OR IGNORE INTO steam_accounts(user_id,steam_id,login,requested,created_at)
                VALUES(?,?,?,?,?)
            """, (legacy["id"],legacy["steam_id"],legacy["steam_login"],
                  1 if legacy["steam_automation"] and legacy["steam_login"] else 0, int(time.time())))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def create_invite(ttl_hours=48, is_admin=False):
    if not 1 <= int(ttl_hours) <= 168:
        raise ValueError("Invite expiry must be 1-168 hours")
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with LOCK, database() as conn:
        conn.execute(
            "INSERT INTO invites(token_hash,created_at,expires_at,claimed_by,is_admin) VALUES(?,?,?,NULL,?)",
            (digest(token), now, now+int(ttl_hours)*3600, 1 if is_admin else 0),
        )
    return token


def register(invite_token, username, password):
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username) or not 12 <= len(password) <= 128:
        return False
    salt = secrets.token_hex(16)
    hashed = hash_password(password, salt)
    now = int(time.time())
    with LOCK, database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        invitation = conn.execute(
            "SELECT claimed_by,expires_at,is_admin FROM invites WHERE token_hash=?",
            (digest(invite_token),),
        ).fetchone()
        if not invitation or invitation["claimed_by"] is not None or invitation["expires_at"] < now:
            return False
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,salt,password_hash,created_at,is_admin) VALUES(?,?,?,?,?)",
                (username,salt,hashed,now,invitation["is_admin"]),
            )
            conn.execute(
                "UPDATE invites SET claimed_by=? WHERE token_hash=? AND claimed_by IS NULL",
                (cursor.lastrowid,digest(invite_token)),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def login(username, password):
    username = username.strip().lower()
    if len(username) > 24 or len(password) > 128:
        return None
    with LOCK, database() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=? AND disabled=0", (username,)).fetchone()
        # Fixed dummy comparison prevents a trivial nonexistent-account timing shortcut.
        salt = row["salt"] if row else "00"*16
        expected = row["password_hash"] if row else "00"*64
        valid = hmac.compare_digest(hash_password(password, salt), expected)
        if not row or not valid:
            return None
        now = int(time.time())
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                     (digest(raw),row["id"],csrf,now+SESSION_SECONDS))
        return raw,csrf


def get_session(raw):
    if not raw or len(raw) > 200:
        return None
    with LOCK, database() as conn:
        return conn.execute("""
            SELECT users.id,users.username,users.is_admin,sessions.csrf,sessions.expires_at
            FROM sessions JOIN users ON users.id=sessions.user_id
            WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.disabled=0
        """, (digest(raw),int(time.time()))).fetchone()


def steam_accounts(user_id):
    with LOCK, database() as conn:
        return conn.execute(
            "SELECT id,steam_id,login,requested FROM steam_accounts WHERE user_id=? ORDER BY id",
            (user_id,),
        ).fetchall()


def request_steam_activation(user_id, account_id, login, consent):
    if not consent or not re.fullmatch(r"[A-Za-z0-9_]{3,64}", login):
        return False
    with LOCK, database() as conn:
        updated = conn.execute(
            "UPDATE steam_accounts SET login=?,requested=1 WHERE id=? AND user_id=?",
            (login,account_id,user_id),
        )
        return updated.rowcount == 1


def cancel_steam_activation(user_id, account_id):
    with LOCK, database() as conn:
        changed = conn.execute(
            "UPDATE steam_accounts SET requested=0 WHERE id=? AND user_id=?",
            (account_id,user_id),
        )
        return changed.rowcount == 1


def add_verified_steam_account(user_id, steam_id):
    if not re.fullmatch(r"7656119[0-9]{10}", steam_id):
        return False
    with LOCK, database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT user_id FROM steam_accounts WHERE steam_id=?", (steam_id,),
        ).fetchone()
        if previous:
            return previous["user_id"] == user_id
        personal_count = conn.execute(
            "SELECT count(*) FROM steam_accounts WHERE user_id=?", (user_id,),
        ).fetchone()[0]
        overall_count = conn.execute("SELECT count(*) FROM steam_accounts").fetchone()[0]
        if personal_count >= MAX_STEAM_ACCOUNTS_PER_USER or overall_count >= MAX_STEAM_ACCOUNTS_TOTAL:
            return False
        conn.execute(
            "INSERT INTO steam_accounts(user_id,steam_id,created_at) VALUES(?,?,?)",
            (user_id,steam_id,int(time.time())),
        )
        return True


STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"
STEAM_OPENID_CLAIM = "http://specs.openid.net/auth/2.0/identifier_select"


def steam_link_begin(user_id):
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 600
    with LOCK, database() as conn:
        conn.execute("DELETE FROM steam_link_requests WHERE expires_at < ?", (int(time.time()),))
        conn.execute("INSERT INTO steam_link_requests VALUES(?,?,?)", (digest(token),user_id,expires))
    return_to = PUBLIC_ORIGIN + "/steam/callback?state=" + token
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": PUBLIC_ORIGIN + "/",
        "openid.identity": STEAM_OPENID_CLAIM,
        "openid.claimed_id": STEAM_OPENID_CLAIM,
    }
    return STEAM_OPENID_ENDPOINT + "?" + urlencode(params)


def verify_steam_openid(user_id, values):
    """Verify signed OpenID assertion with Valve; never trust callback contents alone."""
    if any(len(items)!=1 for items in values.values()):
        return False
    get = lambda k: values.get(k, [""])[0]
    token = get("state")
    if not 32 <= len(token) <= 128 or not re.fullmatch(r"[A-Za-z0-9_-]+",token):
        return False
    if get("openid.mode") != "id_res":
        return False
    if get("openid.op_endpoint") != STEAM_OPENID_ENDPOINT:
        return False
    expected_return = PUBLIC_ORIGIN + "/steam/callback?state=" + token
    if get("openid.return_to") != expected_return:
        return False
    claim = get("openid.claimed_id")
    match = re.fullmatch(r"https?://steamcommunity[.]com/openid/id/(7656119[0-9]{10})", claim)
    if not match or get("openid.identity") != claim:
        return False
    signed = set(get("openid.signed").split(","))
    if not {"return_to", "claimed_id", "identity", "response_nonce", "op_endpoint"} <= signed:
        return False
    nonce = get("openid.response_nonce")
    if len(nonce) < 24 or len(nonce) > 180 or not nonce.endswith(tuple("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")):
        return False
    try:
        stamp = datetime.strptime(nonce[:20], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if abs(time.time() - stamp.timestamp()) > 600:
            return False
    except ValueError:
        return False
    now = int(time.time())
    with LOCK, database() as conn:
        state = conn.execute("SELECT user_id,expires_at FROM steam_link_requests WHERE state_hash=?", (digest(token),)).fetchone()
        if not state or state["user_id"] != user_id or state["expires_at"] < now:
            return False
        if conn.execute("SELECT 1 FROM steam_openid_nonces WHERE nonce_hash=?", (digest(nonce),)).fetchone():
            return False
    params = {k:v[0] for k,v in values.items() if k.startswith("openid.")}
    params["openid.mode"] = "check_authentication"
    try:
        request = Request(STEAM_OPENID_ENDPOINT,
                          data=urlencode(params).encode("ascii"),
                          headers={"Content-Type":"application/x-www-form-urlencoded"},
                          method="POST")
        # Steam's endpoint is hard-coded HTTPS; no user-controlled destination.
        with urlopen(request,timeout=8) as response:
            if response.status != 200:
                return False
            reply = response.read(4096).decode("utf-8","replace")
        if "is_valid:true" not in reply.splitlines():
            return False
    except (OSError,ValueError):
        return False
    with LOCK, database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT user_id,expires_at FROM steam_link_requests WHERE state_hash=?", (digest(token),)).fetchone()
        if not state or state["user_id"] != user_id or state["expires_at"] < int(time.time()):
            return False
        conn.execute("DELETE FROM steam_openid_nonces WHERE expires_at < ?",(int(time.time()),))
        existing = conn.execute(
            "SELECT user_id FROM steam_accounts WHERE steam_id=?", (match.group(1),),
        ).fetchone()
        if existing is not None and existing["user_id"] != user_id:
            return False
        if existing is None:
            count = conn.execute("SELECT count(*) FROM steam_accounts WHERE user_id=?", (user_id,)).fetchone()[0]
            total = conn.execute("SELECT count(*) FROM steam_accounts").fetchone()[0]
            if count >= MAX_STEAM_ACCOUNTS_PER_USER or total >= MAX_STEAM_ACCOUNTS_TOTAL:
                return False
        try:
            conn.execute("INSERT INTO steam_openid_nonces VALUES(?,?)", (digest(nonce),now+1200))
            if existing is None:
                conn.execute(
                    "INSERT INTO steam_accounts(user_id,steam_id,created_at) VALUES(?,?,?)",
                    (user_id,match.group(1),now),
                )
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        conn.execute("DELETE FROM steam_link_requests WHERE state_hash=?", (digest(token),))
        return True


def logout(raw):
    if not raw:
        return
    with LOCK, database() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest(raw),))


def shared_offers(user_id, text="", limit=70):
    """Reads PUBLIC offer fields only from the hub's separate SQLite database."""
    if not HUB_DB.exists():
        return []
    try:
        conn = sqlite3.connect(HUB_DB.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        with conn:
            if text:
                rows = conn.execute("""
                    SELECT offer_id,title,source,url,platform,offer_type,ends_at
                    FROM external_offers
                    WHERE active=1 AND source IN ('gamerpower','freetokeep','ggdeals','alienware')
                      AND (title LIKE ? OR provider LIKE ?)
                    ORDER BY first_seen DESC LIMIT ?
                """, ("%"+text[:70]+"%", "%"+text[:70]+"%", limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT offer_id,title,source,url,platform,offer_type,ends_at
                    FROM external_offers
                    WHERE active=1 AND source IN ('gamerpower','freetokeep','ggdeals','alienware')
                    ORDER BY first_seen DESC LIMIT ?
                """, (limit,)).fetchall()
        conn.close()
    except (sqlite3.Error, OSError):
        return []
    with LOCK, database() as conn:
        states = {r["offer_id"]:r["state"] for r in conn.execute(
            "SELECT offer_id,state FROM offer_states WHERE user_id=?", (user_id,)
        )}
    return [{**dict(r), "state":states.get(r["offer_id"],"new")} for r in rows]


def update_offer_state(user_id, offer_id, new_state):
    if new_state not in ("new","claimed","ignored") or len(offer_id)>200 or not offer_id:
        return False
    # Only public catalog rows can be updated; no cross-user action or arbitrary ID.
    if not HUB_DB.exists():
        return False
    try:
        with closing(sqlite3.connect(HUB_DB.resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
            found = conn.execute(
                "SELECT 1 FROM external_offers WHERE offer_id=? AND source IN "
                "('gamerpower','freetokeep','ggdeals','alienware')", (offer_id,)
            ).fetchone()
    except (sqlite3.Error,OSError):
        return False
    if not found:
        return False
    with LOCK, database() as conn:
        conn.execute("""
            INSERT INTO offer_states(user_id,offer_id,state,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id,offer_id)
            DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at
        """, (user_id,offer_id,new_state,int(time.time())))
    return True


def safe_link(url):
    try:
        p = urlsplit(url)
        return url if p.scheme == "https" and p.hostname and not p.username and not p.password else "#"
    except ValueError:
        return "#"


def page(title, body, username=None, is_admin=False):
    signed = bool(username)
    nav = ('<a href="/offers">Games</a><a href="/account">Account</a>'
           '<form method="post" action="/logout"><button>Sign out</button></form>') if signed else ''
    if signed and is_admin:
        nav = '<a href="/admin">Admin</a>' + nav
    if signed:
        nav = '<a href="/steam-setup">Steam setup</a>' + nav
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · FreebieHarbor</title>
<style>
:root{{color-scheme:dark;--bg:#0b1426;--panel:#182840;--line:#37506c;--text:#eef6ff;--accent:#79d8b6}}
*{{box-sizing:border-box}}body{{background:var(--bg);color:var(--text);font:16px/1.55 system-ui;margin:0}}
header,main{{max-width:1050px;margin:auto;padding:22px}}header{{display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:1px solid var(--line)}}
a{{color:#8bc4ff}}nav{{display:flex;gap:15px;align-items:center}}nav form{{margin:0}}
h1{{font-size:clamp(24px,4vw,36px)}}.muted{{color:#b4c5d7}}.card{{background:var(--panel);border:1px solid var(--line);padding:20px;border-radius:14px;margin:14px 0}}
input,button{{font:inherit;padding:10px;border-radius:8px;border:1px solid #577090}}input{{background:#101d32;color:var(--text);max-width:100%}}
button{{cursor:pointer;background:#7bb6fa;color:#07172a;font-weight:700}}label{{display:block;margin:12px 0 5px}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
.offer{{padding:15px;display:flex;flex-direction:column;gap:6px}}.offer form{{display:flex;gap:8px;flex-wrap:wrap;margin-top:auto}}.offer p{{margin:0}}
@media(max-width:650px){{.grid{{grid-template-columns:1fr}}header{{flex-wrap:wrap}}}}
</style></head><body><header><b>⚓ FreebieHarbor · Friends</b><nav>{nav}</nav></header><main>{body}</main></body></html>"""


def hidden(name, value):
    return f'<input type="hidden" name="{name}" value="{html.escape(value,quote=True)}">'


def admin_content(user, token=None):
    if not user["is_admin"]:
        return ""
    with LOCK, database() as db:
        members = list(db.execute("SELECT username,disabled FROM users ORDER BY username"))
    listing = ""
    for member in members:
        name = member["username"]
        listing += "<div class='card'><b>"+html.escape(name)+"</b>"
        if member["disabled"]:
            listing += " (disabled)"
        elif name != user["username"]:
            listing += "<form method='post' action='/admin/disable'>"+hidden("csrf",user["csrf"])+hidden("username",name)+"<button>Disable</button></form>"
        listing += "</div>"
    created = ("<div class='card'><p>One-time invitation code:</p><code>"+html.escape(token)+"</code><p>Signup page: "+html.escape(PUBLIC_ORIGIN+"/signup")+"</p></div>") if token else ""
    return "<h1>Admin</h1><p>Create a private invite, valid for 48 hours.</p><form method='post' action='/admin/invite'>"+hidden("csrf",user["csrf"])+"<button>Create invite</button></form>"+created+"<h2>Members</h2>"+listing


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log invite tokens, cookies, passwords, URL parameters or raw bodies.
        print("Friends HTTP:",self.command, "status", args[1] if len(args)>1 else "event", flush=True)

    def respond(self, code, body, content_type="text/html; charset=utf-8", cookie=None):
        data = body.encode() if isinstance(body,str) else body
        self.send_response(code)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(data)))
        for key,value in (
            ("Cache-Control","no-store"),
            ("X-Content-Type-Options","nosniff"),
            ("X-Frame-Options","DENY"),
            ("Referrer-Policy","no-referrer"),
            ("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; img-src data: 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
        ):
            self.send_header(key,value)
        if cookie:
            self.send_header("Set-Cookie",cookie)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path, cookie=None):
        self.send_response(303)
        self.send_header("Location",path)
        self.send_header("Cache-Control","no-store")
        if cookie:
            self.send_header("Set-Cookie",cookie)
        self.end_headers()

    def raw_session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie","")[:1024])
            return jar["fh_friend"].value if "fh_friend" in jar else None
        except cookies.CookieError:
            return None

    def session(self):
        return get_session(self.raw_session())

    def host_ok(self):
        try:
            origin = urlsplit(PUBLIC_ORIGIN)
            return self.headers.get("Host","").lower() == origin.netloc.lower()
        except ValueError:
            return False

    def origin_ok(self):
        return self.host_ok() and self.headers.get("Origin","").rstrip("/") == PUBLIC_ORIGIN

    def do_GET(self):
        if not self.host_ok():
            return self.respond(400,"Invalid Host","text/plain")
        path = urlsplit(self.path)
        if path.path == "/health":
            return self.respond(200,'{"ok":true,"mode":"friends"}',"application/json")
        user = self.session()
        if path.path == "/login" and not user:
            return self.respond(200,page("Login","""<h1>Sign in</h1>
<form method="post" action="/login"><label>Username</label><input name="username" required autocomplete="username">
<label>Password</label><input name="password" type="password" required autocomplete="current-password">
<p><button>Sign in</button></p></form><p class="muted">Accounts are invitation-only.</p>"""))
        if path.path == "/signup" and not user:
            form = """<h1>Join FreebieHarbor</h1><p class="muted">Paste your one-time invite code below. It is never included in the URL.</p>
<form method="post" action="/signup">
<label>Invite code</label><input name="invite" type="password" required autocomplete="off" maxlength="180">
<label>Username</label><input name="username" required maxlength="24" autocomplete="username">
<label>Password (12 characters minimum)</label><input type="password" name="password" minlength="12" required autocomplete="new-password">
<p><button>Create account</button></p></form>"""
            return self.respond(200,page("Join",form))
        if not user:
            return self.redirect("/login")
        if path.path == "/steam/callback":
            if len(path.query)>4096:
                return self.respond(403,"Invalid Steam callback","text/plain")
            try:
                query = parse_qs(path.query,keep_blank_values=True,max_num_fields=40)
            except ValueError:
                return self.respond(403,"Invalid Steam callback","text/plain")
            if not verify_steam_openid(user["id"],query):
                return self.respond(403,"Steam identity verification failed","text/plain")
            return self.redirect("/account")
        if path.path in ("/","/offers"):
            query = parse_qs(path.query)
            text = query.get("q",[""])[0][:70]
            offers = shared_offers(user["id"],text)
            cards = []
            for offer in offers:
                title = html.escape(str(offer["title"] or "Untitled"))
                state = offer["state"]
                href = html.escape(safe_link(str(offer["url"])),quote=True)
                buttons = "".join(
                    '<button name="state" value="'+value+'">'+label+'</button>'
                    for value,label in (("claimed","Claimed"),("ignored","Ignore"),("new","Reset"))
                )
                cards.append(f'''<article class="card offer"><b>{title}</b><span class="muted">{html.escape(offer["source"])} · {html.escape(state)}</span>
<a href="{href}" target="_blank" rel="noopener noreferrer">Open offer</a>
<form method="post" action="/state">{hidden('csrf',user["csrf"])}{hidden('offer_id',offer["offer_id"])}{buttons}</form></article>''')
            body = (f'<h1>Free game offers</h1><p class="muted">Your claimed and ignored states stay private. '
                    'Free-to-keep offers require manual redemption in Steam; no Steam account is connected.</p>'
                    f'<form method="get" action="/offers"><input name="q" placeholder="Search games" value="{html.escape(text,quote=True)}">'
                    '<button>Search</button></form><div class="grid">'+''.join(cards)+'</div>')
            return self.respond(200,page("Games",body,user["username"],bool(user["is_admin"])))
        if path.path == "/admin":
            if not user["is_admin"]:
                return self.respond(403,"Admin access required","text/plain")
            return self.respond(200,page("Admin",admin_content(user),user["username"],True))
        if path.path == "/steam/qr":
            if not QR_ENABLED:
                return self.respond(404,"QR login is not configured","text/plain")
            try:
                account_id=int(parse_qs(path.query).get("account_id",[""])[0])
            except ValueError:
                return self.respond(400,"Invalid account","text/plain")
            account=next((a for a in steam_accounts(user["id"]) if a["id"]==account_id and a["requested"]),None)
            if account is None:
                return self.respond(403,"Account not authorized","text/plain")
            try:
                status=qr_broker_call("GET","/status?"+urlencode({"user_id":user["id"],"account_id":account_id}))
            except (OSError,RuntimeError,ValueError):
                return self.respond(503,page("Steam QR","<h1>Steam QR is currently unavailable</h1><a href='/steam-setup'>Back to accounts</a>",user["username"]))
            phase=status.get("phase","unknown")
            qr=status.get("svg")
            picture=('<img width="290" alt="Steam Mobile QR challenge" src="data:image/svg+xml;base64,'+qr+'">') if (
                phase=="qr" and isinstance(qr,str) and len(qr)<110000 and re.fullmatch(r"[a-zA-Z0-9+/=]+",qr)
            ) else ""
            explanation={
                "not_started":"No QR session has started.",
                "starting":"Starting a temporary ASF QR session...",
                "qr":"Open Steam Mobile, scan the QR and approve this account only.",
                "transferring":"Steam login confirmed. Verifying the shared ASF connection...",
                "connected":"Steam identity verified and ASF session connected.",
                "failed":"QR login failed or expired. Try again from Steam setup.",
            }.get(phase,"Checking Steam QR status...")
            doc=page("Steam QR",'<h1>Steam QR onboarding</h1>'
                '<p><b>SteamID '+html.escape(account["steam_id"])+'</b></p>'
                '<p>'+explanation+'</p>'+picture+
                '<p><a href="/steam-setup">Back to your Steam accounts</a></p>',user["username"],bool(user["is_admin"]))
            if phase not in ("connected","failed"):
                doc=doc.replace("</head>",'<meta http-equiv="refresh" content="5"></head>',1)
            return self.respond(200,doc)
        if path.path == "/steam-setup":
            items = steam_accounts(user["id"])
            cards = []
            for account in items:
                state = ("Awaiting Steam/ASF login — activation NOT active" if account["requested"]
                         else "Not requested")
                login = html.escape(account["login"] or "",quote=True)
                qr_button=(
                    '<form method="post" action="/steam/qr/start">'+
                    hidden("csrf",user["csrf"])+hidden("account_id",str(account["id"]))+
                    '<button>Authorize through Steam QR</button></form>'
                ) if QR_ENABLED and account["requested"] else ""
                cards.append(
                    '<section class="card"><b>SteamID '+html.escape(account["steam_id"])+'</b>'
                    '<p class="muted">'+state+'</p>'
                    '<form method="post" action="/steam/automation">'+
                    hidden("csrf",user["csrf"])+hidden("account_id",str(account["id"]))+
                    '<label>Steam login name (not password)</label>'
                    '<input name="steam_login" value="'+login+'" maxlength="64" required>'
                    '<label><input type="checkbox" name="consent" value="yes" required>'
                    ' I agree to request a dedicated ASF bot for this Steam account.</label>'
                    '<p><button>Request connection</button></p></form>'
                    '<form method="post" action="/steam/automation">'+
                    hidden("csrf",user["csrf"])+hidden("account_id",str(account["id"]))+
                    '<button name="action" value="disable">Cancel request</button></form>'+qr_button+
                    '</section>'
                )
            info = ('<h1>Steam accounts</h1><p class="muted">'
                    'One Friends profile may link multiple independently verified Steam accounts. '
                    'All share one background ASF process, but each account gets a separate bot. '
                    'No Steam passwords or Guard codes are collected here. '
                    'Preparing a bot does not mean it is authorized or receiving games.</p>'
                    '<form method="post" action="/steam/link">'+hidden("csrf",user["csrf"])+
                    '<button>Link another Steam account</button></form>'+
                    (''.join(cards) if cards else '<p>No verified Steam accounts yet.</p>'))
            return self.respond(200,page("Steam setup",info,user["username"],bool(user["is_admin"])))
        if path.path == "/account":
            items = steam_accounts(user["id"])
            label = ', '.join(html.escape(r["steam_id"]) for r in items) if items else "None yet"
            body = (f'<h1>Account</h1><div class="card"><p>Signed in as <b>{html.escape(user["username"])}</b></p>'
                    f'<p>Linked Steam accounts: {len(items)} / {MAX_STEAM_ACCOUNTS_PER_USER}</p>'
                    f'<p class="muted">{label}</p>'
                    '<form method="post" action="/steam/link">'+hidden("csrf",user["csrf"])+
                    '<button>Link Steam account</button></form>'
                    '<p class="muted">Steam OpenID verifies identity only. '
                    'Individual ASF Steam authentication is a separate step.</p>'
                    '<a href="/steam-setup">Manage Steam accounts</a></div>')
            return self.respond(200,page("Account",body,user["username"],bool(user["is_admin"])))
        return self.respond(404,"Not found","text/plain")

    def do_POST(self):
        if not self.origin_ok():
            return self.respond(403,"Invalid request origin","text/plain")
        try:
            size = int(self.headers.get("Content-Length","0"))
        except ValueError:
            return self.respond(400,"Invalid body","text/plain")
        if not 0 < size <= 4096:
            return self.respond(413,"Invalid body size","text/plain")
        fields = parse_qs(self.rfile.read(size).decode("utf-8","replace"),keep_blank_values=True)
        item = lambda key: fields.get(key,[""])[0]
        path = urlsplit(self.path).path
        if path == "/signup":
            if register(item("invite"),item("username"),item("password")):
                return self.redirect("/login")
            return self.respond(400,page("Join","<h1>Invalid or used invitation, username, or password</h1>"))
        if path == "/login":
            # Explicitly passed by our local reverse proxy; never trust it from the internet.
            ip = (self.headers.get("X-Real-IP","")[:64] or self.client_address[0]) if self.client_address[0] in ("127.0.0.1","::1") else self.client_address[0]
            username = item("username").strip().lower()[:24]
            key = (ip, username)
            now = time.time()
            attempts = [x for x in FAILED_LOGINS.get(key,[]) if now-x < 900]
            if len(attempts)>=6:
                return self.respond(429,"Too many login attempts","text/plain")
            result = login(item("username"),item("password"))
            if not result:
                attempts.append(now)
                FAILED_LOGINS[key]=attempts
                return self.respond(401,page("Login","<h1>Invalid username or password</h1><a href='/login'>Retry</a>"))
            FAILED_LOGINS.pop(key,None)
            raw,_csrf = result
            return self.redirect("/offers", "fh_friend="+raw+"; Path=/; HttpOnly; SameSite=Lax"+("; Secure" if COOKIE_SECURE else ""))
        user = self.session()
        if not user:
            return self.respond(401,"Sign in required","text/plain")
        if path == "/logout":
            logout(self.raw_session())
            return self.redirect("/login","fh_friend=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"+("; Secure" if COOKIE_SECURE else ""))
        if not hmac.compare_digest(item("csrf"),user["csrf"]):
            return self.respond(403,"Invalid CSRF token","text/plain")
        if path == "/steam/qr/start":
            if not QR_ENABLED:
                return self.respond(404,"Steam QR not configured","text/plain")
            try:
                account_id=int(item("account_id"))
            except ValueError:
                return self.respond(400,"Invalid account","text/plain")
            account=next((a for a in steam_accounts(user["id"]) if a["id"]==account_id and a["requested"]),None)
            if account is None:
                return self.respond(403,"Account not authorized","text/plain")
            try:
                qr_broker_call("POST","/start",{"user_id":user["id"],"account_id":account_id})
            except (OSError,ValueError,RuntimeError):
                return self.respond(503,"QR service unavailable or busy","text/plain")
            return self.redirect("/steam/qr?"+urlencode({"account_id":account_id}))
        if path == "/steam/automation":
            try:
                account_id = int(item("account_id"))
            except ValueError:
                return self.respond(400,"Invalid Steam account","text/plain")
            if item("action") == "disable":
                ok = cancel_steam_activation(user["id"],account_id)
            else:
                ok = request_steam_activation(
                    user["id"],account_id,item("steam_login"),item("consent")=="yes",
                )
            if not ok:
                return self.respond(400,"Steam account not found or request invalid","text/plain")
            return self.redirect("/steam-setup")
        if path == "/steam/link":
            url = steam_link_begin(user["id"])
            return self.redirect(url)
        if path == "/admin/invite":
            if not user["is_admin"]:
                return self.respond(403,"Admin access required","text/plain")
            token = create_invite(48)
            return self.respond(200,page("Invitation",admin_content(user,token),user["username"],True))
        if path == "/admin/disable":
            if not user["is_admin"]:
                return self.respond(403,"Admin access required","text/plain")
            name = item("username").strip().lower()
            if not USERNAME_RE.fullmatch(name) or name == user["username"]:
                return self.respond(400,"Invalid username","text/plain")
            with LOCK, database() as conn:
                member = conn.execute("SELECT id FROM users WHERE username=?",(name,)).fetchone()
                if not member:
                    return self.respond(404,"Unknown member","text/plain")
                conn.execute("UPDATE users SET disabled=1 WHERE id=?",(member["id"],))
                conn.execute("DELETE FROM sessions WHERE user_id=?",(member["id"],))
            return self.redirect("/admin")
        if path == "/state":
            if not update_offer_state(user["id"],item("offer_id"),item("state")):
                return self.respond(400,"Invalid offer state","text/plain")
            return self.redirect("/offers")
        return self.respond(404,"Not found","text/plain")


def main():
    init_db()
    srv = ThreadingHTTPServer((HOST,PORT),Handler)
    print(f"FreebieHarbor Friends listening at {HOST}:{PORT}",flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
