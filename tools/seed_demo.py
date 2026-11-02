"""Create an isolated, clearly fictional dataset for local UI previews.

Usage:
    DATA_DIR=./demo-data python3 tools/seed_demo.py

Never run this against a production database.
"""
import importlib.util
import os
from pathlib import Path

if not os.environ.get("DATA_DIR"):
    raise SystemExit("Set DATA_DIR to a new, disposable demo directory.")

directory = Path(os.environ["DATA_DIR"]).resolve()
if (directory / "giveaways.db").exists():
    raise SystemExit("Refusing to overwrite an existing database.")

spec = importlib.util.spec_from_file_location(
    "freebieharbor_demo", Path(__file__).resolve().parents[1] / "app" / "hub.py"
)
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)
hub.init_db()

samples = [
    ("Stellar Cartographer", "Strategy", "Steam", "19.99"),
    ("The Last Lighthouse", "Adventure", "PC", "14.99"),
    ("Pixel Farm Chronicles", "Simulation", "Steam", "11.99"),
    ("Orbital Trails", "Puzzle", "Steam", "9.99"),
    ("Ember Islands", "RPG", "PC", "24.99"),
    ("Clockwork Gardens", "Indie", "Steam", "12.99"),
    ("Lunar Archive", "Mystery", "Steam", "16.99"),
    ("Cobalt City", "Platformer", "PC", "7.99"),
]
with hub.db() as conn:
    for number, (title, genre, platform, price) in enumerate(samples, 1):
        hub._external_upsert(conn, {
            "offer_id": "demo:" + str(number),
            "source": "demo",
            "title": "DEMO · " + title,
            "url": "https://example.org/demo/" + str(number),
            "image_url": "",
            "offer_type": genre + " · free to keep",
            "platform": platform,
            "provider": "Example source",
            "description": "Fictional sample listing for the FreebieHarbor preview.",
            "instructions": "Demo only. No game is actually available at this URL.",
            "original_price": price,
            "published_at": hub.now_iso(),
            "starts_at": "",
            "ends_at": "",
            "stamp": hub.now_iso(),
        })
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('demo_mode','1')")
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_refresh_at',?)",
                 (hub.now_iso(),))
print("Seeded", len(samples), "fictional offers in", directory)
