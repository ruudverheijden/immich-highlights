import sqlite3
import json
from pathlib import Path

# The schema is intentionally compact: processed_assets powers idempotent rescans,
# while the other tables reserve space for duplicate tracking and generated albums.
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS processed_assets (
    asset_id TEXT PRIMARY KEY,
    checksum TEXT,
    score INTEGER,
    exif_json TEXT,
    blur_variance REAL,
    face_count INTEGER,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duplicates (
    primary_asset_id TEXT,
    duplicate_asset_id TEXT,
    reason TEXT,
    hamming_distance INTEGER,
    gps_distance_meters REAL,
    PRIMARY KEY (primary_asset_id, duplicate_asset_id)
);

CREATE TABLE IF NOT EXISTS album_mappings (
    album_id TEXT PRIMARY KEY,
    album_name TEXT,
    asset_ids_json TEXT,
    bucket TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT,
    album_id TEXT,
    asset_count INTEGER,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(db_path: str):
    """Create the SQLite database and return an open connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_processed_asset(conn, asset_id, checksum, score, exif, blur, faces):
    """Store the latest score for an asset, replacing stale scan results."""
    cur = conn.cursor()
    # SQLite stores JSON as text; callers receive a dict again in get_processed_asset.
    exif_json = json.dumps(exif or {})
    upsert_sql = (
        "INSERT INTO processed_assets (asset_id, checksum, score, exif_json, "
        "blur_variance, face_count) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET checksum=excluded.checksum, "
        "score=excluded.score, exif_json=excluded.exif_json, "
        "blur_variance=excluded.blur_variance, "
        "face_count=excluded.face_count, processed_at=CURRENT_TIMESTAMP"
    )
    cur.execute(
        upsert_sql,
        (asset_id, checksum, score, exif_json, blur, faces),
    )
    conn.commit()


def get_processed_asset(conn, asset_id):
    """Fetch a processed asset row in the shape used by tests and callers."""
    cur = conn.cursor()
    cur.execute(
        "SELECT asset_id, checksum, score, exif_json, blur_variance, "
        "face_count FROM processed_assets WHERE asset_id = ?",
        (asset_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "asset_id": row[0],
        "checksum": row[1],
        "score": row[2],
        "exif": json.loads(row[3] or "{}"),
        "blur_variance": row[4],
        "face_count": row[5],
    }
