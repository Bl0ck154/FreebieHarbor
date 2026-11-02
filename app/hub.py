#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

APP_VERSION = "0.6.0"
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "giveaways.db")
POLL_SECONDS = max(300, int(os.environ.get("POLL_SECONDS", "1200")))
PORT = int(os.environ.get("PORT", "8080"))
BIND_HOST = os.environ.get("BIND_HOST", "127.0.0.1")
GAMERPOWER_API = "https://www.gamerpower.com/api/giveaways?platform=steam"
FREETOKEEP_API_BASE = "https://freetokeep.gg/api/v1/offers"
GGDEALS_FREEBIES_FEED = "https://gg.deals/news/pc/freebies/feed/"
ALIENWARE_FORUM_URL = "https://www.alienwarearena.com/forums/board/438/contest-and-giveaways-global"
INDIEGALA_WORKER = os.environ.get("INDIEGALA_WORKER", "").rstrip("/")
EXTERNAL_USER_AGENT = "FreebieHarbor/" + APP_VERSION + " (self-hosted)"
NOTIFY_NEW_OFFERS = os.environ.get("NOTIFY_NEW_OFFERS", "false").lower() in {"true", "1", "yes"}
NOTIFY_NEW_WINS = os.environ.get("NOTIFY_NEW_WINS", "false").lower() in {"true", "1", "yes"}
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
ALLOWED_HOSTS = {
    host.strip().lower() for host in
    os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",")
    if host.strip()
}


os.makedirs(DATA_DIR, exist_ok=True)
_db_lock = threading.Lock()
_poll_lock = threading.Lock()
_refresh_request_lock = threading.Lock()
_refresh_thread = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=20000")
    return c


