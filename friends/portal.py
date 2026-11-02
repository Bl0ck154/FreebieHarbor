#!/usr/bin/env python3
"""Optional invite-only FreebieHarbor portal. No Steam sessions or game-claim actions."""
import hashlib
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
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlencode

FRIENDS_DB = Path(os.getenv("FRIENDS_DB", "./friends-data/friends.db"))
HUB_DB = Path(os.getenv("HUB_DB", "./demo-data/giveaways.db"))
HOST = os.getenv("FRIENDS_HOST", "127.0.0.1")
PORT = int(os.getenv("FRIENDS_PORT", "18084"))
PUBLIC_ORIGIN = os.getenv("FRIENDS_PUBLIC_ORIGIN", "http://127.0.0.1:18084").rstrip("/")
COOKIE_SECURE = os.getenv("FRIENDS_COOKIE_SECURE", "true").lower() not in ("false", "0")
SESSION_SECONDS = 86400 * 7
LOCK = threading.RLock()
FAILED_LOGINS = {}
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,23}$")


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
            is_admin INTEGER NOT NULL DEFAULT 0
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
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        """)
        for name in ("users","invites"):
            cols = {row[1] for row in conn.execute("PRAGMA table_info("+name+")")}
            if "is_admin" not in cols:
                conn.execute("ALTER TABLE "+name+" ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")


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
            ("Content-Security-Policy","default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
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
        if path.path == "/steam-setup":
            text = ("<h1>Free-to-keep setup</h1><div class='card'>"
                    "<p>Use your own ASF installation and the FreePackages plugin. "
                    "No Steam login is connected to this portal.</p>"
                    "<p>Suggested filters: NoCostOnly, IgnoreFreeWeekends and PauseFreePackagesWhilePlaying.</p>"
                    "<p><a href='https://github.com/Citrinate/FreePackages'>Install FreePackages</a></p>"
                    "<p>Steam restricts automated activity. Use third-party tools at your own account risk.</p></div>")
            return self.respond(200,page("Steam setup",text,user["username"],bool(user["is_admin"])))
        if path.path == "/account":
            body = f'''<h1>Account</h1><div class="card"><p>Signed in as <b>{html.escape(user["username"])}</b></p>
<p class="muted">Steam account linking and automatic claiming are not enabled. You can track offers without sharing your Steam password.</p></div>'''
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
            return self.redirect("/offers", "fh_friend="+raw+"; Path=/; HttpOnly; SameSite=Strict"+("; Secure" if COOKIE_SECURE else ""))
        user = self.session()
        if not user:
            return self.respond(401,"Sign in required","text/plain")
        if path == "/logout":
            logout(self.raw_session())
            return self.redirect("/login","fh_friend=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"+("; Secure" if COOKIE_SECURE else ""))
        if not hmac.compare_digest(item("csrf"),user["csrf"]):
            return self.respond(403,"Invalid CSRF token","text/plain")
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
