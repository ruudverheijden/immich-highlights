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
    rating INTEGER,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS duplicates (
    -- Reserved for future perceptual-hash/GPS duplicate detection.
    primary_asset_id TEXT,
    duplicate_asset_id TEXT,
    reason TEXT,
    hamming_distance INTEGER,
    gps_distance_meters REAL,
    PRIMARY KEY (primary_asset_id, duplicate_asset_id)
);

CREATE TABLE IF NOT EXISTS album_mappings (
    -- Maps generated scorer buckets to Immich albums so reruns update albums.
    album_id TEXT PRIMARY KEY,
    album_name TEXT,
    asset_ids_json TEXT,
    bucket TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_album_mappings_bucket
ON album_mappings(bucket);

CREATE TABLE IF NOT EXISTS sync_log (
    -- Reserved for tracking scheduled runs and API write outcomes.
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
    ensure_processed_assets_columns(cur)
    conn.commit()
    return conn


def ensure_processed_assets_columns(cur):
    """Add columns introduced after the initial schema to existing databases."""
    cur.execute("PRAGMA table_info(processed_assets)")
    columns = {row[1] for row in cur.fetchall()}
    if "rating" not in columns:
        cur.execute("ALTER TABLE processed_assets ADD COLUMN rating INTEGER")


def upsert_processed_asset(conn, asset_id, checksum, score, exif, blur, faces, rating):
    """Store the latest score for an asset, replacing stale scan results."""
    cur = conn.cursor()
    # SQLite stores JSON as text; callers receive a dict again in get_processed_asset.
    exif_json = json.dumps(exif or {})
    upsert_sql = (
        "INSERT INTO processed_assets (asset_id, checksum, score, exif_json, "
        "blur_variance, face_count, rating) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET checksum=excluded.checksum, "
        "score=excluded.score, exif_json=excluded.exif_json, "
        "blur_variance=excluded.blur_variance, "
        "face_count=excluded.face_count, rating=excluded.rating, "
        "processed_at=CURRENT_TIMESTAMP"
    )
    cur.execute(
        upsert_sql,
        (asset_id, checksum, score, exif_json, blur, faces, rating),
    )
    conn.commit()


def get_processed_asset(conn, asset_id):
    """Fetch a processed asset row in the shape used by tests and callers."""
    cur = conn.cursor()
    cur.execute(
        "SELECT asset_id, checksum, score, exif_json, blur_variance, "
        "face_count, rating FROM processed_assets WHERE asset_id = ?",
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
        "rating": row[6],
    }


def get_album_mapping(conn, bucket):
    """Fetch the Immich album previously created for a scorer bucket."""
    cur = conn.cursor()
    cur.execute(
        "SELECT album_id, album_name, asset_ids_json, bucket, generated_at "
        "FROM album_mappings WHERE bucket = ?",
        (bucket,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "album_id": row[0],
        "album_name": row[1],
        "asset_ids": json.loads(row[2] or "[]"),
        "bucket": row[3],
        "generated_at": row[4],
    }


def upsert_album_mapping(conn, bucket, album_id, album_name, asset_ids):
    """Remember which Immich album belongs to a generated scorer bucket."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO album_mappings "
        "(album_id, album_name, asset_ids_json, bucket, generated_at) "
        "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(bucket) DO UPDATE SET "
        "album_id=excluded.album_id, "
        "album_name=excluded.album_name, "
        "asset_ids_json=excluded.asset_ids_json, "
        "generated_at=CURRENT_TIMESTAMP",
        (album_id, album_name, json.dumps(asset_ids or []), bucket),
    )
    conn.commit()