def init_db():
    with _db_lock, db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS external_offers (
          offer_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          title TEXT NOT NULL,
          url TEXT NOT NULL,
          image_url TEXT,
          offer_type TEXT,
          platform TEXT,
          provider TEXT,
          description TEXT,
          instructions TEXT,
          original_price TEXT,
          published_at TEXT,
          starts_at TEXT,
          ends_at TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          user_state TEXT NOT NULL DEFAULT 'new',
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          raw_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS external_offers_source_seen ON external_offers(source, last_seen DESC);
        CREATE TABLE IF NOT EXISTS offer_notifications (
          offer_id TEXT PRIMARY KEY,
          seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS win_notifications (
          giveaway_code TEXT PRIMARY KEY,
          seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)




def fetch_url_json(url, timeout=20):
    req = Request(url, headers={"Accept": "application/json", "User-Agent": EXTERNAL_USER_AGENT})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_url_text(url, timeout=20, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"):
    req = Request(url, headers={"Accept": accept, "User-Agent": EXTERNAL_USER_AGENT})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def indiegala_worker_request(path, payload=None, timeout=25):
    body = None
    headers = {"Accept": "application/json", "User-Agent": "steam-giveaway-inbox/0.4"}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
        method = "POST"
    req = Request(INDIEGALA_WORKER + path, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": False, "error": f"IndieGala worker HTTP {e.code}"}


def indiegala_status():
    if not INDIEGALA_WORKER:
        return {"ok": False, "configured": False, "authenticated": False}
    try:
        return indiegala_worker_request("/health", timeout=5)
    except Exception:
        return {"ok": False, "configured": True, "authenticated": False}












def set_meta(key, value):
    with _db_lock, db() as c:
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta(key, default=None):
    with _db_lock, db() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default










def _parse_external_time(value):
    text = str(value or "").strip()
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL"}:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def safe_public_url(value):
    value = str(value or "").strip()
    if len(value) > 2048 or any(ord(ch) < 32 for ch in value):
        return ""
    try:
        parsed = urlparse(value)
        if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return ""




def _external_upsert(c, item):
    item = dict(item)
    item["url"] = safe_public_url(item.get("url"))
    item["image_url"] = safe_public_url(item.get("image_url"))
    if not item["url"]:
        return

    c.execute("""
      INSERT INTO external_offers(
        offer_id,source,title,url,image_url,offer_type,platform,provider,description,instructions,
        original_price,published_at,starts_at,ends_at,active,user_state,first_seen,last_seen,raw_json
      ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'new',?,?,?)
      ON CONFLICT(offer_id) DO UPDATE SET
        source=excluded.source,title=excluded.title,url=excluded.url,image_url=excluded.image_url,
        offer_type=excluded.offer_type,platform=excluded.platform,provider=excluded.provider,
        description=excluded.description,instructions=excluded.instructions,original_price=excluded.original_price,
        published_at=excluded.published_at,starts_at=excluded.starts_at,ends_at=excluded.ends_at,
        active=1,last_seen=excluded.last_seen,raw_json=excluded.raw_json
    """, (
        item["offer_id"], item["source"], item["title"], item["url"], item.get("image_url"),
        item.get("offer_type"), item.get("platform"), item.get("provider"), item.get("description"),
        item.get("instructions"), item.get("original_price"), item.get("published_at"), item.get("starts_at"),
        item.get("ends_at"), item["stamp"], item["stamp"], item.get("raw_json", "{}"),
    ))


def _clean_html_text(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _alienware_url_from_text(value):
    m = re.search(
        r"https?://(?:(?:www|eu|na|in|anz)\.)?alienwarearena\.com/[^\s\"'<>]+",
        str(value or ""),
        re.I,
    )
    return m.group(0).rstrip(".,;:!?)]}") if m else ""


def poll_ggdeals():
    stamp = now_iso()
    xml_text = fetch_url_text(
        GGDEALS_FREEBIES_FEED,
        timeout=25,
        accept="application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
    )
    root = ET.fromstring(xml_text)
    items = []
    now = datetime.now(timezone.utc)
    skip_terms = (
        "free weekend", "free-to-play", "free to play", "demo", "playtest",
        "open beta", "closed beta", "free trial", "dlc", "cosmetic",
        "in-game reward", "in game reward",
    )
    relevant_terms = (
        "steam", "steam key", "game key", "key giveaway",
        "alienware", "lenovo", "gamesplanet",
    )

    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or node.findtext("guid") or "").strip()
        desc_html = node.findtext("description") or ""
        desc = _clean_html_text(desc_html)
        combined = f"{title} {desc}".lower()
        if not title or not link:
            continue
        if not any(term in combined for term in relevant_terms):
            continue
        if any(term in combined for term in skip_terms):
            continue

        pub_raw = (node.findtext("pubDate") or "").strip()
        pub_iso = pub_raw
        try:
            dt = parsedate_to_datetime(pub_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            pub_iso = dt.isoformat()
            if (now - dt).days > 21:
                continue
        except Exception:
            pass

        image = ""
        m = re.search(r"<img[^>]+src=[\"']([^\"']+)", desc_html, re.I)
        if m:
            image = html.unescape(m.group(1)).strip()

        if "alienware" in combined or "lenovo" in combined or "gamesplanet" in combined:
            offer_type = "Key-drop radar"
        elif "steam key" in combined or "game key" in combined or "key giveaway" in combined:
            offer_type = "Steam key giveaway"
        elif "free to keep" in combined or "100% off" in combined:
            offer_type = "Free to keep"
        else:
            offer_type = "PC freebie radar"

        rid = hashlib.sha1(link.encode("utf-8", "replace")).hexdigest()[:20]
        raw = {
            "title": title,
            "link": link,
            "description": desc,
            "published_at": pub_iso,
            "feed": GGDEALS_FREEBIES_FEED,
        }
        items.append({
            "offer_id": f"ggdeals:{rid}",
            "source": "ggdeals",
            "title": title,
            "url": link,
            "image_url": image,
            "offer_type": offer_type,
            "platform": "Steam / PC" if "steam" in combined else "PC",
            "provider": "GG.deals",
            "description": desc or "GG.deals PC freebie radar",
            "instructions": "Discovery radar: open the article and claim through the linked provider.",
            "original_price": "",
            "published_at": pub_iso,
            "starts_at": "",
            "ends_at": "",
            "stamp": stamp,
            "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        })

    with _db_lock, db() as c:
        c.execute("UPDATE external_offers SET active=0 WHERE source='ggdeals'")
        for item in items:
            _external_upsert(c, item)
    set_meta("last_poll:ggdeals", stamp)
    set_meta("poll_error:ggdeals", "")
    return len(items)


def poll_alienware():
    stamp = now_iso()
    found = {}
    direct_state = "unavailable"

    # Prefer Alienware's own public giveaway board when the current egress is accepted.
    try:
        page = fetch_url_text(ALIENWARE_FORUM_URL, timeout=12)
        if "detected an issue with your network" in page.lower():
            direct_state = "blocked_by_egress"
        else:
            direct_state = "ok"
            anchor_rx = re.compile(
                r"<a[^>]+href=[\"']([^\"']*/ucf/show/\d+/[^\"']*/Giveaway/[^\"']+)[\"'][^>]*>(.*?)</a>",
                re.I | re.S,
            )
            for href, label_html in anchor_rx.findall(page):
                url = html.unescape(href).strip()
                if url.startswith("/"):
                    url = "https://www.alienwarearena.com" + url
                title = _clean_html_text(label_html)
                low = f"{title} {url}".lower()
                if "steam" not in low or "key-giveaway" not in low.replace("_", "-"):
                    continue
                rid = hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:20]
                found[url] = {
                    "offer_id": f"alienware:{rid}", "source": "alienware", "title": title or "Alienware Arena Steam key giveaway",
                    "url": url, "image_url": "", "offer_type": "Official Steam key drop", "platform": "Steam",
                    "provider": "Alienware Arena", "description": "Official Alienware Arena instant-key giveaway discovered from the public board.",
                    "instructions": "Open Alienware Arena, sign in, and use GET KEY while stock is available.",
                    "original_price": "", "published_at": "", "starts_at": "", "ends_at": "", "stamp": stamp,
                    "raw_json": json.dumps({"discovery": "alienware-board", "direct_state": direct_state}, separators=(",", ":")),
                }
    except Exception as e:
        direct_state = f"{type(e).__name__}: {e}"[:240]

    # Fallback: GG.deals radar. Enrich Alienware articles with the direct AWA link when the article exposes it.
    with _db_lock, db() as c:
        rows = c.execute(
            """SELECT title,url,description,published_at FROM external_offers
               WHERE source='ggdeals' AND active=1
                 AND lower(title || ' ' || coalesce(description,'')) LIKE '%alienware%'"""
        ).fetchall()
    for row in rows:
        article_url = str(row["url"] or "")
        direct_url = ""
        try:
            direct_url = _alienware_url_from_text(fetch_url_text(article_url, timeout=12))
        except Exception:
            pass
        url = direct_url or article_url
        if not url:
            continue
        rid = hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()[:20]
        found[url] = {
            "offer_id": f"alienware:{rid}", "source": "alienware", "title": str(row["title"] or "Alienware Arena giveaway"),
            "url": url, "image_url": "", "offer_type": "Steam key-drop radar", "platform": "Steam",
            "provider": "Alienware Arena", "description": str(row["description"] or "Alienware Arena giveaway discovered by GG.deals."),
            "instructions": "Open the giveaway in your browser and use GET KEY. Direct server-side claim is disabled while Alienware blocks this VPS egress.",
            "original_price": "", "published_at": str(row["published_at"] or ""), "starts_at": "", "ends_at": "", "stamp": stamp,
            "raw_json": json.dumps({"discovery": "ggdeals", "article_url": article_url, "direct_url": direct_url, "direct_state": direct_state}, ensure_ascii=False, separators=(",", ":")),
        }

    with _db_lock, db() as c:
        c.execute("UPDATE external_offers SET active=0 WHERE source='alienware'")
        for item in found.values():
            _external_upsert(c, item)
    set_meta("last_poll:alienware", stamp)
    set_meta("poll_error:alienware", "")
    set_meta("alienware_direct_state", direct_state)
    return len(found)


def poll_gamerpower():
    payload = fetch_url_json(GAMERPOWER_API, timeout=25)
    rows = payload if isinstance(payload, list) else []
    stamp = now_iso()
    items = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        end_text = str(raw.get("end_date") or "").strip()
        end_dt = _parse_external_time(end_text)
        if end_dt and end_dt < datetime.now(timezone.utc):
            continue
        rid = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("open_giveaway_url") or raw.get("giveaway_url") or "").strip()
        if not rid or not title or not url:
            continue
        items.append({
            "offer_id": f"gamerpower:{rid}", "source": "gamerpower", "title": title, "url": url,
            "image_url": str(raw.get("image") or raw.get("thumbnail") or ""),
            "offer_type": str(raw.get("type") or ""), "platform": str(raw.get("platforms") or "Steam"),
            "provider": "GamerPower", "description": str(raw.get("description") or ""),
            "instructions": str(raw.get("instructions") or ""), "original_price": str(raw.get("worth") or ""),
            "published_at": str(raw.get("published_date") or ""), "starts_at": "", "ends_at": end_text,
            "stamp": stamp, "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        })
    with _db_lock, db() as c:
        c.execute("UPDATE external_offers SET active=0 WHERE source='gamerpower'")
        for item in items:
            _external_upsert(c, item)
    set_meta("last_poll:gamerpower", stamp)
    set_meta("poll_error:gamerpower", "")
    return len(items)


def poll_freetokeep():
    stamp = now_iso()
    items = []
    for store in ("steam", "freetokeep"):
        url = FREETOKEEP_API_BASE + "?" + urlencode({"store": store, "limit": 200})
        payload = fetch_url_json(url, timeout=25)
        for raw in (payload.get("items") or []) if isinstance(payload, dict) else []:
            if not isinstance(raw, dict):
                continue
            rid = str(raw.get("id") or "").strip()
            title = str(raw.get("title") or "").strip()
            offer_url = str(raw.get("url") or "").strip()
            if not rid or not title or not offer_url:
                continue
            tags = raw.get("tags") or []
            items.append({
                "offer_id": f"ftk:{rid}", "source": "freetokeep", "title": title, "url": offer_url,
                "image_url": str(raw.get("imageUrl") or ""), "offer_type": ", ".join(map(str, tags)),
                "platform": "Steam", "provider": str(raw.get("storeLabel") or store),
                "description": "Live Steam offer discovered by FreeToKeep.gg",
                "instructions": "Open the offer page and claim it while it is live.",
                "original_price": str(raw.get("originalPrice") or ""),
                "published_at": str(raw.get("publishedAt") or ""), "starts_at": str(raw.get("startsAt") or ""),
                "ends_at": str(raw.get("endsAt") or ""), "stamp": stamp,
                "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            })
    with _db_lock, db() as c:
        c.execute("UPDATE external_offers SET active=0 WHERE source='freetokeep'")
        for item in items:
            _external_upsert(c, item)
    set_meta("last_poll:freetokeep", stamp)
    set_meta("poll_error:freetokeep", "")
    return len(items)


def poll_indiegala():
    stamp = now_iso()
    status = fetch_url_json(INDIEGALA_WORKER + "/health", timeout=8)
    payload = fetch_url_json(INDIEGALA_WORKER + "/api/giveaways", timeout=15)
    rows = payload.get("giveaways") or [] if isinstance(payload, dict) else []
    auth_ready = bool(status.get("authenticated")) if isinstance(status, dict) else False
    user_level = int(status.get("level") or 0) if isinstance(status, dict) else 0
    items = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        rid = str(raw.get("giveaway_id") or "").strip()
        title = str(raw.get("name") or "").strip()
        rel_url = str(raw.get("url") or "").strip()
        if not rid or not title or not rel_url:
            continue
        offer_url = rel_url if rel_url.startswith("http") else "https://www.indiegala.com" + rel_url
        price = int(raw.get("price") or 0)
        participants = int(raw.get("participants") or 0)
        level = int(raw.get("required_level") or 0)
        worker_raw = {}
        try:
            worker_raw = json.loads(raw.get("raw_json") or "{}") if isinstance(raw.get("raw_json"), str) else {}
        except Exception:
            worker_raw = {}
        image_url = str(raw.get("image_url") or worker_raw.get("image_url") or "")
        steam_type = str(raw.get("steam_type") or worker_raw.get("steam_type") or "unknown").lower()
        entered = bool(raw.get("entered"))
        single = bool(raw.get("single", worker_raw.get("single", False)))
        if steam_type == "dlc":
            worker_state = "DLC · auto-entry skipped"
        elif entered:
            worker_state = "Entered automatically"
        elif not single:
            worker_state = "Non-single giveaway · skipped"
        elif level > user_level:
            worker_state = f"Level {level} required · current level {user_level}"
        elif auth_ready:
            worker_state = "Eligible for auto-entry"
        else:
            worker_state = "Login required for auto-entry"
        offer_kind = "DLC" if steam_type == "dlc" else str(raw.get("kind") or "giveaway")
        items.append({
            "offer_id": f"indiegala:{rid}", "source": "indiegala", "title": title, "url": offer_url,
            "image_url": image_url, "offer_type": offer_kind, "platform": "Steam",
            "provider": "IndieGala", "description": f"{worker_state}. Level {level}; {participants} entries.",
            "instructions": worker_state, "original_price": f"{price} iS", "published_at": "",
            "starts_at": "", "ends_at": "", "stamp": stamp,
            "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
        })
    with _db_lock, db() as c:
        c.execute("UPDATE external_offers SET active=0 WHERE source='indiegala'")
        for item in items:
            _external_upsert(c, item)
    set_meta("last_poll:indiegala", stamp)
    set_meta("poll_error:indiegala", "")
    return len(items)


def poll_external_sources():
    counts = {}
    for name, fn in (
        ("gamerpower", poll_gamerpower),
        ("freetokeep", poll_freetokeep),
        ("ggdeals", poll_ggdeals),
        ("alienware", poll_alienware),
        *([("indiegala", poll_indiegala)] if INDIEGALA_WORKER else []),
    ):
        try:
            counts[name] = fn()
        except Exception as e:
            set_meta(f"poll_error:{name}", f"{type(e).__name__}: {e}")
            counts[name] = "error"
    return counts


def _send_telegram(text):
    """Single outbound request; never include bot token in errors or logs."""
    data = urlencode({
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = Request(
        "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage",
        data=data, method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            raise RuntimeError("Telegram rejected the message")
    except HTTPError as exc:
        raise RuntimeError("Telegram HTTP " + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Telegram network error") from None


def notify_new_offers():
    """No first-run alert flood, and no repeated alerts across restarts."""
    if not (NOTIFY_NEW_OFFERS and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return {"enabled": False}
    with _db_lock, db() as conn:
        ready = conn.execute(
            "SELECT value FROM meta WHERE key='offer_notifications_initialized'"
        ).fetchone()
        if ready is None:
            conn.execute(
                "INSERT OR IGNORE INTO offer_notifications(offer_id, seen_at) "
                "SELECT offer_id, ? FROM external_offers", (now_iso(),)
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('offer_notifications_initialized','1')"
            )
            return {"enabled": True, "baseline": True}
        rows = conn.execute("""
            SELECT e.offer_id,e.title,e.url,e.provider
            FROM external_offers e
            LEFT JOIN offer_notifications n ON n.offer_id=e.offer_id
            WHERE n.offer_id IS NULL AND e.active=1 AND e.user_state='new'
            ORDER BY e.first_seen DESC LIMIT 7
        """).fetchall()
        count = conn.execute("""
            SELECT COUNT(*) FROM external_offers e
            LEFT JOIN offer_notifications n ON n.offer_id=e.offer_id
            WHERE n.offer_id IS NULL AND e.active=1 AND e.user_state='new'
        """).fetchone()[0]
    if not count:
        return {"enabled": True, "sent": 0}
    lines = ["🎮 <b>FreebieHarbor: " + str(count) + " new offer(s)</b>"]
    for item in rows[:6]:
        name = html.escape(str(item["title"] or "Untitled")[:90])
        provider = html.escape(str(item["provider"] or "")[:40])
        url = safe_public_url(item["url"])
        if not url:
            continue
        lines.append("• <a href='" + html.escape(url, quote=True) + "'>" +
                     name + "</a>" + (" · " + provider if provider else ""))
    if count > 6:
        lines.append("… and " + str(count - 6) + " more in the dashboard")
    try:
        _send_telegram("\n".join(lines))
    except RuntimeError as exc:
        set_meta("notification_error", str(exc))
        return {"enabled": True, "sent": 0, "error": str(exc)}
    with _db_lock, db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO offer_notifications(offer_id,seen_at) "
            "SELECT offer_id, ? FROM external_offers WHERE active=1", (now_iso(),)
        )
    set_meta("notification_error", "")
    set_meta("last_notification_at", now_iso())
    return {"enabled": True, "sent": count}




def poll_all():
    if get_meta("demo_mode") == "1":
        return {"ok": True, "demo": True, "message": "Demo data; external polling disabled"}
    if not _poll_lock.acquire(blocking=False):
        return {"ok": False, "message": "refresh already running"}
    try:
        counts = {}
        try:
            notify_new_wins()
        except Exception:
            set_meta("win_notification_error", "Unexpected win notifier error")
        external_counts = poll_external_sources()
        # Only initialize the alert baseline when a source actually responded.
        # A total network outage must not turn recovered historical offers into alerts.
        if any(value != "error" for value in external_counts.values()):
            try:
                notify_new_offers()
            except Exception:
                set_meta("notification_error", "Unexpected notifier error")

        set_meta("last_refresh_at", now_iso())
        return {"ok": True, "counts": counts, "external": external_counts}
    finally:
        _poll_lock.release()


def schedule_refresh():
    """Start a manual refresh without blocking the HTTP request thread."""
    global _refresh_thread
    if get_meta("demo_mode") == "1":
        return False
    with _refresh_request_lock:
        if _poll_lock.locked() or (_refresh_thread is not None and _refresh_thread.is_alive()):
            return False
        _refresh_thread = threading.Thread(target=poll_all, name="manual-refresh", daemon=True)
        _refresh_thread.start()
        return True


def poll_loop():
    while True:
        try:
            poll_all()
        except Exception as e:
            set_meta("last_loop_error", f"{type(e).__name__}: {e}")
        time.sleep(POLL_SECONDS)












def _page_number(query, key="page", default=1):
    try:
        return max(1, int((query.get(key) or [str(default)])[0]))
    except (TypeError, ValueError):
        return default


def _query_text(query, key, default=""):
    return str((query.get(key) or [default])[0] or "").strip()


def _safe_return_to(value, fallback="/dashboard"):
    value = str(value or "").strip()
    if not value or len(value) > 2048 or "\\" in value or any(ord(ch) < 32 for ch in value):
        return fallback
    parsed = urlparse(value)
    if (not parsed.scheme and not parsed.netloc and not parsed.fragment
            and parsed.path in {"/dashboard", "/offers", }):
        return value
    return fallback


def _page_href(path, page, params=None):
    q = {k: v for k, v in (params or {}).items() if v not in (None, "", "all")}
    q["page"] = page
    return path + ("?" + urlencode(q) if q else "")


def pagination_html(path, page, pages, total, page_size, params=None):
    if pages <= 1:
        return ""
    start = (page - 1) * page_size + 1
    end = min(total, page * page_size)
    candidates = {1, pages, page - 2, page - 1, page, page + 1, page + 2}
    nums = sorted(n for n in candidates if 1 <= n <= pages)
    parts = []
    prev_cls = " disabled" if page <= 1 else ""
    next_cls = " disabled" if page >= pages else ""
    prev_href = _page_href(path, max(1, page - 1), params)
    next_href = _page_href(path, min(pages, page + 1), params)
    parts.append(f'<a class="page-btn{prev_cls}" href="{html.escape(prev_href, quote=True)}">Previous</a>')
    last = 0
    for n in nums:
        if last and n > last + 1:
            parts.append('<span class="page-gap">…</span>')
        cls = " active" if n == page else ""
        href = _page_href(path, n, params)
        parts.append(f'<a class="page-num{cls}" href="{html.escape(href, quote=True)}">{n}</a>')
        last = n
    parts.append(f'<a class="page-btn{next_cls}" href="{html.escape(next_href, quote=True)}">Next</a>')
    return f'''<div class="pagination"><div class="pagination-info">Showing {start}–{end} of {total}</div><div class="pagination-links">{"".join(parts)}</div></div>'''


def external_offers_data(page=1, page_size=20, source="all", search="", view="active"):
    source = source if source in {"all", "gamerpower", "freetokeep", "ggdeals", "alienware", "indiegala"} else "all"
    where = ["active=0" if view == "history" else "active=1", "user_state != 'ignored'"]
    params = []
    if source != "all":
        where.append("source=?")
        params.append(source)
    if search:
        like = f"%{search}%"
        where.append("(title LIKE ? OR provider LIKE ? OR description LIKE ? OR instructions LIKE ?)")
        params.extend([like, like, like, like])
    where_sql = " AND ".join(where)
    with _db_lock, db() as c:
        total = int(c.execute(f"SELECT COUNT(*) FROM external_offers WHERE {where_sql}", params).fetchone()[0])
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(1, page), pages)
        offset = (page - 1) * page_size
        rows = c.execute(
            f"SELECT * FROM external_offers WHERE {where_sql} ORDER BY first_seen DESC, source, title LIMIT ? OFFSET ?",
            [*params, page_size, offset],
        ).fetchall()
    return rows, total, page, pages


def external_source_counts():
    with _db_lock, db() as c:
        rows = c.execute("SELECT source, COUNT(*) n FROM external_offers WHERE active=1 AND user_state != 'ignored' GROUP BY source").fetchall()
    return {str(r["source"]): int(r["n"]) for r in rows}


def _external_card(r, return_to):
    state = str(r["user_state"] or "new")
    source = str(r["source"] or "")
    source_label = {"gamerpower": "GamerPower", "freetokeep": "FreeToKeep", "ggdeals": "GG.deals", "alienware": "Alienware Arena", "indiegala": "IndieGala"}.get(source, source or "External")
    provider = str(r["provider"] or "").strip()
    offer_type = str(r["offer_type"] or "").strip()
    platform = str(r["platform"] or "").strip()
    end_text = str(r["ends_at"] or "").strip()
    price = str(r["original_price"] or "").strip()
    info = str(r["instructions"] or r["description"] or "").strip()
    info = re.sub(r"<[^>]+>", " ", info)
    info = re.sub(r"\s+", " ", info).strip()
    if len(info) > 180:
        info = info[:179].rstrip() + "…"
    oid = html.escape(str(r["offer_id"]), quote=True)
    offer_url = html.escape(safe_public_url(r["url"]), quote=True)
    image_url = html.escape(safe_public_url(r["image_url"]), quote=True)
    return_value = html.escape(return_to, quote=True)
    raw_meta = {}
    try:
        raw_meta = json.loads(r["raw_json"] or "{}") if "raw_json" in r.keys() else {}
    except Exception:
        raw_meta = {}
    worker_entered = source == "indiegala" and bool(raw_meta.get("entered"))
    worker_type = str(raw_meta.get("steam_type") or "").lower() if source == "indiegala" else ""
    if worker_type == "dlc":
        badge_variant, badge_text = "warning", "DLC · skipped"
    elif worker_entered:
        badge_variant, badge_text = "success", "Entered"
    elif state == "claimed":
        badge_variant, badge_text = "default", "Claimed"
    elif not r["active"]:
        badge_variant, badge_text = "default", "Archived"
    else:
        badge_variant, badge_text = "success", "Live"
    state_actions = ""
    if source != "indiegala" and state != "claimed":
        state_actions += f'<form class="action-claim" method="post" action="/external-state"><input type="hidden" name="id" value="{oid}"><input type="hidden" name="return_to" value="{return_value}"><button class="btn btn-primary btn-sm" name="state" value="claimed">Mark claimed</button></form>'
    ignore_class = "action-ignore-auto" if source == "indiegala" else "action-ignore"
    state_actions += f'<form class="{ignore_class}" method="post" action="/external-state"><input type="hidden" name="id" value="{oid}"><input type="hidden" name="return_to" value="{return_value}"><button class="btn btn-secondary btn-sm" name="state" value="ignored">Ignore</button></form>'
    if state != "new":
        state_actions += f'<form class="action-undo" method="post" action="/external-state"><input type="hidden" name="id" value="{oid}"><input type="hidden" name="return_to" value="{return_value}"><button class="btn btn-ghost btn-sm" name="state" value="new">Undo</button></form>'
    facts = []
    if source == "indiegala":
        amount_match = re.search(r"([0-9]+)", price)
        gala_amount = int(amount_match.group(1)) if amount_match else 0
        facts.append(f'<span class="offer-fact coin-fact" title="GalaSilver entry cost">{svg_icon("coins", 15)}<b>{gala_amount}</b> GalaSilver</span>')
        required_level = int(raw_meta.get("required_level") or 0)
        facts.append(f'<span class="offer-fact" title="Required IndieGala level">{svg_icon("user", 15)}Level {required_level}</span>')
        ticket_source = str(raw_meta.get("kind") or offer_type or "")
        ticket_label = re.sub(r"\s*-\s*Lev\.\s*\d+\s*$", "", ticket_source, flags=re.I).strip()
        if ticket_label and ticket_label.lower() != "dlc":
            facts.append(f'<span class="offer-fact" title="Giveaway type">{svg_icon("ticket", 15)}{html.escape(ticket_label)}</span>')
        if worker_type == "dlc":
            facts.append(f'<span class="offer-fact dlc-fact" title="Steam downloadable content">{svg_icon("layers", 15)}DLC</span>')
        elif platform:
            facts.append(f'<span class="offer-fact" title="Platform">{svg_icon("gamepad", 15)}{html.escape(platform)}</span>')
        participants = int(raw_meta.get("participants") or 0)
        if participants > 0:
            facts.append(f'<span class="offer-fact" title="Current entries">{svg_icon("users", 15)}{participants} entries</span>')
    else:
        if provider:
            facts.append(f'<span class="offer-fact">{svg_icon("gamepad", 15)}{html.escape(provider)}</span>')
        if offer_type:
            facts.append(f'<span class="offer-fact">{svg_icon("ticket", 15)}{html.escape(offer_type)}</span>')
        if platform and platform.lower() not in provider.lower():
            facts.append(f'<span class="offer-fact">{svg_icon("gamepad", 15)}{html.escape(platform)}</span>')
        if price:
            facts.append(f'<span class="offer-fact coin-fact">{svg_icon("coins", 15)}Was {html.escape(price)}</span>')
    if end_text and end_text.upper() != "N/A":
        facts.append(f'<span class="offer-fact" title="Offer deadline">{svg_icon("clock", 15)}{html.escape(end_text)}</span>')
    facts_html = ''.join(facts)
    image = f'<img class="offer-image" src="{image_url}" alt="" loading="lazy" onerror="this.style.display=\'none\'">' if image_url else '<div class="offer-image placeholder">🎁</div>'
    if source == "demo":
        badge_variant, badge_text = "default", "Sample"
        covers = [
            ("linear-gradient(130deg,#1e40af,#0891b2)", "🚀"),
            ("linear-gradient(130deg,#7c3aed,#db2777)", "✨"),
            ("linear-gradient(130deg,#166534,#65a30d)", "🌿"),
            ("linear-gradient(130deg,#c2410c,#f59e0b)", "🏝️"),
            ("linear-gradient(130deg,#334155,#64748b)", "🪐"),
            ("linear-gradient(130deg,#0f766e,#0d9488)", "🧩"),
        ]
        bg, symbol = covers[hashlib.sha256(str(r["title"]).encode()).digest()[0] % len(covers)]
        image = f'<div class="offer-image placeholder" style="background:{bg};color:white;font-size:42px">{symbol}</div>'
    action_class = "auto-actions" if source == "indiegala" else "manual-actions"
    return f'''<article class="card offer-card source-{html.escape(source)} {html.escape(state)}">{image}<div class="offer-body"><div class="offer-top"><span class="badge badge-info">{html.escape(source_label)}</span><span class="badge badge-{badge_variant}">{badge_text}</span></div><h3>{html.escape(str(r["title"] or "Untitled offer"))}</h3><div class="offer-facts">{facts_html}</div>{f'<p class="offer-description">{html.escape(info)}</p>' if info else ''}<div class="offer-actions {action_class}"><a class="btn btn-ghost btn-sm action-open" href="{offer_url}" target="_blank" rel="noreferrer">Open ↗</a>{state_actions}</div></div></article>'''










ICON_PATHS = {
    "dashboard": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "gift": '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 1 1 4.5-1.5V8H7.5Z"/><path d="M16.5 8A2.5 2.5 0 1 0 12 6.5V8h4.5Z"/>',
    "message": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>',
    "coins": '<circle cx="12" cy="12" r="9"/><path d="M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8"/><path d="M12 18V6"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "user": '<path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "ticket": '<path d="M2 9a3 3 0 0 0 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 0 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M13 5v2"/><path d="M13 17v2"/><path d="M13 11v2"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "gamepad": '<line x1="6" x2="10" y1="11" y2="11"/><line x1="8" x2="8" y1="9" y2="13"/><line x1="15" x2="15.01" y1="12" y2="12"/><line x1="18" x2="18.01" y1="10" y2="10"/><path d="M17.32 5H6.68a4 4 0 0 0-3.978 3.59c-.006.052-.01.101-.017.152C2.604 9.416 2 14.456 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.414-1.414A2 2 0 0 1 9.828 16h4.344a2 2 0 0 1 1.414.586L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.545-.604-6.584-.685-7.258-.007-.05-.011-.1-.017-.152A4 4 0 0 0 17.32 5z"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83z"/><path d="m22 12.5-9.17 4.17a2 2 0 0 1-1.66 0L2 12.5"/><path d="m22 17.5-9.17 4.17a2 2 0 0 1-1.66 0L2 17.5"/>',
    "refresh": '<path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5"/><path d="M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5"/>',
}


def svg_icon(name, size=20, extra_class=""):
    paths = ICON_PATHS.get(name, ICON_PATHS["gift"])
    return f'<svg class="icon {html.escape(extra_class)}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'


UI_CSS = r'''
:root{--background:#fff;--surface:#f5f5f5;--surface-card:#fff;--text:#111827;--muted:#6b7280;--muted-2:#9ca3af;--border:#e5e7eb;--primary:#3b82f6;--primary-hover:#2563eb;--danger:#ef4444;--green:#16a34a;--orange:#ea580c;--purple:#9333ea;--teal:#0d9488;--yellow:#ca8a04}
html.dark{--background:#1a1a2e;--surface:#16213e;--surface-card:#16213e;--text:#f9fafb;--muted:#d1d5db;--muted-2:#9ca3af;--border:#374151;--primary:#60a5fa;--primary-hover:#3b82f6;--danger:#f87171;--green:#4ade80;--orange:#fb923c;--purple:#c084fc;--teal:#2dd4bf;--yellow:#facc15}
*{box-sizing:border-box}html,body{margin:0;min-width:320px;min-height:100%;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--text);background:var(--background);font-size:14px;line-height:1.5}a{color:inherit;text-decoration:none}button,input,textarea,select{font:inherit}.header{height:64px;border-bottom:1px solid var(--border);background:var(--surface-card);padding:0 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:20}.header h1{font-size:20px;line-height:28px;font-weight:700;margin:0}.header-right{display:flex;align-items:center;gap:16px}.status-item{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:14px;font-weight:500}.status-green{color:#22c55e}.status-gray{color:#9ca3af}.icon-button{padding:8px;border:0;border-radius:8px;background:transparent;color:var(--muted);cursor:pointer;display:grid;place-items:center}.icon-button:hover{background:#f3f4f6}.dark .icon-button:hover{background:#374151}.layout{display:flex;min-height:calc(100vh - 64px)}.sidebar{width:256px;flex:0 0 256px;border-right:1px solid var(--border);background:var(--surface);min-height:calc(100vh - 64px)}.sidebar nav{padding:16px}.nav-list{display:grid;gap:8px}.nav-link{display:flex;align-items:center;gap:12px;padding:8px 16px;border-radius:8px;color:#4b5563;transition:background-color .15s,color .15s}.dark .nav-link{color:#d1d5db}.nav-link:hover{background:#e5e7eb}.dark .nav-link:hover{background:#374151}.nav-link.active{background:var(--primary);color:#fff}.nav-divider{border-top:1px solid var(--border);margin:8px 0}.main{flex:1;min-width:0;padding:24px}.page{display:grid;gap:24px}.page-title{display:flex;align-items:center;justify-content:space-between;gap:16px}.page-title h2{font-size:24px;line-height:32px;font-weight:700;margin:0}.page-title-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.card{background:var(--surface-card);border:1px solid var(--border);border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.05);overflow:hidden}.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-bottom:1px solid var(--border)}.card-header h3{font-size:18px;line-height:28px;font-weight:600;margin:0}.card-body{padding:16px}.stats-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.stat-label{font-size:14px;color:var(--muted);margin:0}.stat-value{font-size:30px;line-height:36px;font-weight:700;margin:2px 0 0}.stat-sub{font-size:12px;color:var(--muted-2);margin-top:2px}.blue{color:#2563eb}.dark .blue{color:#60a5fa}.green{color:var(--green)}.purple{color:var(--purple)}.orange{color:var(--orange)}.teal{color:var(--teal)}.yellow{color:var(--yellow)}.row-between{display:flex;align-items:center;justify-content:space-between;gap:12px}.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;border:1px solid transparent;border-radius:8px;font-weight:500;cursor:pointer;white-space:nowrap;line-height:20px;min-height:36px;transition:background-color .15s,border-color .15s,color .15s,opacity .15s}.btn-md{padding:9px 16px;font-size:16px;min-height:42px}.btn-sm{padding:7px 12px;font-size:14px;min-height:36px}.offer-actions .btn{min-width:0}.offer-actions form{display:inline-flex}.offer-actions{align-items:center}.btn-primary{background:var(--primary);color:#fff}.btn-primary:hover{opacity:.9}.btn-secondary{background:#e5e7eb;color:#111827}.btn-secondary:hover{background:#d1d5db}.dark .btn-secondary{background:#374151;color:#f3f4f6}.dark .btn-secondary:hover{background:#4b5563}.btn-danger{background:var(--danger);color:#fff}.btn-ghost{background:#fff;color:#374151;border-color:#e5e7eb}.btn-ghost:hover{background:#f3f4f6;border-color:#d1d5db}.dark .btn-ghost{background:transparent;color:#d1d5db;border-color:#374151}.dark .btn-ghost:hover{background:#1f2937;border-color:#4b5563}.btn.disabled,.page-btn.disabled{opacity:.45;pointer-events:none}.badge{display:inline-flex;align-items:center;font-weight:500;border-radius:999px;padding:2px 8px;font-size:12px}.badge-success{background:#dcfce7;color:#166534}.badge-warning{background:#fef9c3;color:#854d0e}.badge-error{background:#fee2e2;color:#991b1b}.badge-info{background:#dbeafe;color:#1e40af}.badge-default{background:#f3f4f6;color:#1f2937}.dark .badge-success{background:rgba(20,83,45,.45);color:#86efac}.dark .badge-warning{background:rgba(113,63,18,.45);color:#fde047}.dark .badge-error{background:rgba(127,29,29,.45);color:#fca5a5}.dark .badge-info{background:rgba(30,58,138,.45);color:#93c5fd}.dark .badge-default{background:#1f2937;color:#d1d5db}.text-sm{font-size:14px}.text-xs{font-size:12px}.muted{color:var(--muted)}.input,.select,.textarea{appearance:none;background:#fff;color:#111827;border:1px solid #d1d5db;border-radius:8px;padding:8px 12px;line-height:24px;min-height:42px;transition:border-color .15s,box-shadow .15s,background-color .15s}.input:hover,.select:hover,.textarea:hover{border-color:#9ca3af}.dark .input,.dark .select,.dark .textarea{background:#111827;color:#f9fafb;border-color:#4b5563}.input:focus,.select:focus,.textarea:focus{outline:none;box-shadow:0 0 0 3px rgba(59,130,246,.18);border-color:var(--primary)}.select{padding-right:38px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3E%3Cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='m6 8 4 4 4-4'/%3E%3C/svg%3E");background-position:right 10px center;background-repeat:no-repeat;background-size:18px 18px}.textarea{width:100%;min-height:100px;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}.filters{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.search-form{display:flex;gap:8px;align-items:center}.search-form .input{width:300px}.search-form .btn{flex:0 0 auto}.filter-chip{display:flex;align-items:center;gap:8px;padding:6px 12px;background:#f3f4f6;border-radius:8px}.dark .filter-chip{background:#1f2937}.results-count{font-size:14px;color:var(--muted)}.offer-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px}.offer-card{display:flex;flex-direction:column;min-width:0;height:100%}.offer-image{width:100%;aspect-ratio:16/9;object-fit:cover;background:#e5e7eb;display:block}.offer-image.placeholder{display:grid;place-items:center;font-size:28px;color:#9ca3af}.offer-body{padding:12px;display:flex;flex-direction:column;gap:8px;min-height:240px}.offer-top{display:flex;justify-content:space-between;gap:6px;align-items:center}.offer-card h3{font-size:15px;line-height:20px;font-weight:600;margin:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.offer-meta{display:flex;gap:6px;flex-wrap:wrap}.mini-meta{font-size:11px;color:var(--muted);background:#f3f4f6;border-radius:4px;padding:2px 5px}.dark .mini-meta{background:#1f2937}.offer-facts{display:flex;align-items:center;gap:6px 10px;flex-wrap:wrap;min-height:24px}.offer-fact{display:inline-flex;align-items:center;gap:5px;color:var(--muted);font-size:12px;line-height:18px;white-space:nowrap}.offer-fact .icon{flex:0 0 auto;stroke-width:1.8}.offer-fact b{font-weight:650;color:var(--text)}.coin-fact{color:#a16207}.coin-fact .icon{color:#ca8a04}.dark .coin-fact{color:#fde68a}.dark .coin-fact .icon{color:#facc15}.dlc-fact{color:#b45309}.dlc-fact .icon{color:#d97706}.dark .dlc-fact{color:#fbbf24}.offer-description{font-size:13px;color:var(--muted);margin:0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}.offer-actions{margin-top:auto;padding-top:6px;display:grid;gap:8px;align-items:stretch}.offer-actions.manual-actions{grid-template-columns:minmax(0,1fr) minmax(0,1.45fr);min-height:88px}.offer-actions.auto-actions{grid-template-columns:repeat(2,minmax(0,1fr));min-height:40px}.offer-actions>a,.offer-actions>form{min-width:0;margin:0}.offer-actions .btn{width:100%;height:40px;padding:0 10px;white-space:nowrap}.offer-actions.manual-actions .action-ignore,.offer-actions.manual-actions .action-undo{grid-column:1/-1}.actions form{margin:0}.dark .task span,.dark .dark details summary{cursor:pointer;color:var(--muted);font-size:13px}.actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:12px}.pagination{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-top:4px}.pagination-info{font-size:14px;color:var(--muted)}.pagination-links{display:flex;align-items:center;gap:4px}.page-btn,.page-num{display:inline-flex;align-items:center;justify-content:center;min-width:36px;height:36px;padding:0 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface-card);font-size:14px}.page-num.active{background:var(--primary);border-color:var(--primary);color:#fff}.page-btn:hover,.page-num:hover{background:#f3f4f6}.page-num.active:hover{background:var(--primary)}.dark .page-btn:hover,.dark .page-num:hover{background:#1f2937}.page-gap{padding:0 5px;color:var(--muted)}.status-banner{display:flex;align-items:flex-start;gap:12px;border-radius:8px;padding:14px 16px;border:1px solid}.status-banner.success{background:#f0fdf4;border-color:#bbf7d0;color:#166534}.status-banner.warning{background:#fffbeb;border-color:#fde68a;color:#92400e}.dark .status-banner.success{background:rgba(20,83,45,.28);border-color:#166534;color:#86efac}.dark .status-banner.warning{background:rgba(113,63,18,.28);border-color:#92400e;color:#fde047}.list-rows{display:grid;gap:12px}.list-row{display:flex;align-items:center;gap:12px;padding:12px;background:#f9fafb;border-radius:8px}.dark .list-row{background:rgba(31,41,55,.55)}.row-thumb{width:64px;height:40px;object-fit:cover;border-radius:2px;background:#e5e7eb;flex:0 0 auto}.row-main{flex:1;min-width:0}.row-main b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.row-meta{display:flex;align-items:center;gap:10px;color:var(--muted);font-size:13px;flex-wrap:wrap}.source-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.dark .empty{text-align:center;padding:32px;color:var(--muted)}.footer-note{font-size:12px;color:var(--muted);text-align:center;padding:4px 0 20px}
@media(max-width:1350px){.offer-grid{grid-template-columns:repeat(4,minmax(0,1fr))}}@media(max-width:1100px){.offer-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.stats-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:900px){.sidebar{width:220px;flex-basis:220px}.offer-grid,.source-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:720px){.header{padding:0 14px}.header-right .status-text{display:none}.layout{display:block}.sidebar{width:auto;min-height:0;border-right:0;border-bottom:1px solid var(--border)}.sidebar nav{padding:10px;overflow-x:auto}.nav-list{display:flex;gap:6px}.nav-link{white-space:nowrap;padding:7px 10px}.nav-divider{display:none}.main{padding:16px}.page-title{align-items:flex-start;flex-direction:column}.stats-grid,.offer-grid,.source-grid{grid-template-columns:1fr}.search-form{width:100%}.search-form .input{width:100%}.pagination{align-items:flex-start;flex-direction:column}.offer-body{min-height:0}}
'''


UI_JS = r'''
(function(){if(localStorage.getItem('freebieharbor-theme')==='dark'){document.documentElement.classList.add('dark')}})();
function toggleTheme(){var root=document.documentElement;root.classList.toggle('dark');localStorage.setItem('freebieharbor-theme',root.classList.contains('dark')?'dark':'light');var a=document.getElementById('theme-sun'),b=document.getElementById('theme-moon');if(a&&b){a.style.display=root.classList.contains('dark')?'block':'none';b.style.display=root.classList.contains('dark')?'none':'block'}}
function initThemeIcon(){var d=document.documentElement.classList.contains('dark'),a=document.getElementById('theme-sun'),b=document.getElementById('theme-moon');if(a&&b){a.style.display=d?'block':'none';b.style.display=d?'none':'block'}}
window.addEventListener('DOMContentLoaded',initThemeIcon);
'''


def render_shell(active, content, status=None, title="FreebieHarbor"):
    status = status or {}
    authenticated = bool(status.get("authenticated"))
    username = str(status.get("username") or "").strip()
    session_title = "FreebieHarbor local dashboard"
    session_icon = svg_icon("activity", 16, "status-green")
    nav = [("dashboard", "/dashboard", "Dashboard", "dashboard"),("offers", "/offers", "Free games", "gift"),("rewards", "/rewards", "Rewards", "gift")]
    nav_html = "".join(f'<a class="nav-link{" active" if active==key else ""}" href="{href}">{svg_icon(icon)}<span>{label}</span></a>' for key, href, label, icon in nav)
    nav_html += '<div class="nav-divider"></div>'
    nav_html += f'<a class="nav-link" href="https://github.com/kernelcoffee/SteamSelfGifter" target="_blank" rel="noopener noreferrer">{svg_icon("external")}<span>SteamSelfGifter</span></a>'
    nav_html += f'<a class="nav-link" href="https://github.com/JustArchiNET/ArchiSteamFarm" target="_blank" rel="noopener noreferrer">{svg_icon("external")}<span>ASF</span></a>'
    moon = svg_icon("moon", 20); sun = svg_icon("sun", 20)
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>{html.escape(title)}</title><style>{UI_CSS}</style><script>{UI_JS}</script></head><body><header class="header"><h1>⚓ FreebieHarbor</h1><div class="header-right"><div class="status-item" title="{html.escape(session_title, quote=True)}">{session_icon}<span class="status-text">{'Local'}</span></div><div class="status-item"><span class="status-green">{svg_icon('activity',16)}</span><span class="status-text">{"Demo" if get_meta("demo_mode") == "1" else "Polling"}</span></div><button class="icon-button" type="button" onclick="toggleTheme()" aria-label="Toggle theme"><span id="theme-moon">{moon}</span><span id="theme-sun" style="display:none">{sun}</span></button></div></header><div class="layout"><aside class="sidebar"><nav><div class="nav-list">{nav_html}</div></nav></aside><main class="main">{content}<div class="footer-note">FreebieHarbor v{APP_VERSION} · local-first · <a href="https://github.com/Bl0ck154/FreebieHarbor" target="_blank" rel="noopener noreferrer">GitHub</a></div></main></div></body></html>'''


def _stat_card(label, value, color="", sub=""):
    return f'''<div class="card"><div class="card-body"><p class="stat-label">{html.escape(str(label))}</p><p class="stat-value {html.escape(color)}">{html.escape(str(value))}</p>{f'<div class="stat-sub">{html.escape(str(sub))}</div>' if sub else ''}</div></div>'''


def _dashboard_external_rows(limit=6):
    rows, total, _, _ = external_offers_data(page=1, page_size=limit)
    if not rows: return '<div class="empty">No live external offers right now.</div>', total
    out=[]
    for r in rows:
        image=safe_public_url(r["image_url"])
        img=f'<img class="row-thumb" src="{html.escape(image, quote=True)}" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">' if image else '<div class="row-thumb"></div>'
        source={"gamerpower":"GamerPower","freetokeep":"FreeToKeep","ggdeals":"GG.deals","alienware":"Alienware Arena","indiegala":"IndieGala"}.get(str(r["source"] or ""),str(r["source"] or "External"))
        out.append(f'''<div class="list-row">{img}<div class="row-main"><b>{html.escape(str(r["title"] or "Untitled"))}</b><div class="row-meta"><span>{html.escape(source)}</span><span>{html.escape(str(r["offer_type"] or ""))}</span></div></div><a class="btn btn-ghost btn-sm" href="{html.escape(safe_public_url(r["url"]), quote=True)}" target="_blank" rel="noopener noreferrer">Open ↗</a></div>''')
    return '<div class="list-rows">'+"".join(out)+'</div>', total




def rewards_summary():
    """Read-only SteamSelfGifter integration. Never reads credentials or writes its DB."""
    path = os.environ.get("STEAMGIFTS_DB", "").strip()
    if not path or not os.path.isfile(path):
        return {"connected": False}
    c = None
    try:
        c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        joined = c.execute("SELECT COUNT(*) FROM entries WHERE status='success'").fetchone()[0]
        wins = c.execute("SELECT COUNT(*) FROM giveaways WHERE is_won=1").fetchone()[0]
        return {"connected": True, "entered": joined, "wins": wins}
    except (OSError, sqlite3.Error):
        return {"connected": False, "error": "SteamGifts database unavailable"}
    finally:
        if c is not None:
            c.close()


def asf_summary():
    # Read only plugin metadata, never ASF credentials or account session files.
    path = os.environ.get("ASF_PACKAGES_DB", "").strip()
    if not path or not os.path.isfile(path):
        return {"connected": False}
    try:
        if os.path.getsize(path) > 4_000_000:
            raise ValueError("file too large")
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("invalid object")
        if "seen_packages_count" in data or "new_owned_queue_count" in data:
            seen, new = data.get("seen_packages_count"), data.get("new_owned_queue_count")
            if not all(type(n) is int and 0 <= n <= 10_000_000 for n in (seen, new)):
                raise ValueError("invalid sanitized counts")
            return {"connected": True, "seen_packages": seen, "new_owned_queue": new}
        seen, new = data.get("SeenPackages"), data.get("NewOwnedPackages")
        if not isinstance(seen, list) or not isinstance(new, list):
            raise ValueError("missing package arrays")
        return {
            "connected": True,
            "seen_packages": len(seen),
            "new_owned_queue": len(new),
        }
    except (OSError, ValueError, UnicodeError):
        return {"connected": False, "error": "ASF snapshot unavailable"}


def steamgifts_wins(limit=8):
    # SteamSelfGifter snapshot is read-only; do not expose any account data.
    path = os.environ.get("STEAMGIFTS_DB", "").strip()
    if not path or not os.path.isfile(path):
        return None
    conn = None
    try:
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT code,game_name,won_at FROM giveaways "
            "WHERE is_won=1 AND code IS NOT NULL AND code != '' "
            "ORDER BY won_at DESC LIMIT ?",
            (max(1, min(1000, int(limit))),),
        ).fetchall()
        return [{"code": str(r["code"]), "game": str(r["game_name"] or "Unknown game")[:160],
                 "won_at": str(r["won_at"] or "")[:40]} for r in rows]
    except (OSError, sqlite3.Error, ValueError):
        return None
    finally:
        if conn is not None:
            conn.close()


def notify_new_wins():
    # First valid snapshot establishes a silent baseline; duplicates are persisted locally.
    if not (NOTIFY_NEW_WINS and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return {"enabled": False}
    rows = steamgifts_wins(limit=1000)
    if rows is None:
        return {"enabled": True, "available": False}
    with _db_lock, db() as conn:
        initialized = conn.execute(
            "SELECT value FROM meta WHERE key='win_notifications_initialized'"
        ).fetchone()
        if initialized is None:
            conn.executemany(
                "INSERT OR IGNORE INTO win_notifications VALUES(?,?)",
                [(r["code"], now_iso()) for r in rows],
            )
            conn.execute("INSERT INTO meta(key,value) VALUES('win_notifications_initialized','1')")
            return {"enabled": True, "baseline": True}
        seen = {row[0] for row in conn.execute("SELECT giveaway_code FROM win_notifications")}
    new = [r for r in rows if r["code"] not in seen]
    if not new:
        return {"enabled": True, "sent": 0}
    lines = ["🏆 <b>FreebieHarbor: " + str(len(new)) + " new SteamGifts win(s)</b>"]
    for item in new[:6]:
        code = item["code"]
        name = html.escape(item["game"])
        if re.fullmatch(r"[A-Za-z0-9]{5}", code):
            lines.append("• <a href='https://www.steamgifts.com/giveaway/" +
                         code + "/'>" + name + "</a>")
        else:
            lines.append("• " + name)
    if len(new) > 6:
        lines.append("… plus " + str(len(new) - 6) + " more")
    try:
        _send_telegram("\n".join(lines))
    except RuntimeError as exc:
        set_meta("win_notification_error", str(exc))
        return {"enabled": True, "sent": 0, "error": str(exc)}
    with _db_lock, db() as conn:
        conn.executemany("INSERT OR IGNORE INTO win_notifications VALUES(?,?)",
                         [(r["code"], now_iso()) for r in new])
    set_meta("win_notification_error", "")
    set_meta("last_win_notification_at", now_iso())
    return {"enabled": True, "sent": len(new)}


def render_rewards_content():
    steam, asf = rewards_summary(), asf_summary()
    wins = steamgifts_wins()
    win_rows = "".join(
        '<div class="list-row"><div class="row-main"><b>' + html.escape(win["game"]) +
        '</b><div class="text-sm muted">' + html.escape(win["won_at"]) + '</div></div>' +
        '<a class="btn btn-ghost btn-sm" target="_blank" rel="noopener noreferrer" ' +
        'href="https://www.steamgifts.com/giveaway/' +
        html.escape(win["code"]) + '/">View</a></div>'
        for win in (wins or []) if re.fullmatch(r"[A-Za-z0-9]{5}", win["code"])
    ) or '<div class="empty">No recorded wins, or no SteamGifts snapshot connected.</div>'
    details = "SteamGifts snapshot connected" if steam.get("connected") else "SteamGifts not connected"
    packages = "ASF plugin snapshot connected" if asf.get("connected") else "ASF plugin not connected"
    return f'''<div class="page"><div class="page-title"><h2>Reward tracking</h2></div>
    <div class="stats-grid">
    {_stat_card("Giveaway entries",steam.get("entered","—"),"blue")}
    {_stat_card("SteamGifts wins",steam.get("wins","—"),"green")}
    {_stat_card("ASF packages seen",asf.get("seen_packages","—"),"teal")}
    {_stat_card("ASF new-owned queue",asf.get("new_owned_queue","—"),"purple")}
    </div><div class="card"><div class="card-body">
    <p>{details} · {packages}</p>
    <p class="text-sm muted">These are read-only snapshot statistics. ASF plugin counters are not
    proof of redeemed games or Steam library ownership.</p></div></div>
    <div class="card"><div class="card-header"><h3>Recent recorded wins</h3></div>
    <div class="card-body list-rows">{win_rows}</div></div></div>'''


def render_dashboard_content(status):
    rewards = rewards_summary()
    sources = external_source_counts()
    offers, total, _, _ = external_offers_data(page=1, page_size=6)
    cards = "".join(_external_card(item, "/offers") for item in offers) if offers else '<div class="card empty">No active offers yet. Try Refresh.</div>'
    last = html.escape(str(get_meta("last_refresh_at") or "Not yet"))
    status_line = " · ".join(html.escape(k)+": "+str(v) for k, v in sorted(sources.items())) or "First update pending"
    provider_names = ("gamerpower", "freetokeep", "ggdeals", "alienware", "indiegala")
    failed_sources = [name for name in provider_names if get_meta("poll_error:" + name, "")]
    source_warning = (
        '<p class="text-sm" style="color:var(--danger)">Source checks failed: ' +
        html.escape(", ".join(failed_sources)) + '</p>'
        if failed_sources else ""
    )
    demo_note = '<div class="status-banner warning">Sample data: fictional offers for UI preview.</div>' if get_meta("demo_mode") == "1" else ""
    demo_enabled = get_meta("demo_mode") == "1"
    refreshing = _poll_lock.locked()
    refresh_action = (
        '<span class="badge badge-info">Offline preview</span>'
        if demo_enabled else
        '<span class="badge badge-info">Refreshing sources…</span>'
        if refreshing else
        '<form method="post" action="/refresh"><button class="btn btn-primary btn-md">Refresh now</button></form>'
    )
    offer_label = "Sample offers" if demo_enabled else "Live offers"
    return f'''<div class="page"><div class="page-title"><h2>Free game radar</h2>{refresh_action}</div>{demo_note}<div class="stats-grid">{_stat_card(offer_label,total,"blue")}{_stat_card("Sources",len(sources),"green")}{_stat_card("SteamGifts entries",rewards.get("entered","—"),"teal")}{_stat_card("SteamGifts wins",rewards.get("wins","—"),"purple")}</div><div class="card"><div class="card-body"><b>Source status</b><p class="text-sm muted">Active offers: {status_line}</p>{source_warning}</div></div><div class="page-title"><h2>New offers</h2><a class="btn btn-ghost btn-sm" href="/offers">Browse all</a></div><div class="offer-grid">{cards}</div></div>'''


def render_offers_content(query):
    page=_page_number(query); source=_query_text(query,"source","all") or "all"; search=_query_text(query,"q"); view = "history" if _query_text(query,"view") == "history" else "active"; rows,total,page,pages=external_offers_data(page=page,page_size=20,source=source,search=search,view=view); params={"source":source,"q":search,"view":view}; return_to=_page_href("/offers",page,params); cards="".join(_external_card(r,return_to) for r in rows) if rows else '<div class="card empty" style="grid-column:1/-1">No offers match these filters.</div>'; source_options=[("all","All sources"),("gamerpower","GamerPower"),("freetokeep","FreeToKeep"),("ggdeals","GG.deals"),("alienware","Alienware Arena")]; options="".join(f'<option value="{v}"{" selected" if source==v else ""}>{label}</option>' for v,label in source_options)
    tabs = "".join(f'<a class="btn btn-ghost btn-sm" href="{html.escape(_page_href("/offers",1,{"source":source,"q":search,"view":mode}),quote=True)}">{label}</a>' for mode,label in (("active","Current offers"),("history","History")))
    return f'''<div class="page"><div class="page-title"><h2>Free game offers</h2><form class="search-form" method="get" action="/offers"><input class="input" type="search" name="q" placeholder="Search offers..." value="{html.escape(search, quote=True)}"><input type="hidden" name="source" value="{html.escape(source, quote=True)}"><input type="hidden" name="view" value="{view}"><button class="btn btn-secondary btn-md" type="submit">Search</button></form></div>{tabs}<form class="filters" method="get" action="/offers"><div class="filter-chip">{svg_icon('gift',16)}<span class="text-sm muted">Source:</span><select class="select" name="source" onchange="this.form.submit()">{options}</select></div><input type="hidden" name="view" value="{view}">{f'<input type="hidden" name="q" value="{html.escape(search, quote=True)}">' if search else ''}</form><div class="results-count">Showing page {page} of {pages} · {total} offer{'' if total==1 else 's'}</div><div class="offer-grid">{cards}</div>{pagination_html('/offers',page,pages,total,20,params)}</div>'''






def render_page(path, query):
    status=None
    if path in {"/","/dashboard"}: return render_shell("dashboard",render_dashboard_content(status),status,"FreebieHarbor · Dashboard")
    if path=="/offers": return render_shell("offers",render_offers_content(query),status,"FreebieHarbor · External Offers")
    if path=="/rewards": return render_shell("rewards",render_rewards_content(),status,"FreebieHarbor · Rewards")
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _host_allowed(self):
        try:
            host = urlparse("http://" + self.headers.get("Host", "")).hostname
        except ValueError:
            return False
        return bool(host and host.lower() in ALLOWED_HOSTS)

    def _post_origin_allowed(self):
        origin = self.headers.get("Origin", "")
        try:
            parsed = urlparse(origin)
            return parsed.scheme in {"http", "https"} and (
                parsed.netloc.lower() == self.headers.get("Host", "").lower()
            )
        except ValueError:
            return False

    def do_GET(self):
        if not self._host_allowed():
            self.send(403, "Unknown host", "text/plain")
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/health":
            self.send(200, json.dumps({
                "ok": True,
                "version": APP_VERSION,
                "refreshing": _poll_lock.locked(),
                "last_refresh_at": get_meta("last_refresh_at"),
                "alienware_direct_state": get_meta("alienware_direct_state", "unknown"),
                "poll_errors": {
                    name: get_meta(f"poll_error:{name}", "")
                    for name in ("gamerpower", "freetokeep", "ggdeals", "alienware", "indiegala")
                    if get_meta(f"poll_error:{name}", "")
                },
            }), "application/json")
            return
        if path == "/api/rewards":
            self.send(200, json.dumps({"steamgifts": rewards_summary(), "asf": asf_summary()}), "application/json")
            return
        if path == "/api/wins":
            self.send(200, json.dumps({"wins": steamgifts_wins() or []}), "application/json")
            return
        if path == "/api/external-offers":
            with _db_lock, db() as c:
                rows = [dict(x) for x in c.execute("SELECT * FROM external_offers WHERE active=1 ORDER BY first_seen DESC LIMIT 250").fetchall()]
            self.send(200, json.dumps({"offers": rows}, ensure_ascii=False), "application/json; charset=utf-8")
            return
        body = render_page(path, query)
        if body is not None:
            self.send(200, body)
            return
        self.send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._host_allowed() or not self._post_origin_allowed():
            self.send(403, "Invalid host or origin", "text/plain")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/x-www-form-urlencoded":
            self.send(415, "Expected form data", "text/plain")
            return
        path = urlparse(self.path).path
        max_body = 8192
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self.send(400, "Invalid content length", "text/plain")
            return
        if not 0 <= n <= max_body:
            self.send(413, "Request too large", "text/plain")
            return
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace")) if n else {}
        raw_return_to = (form.get("return_to") or [""])[0]

        if path == "/external-state":
            oid = (form.get("id") or [""])[0]
            state = (form.get("state") or [""])[0]
            if state not in {"new", "claimed", "ignored"}:
                self.send(400, "Bad state", "text/plain")
                return
            with _db_lock, db() as c:
                c.execute("UPDATE external_offers SET user_state=? WHERE offer_id=?", (state, oid))
            self.redirect(_safe_return_to(raw_return_to, "/offers"))
            return

        if path == "/refresh":
            schedule_refresh()
            self.redirect(_safe_return_to(raw_return_to, "/dashboard"))
            return

        self.send(404, "Not found", "text/plain")

def main():
    init_db()
    if get_meta("demo_mode") != "1":
        t=threading.Thread(target=poll_loop,name="poller",daemon=True); t.start()
    else:
        print("FreebieHarbor demo preview; source polling disabled", flush=True)
    server=ThreadingHTTPServer((BIND_HOST,PORT),Handler)
    print(f"FreebieHarbor v{APP_VERSION} listening on {BIND_HOST}:{PORT}",flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
