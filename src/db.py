import json
import sqlite3
from pathlib import Path

# `processed_assets` is kept for the current cache/export flow. The stage tables
# below are the pipeline-oriented shape we will migrate modules toward.
SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    -- Immich-sourced asset metadata from the asset discovery stage.
    asset_id TEXT PRIMARY KEY,
    checksum TEXT,
    rating INTEGER,
    exif_json TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS processed_assets (
    -- Compatibility cache used by the current scorer and review export.
    asset_id TEXT PRIMARY KEY,
    checksum TEXT,
    score INTEGER,
    exif_json TEXT,
    rating INTEGER,
    score_details_json TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS technical_analysis (
    -- Objective image facts computed from the downloaded preview.
    asset_id TEXT PRIMARY KEY,
    checksum TEXT,
    blur_variance REAL,
    brightness REAL,
    contrast REAL,
    phash TEXT,
    portrait_quality INTEGER,
    details_json TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semantic_analysis (
    -- Meaningful metadata from Immich and lightweight semantic extraction.
    asset_id TEXT PRIMARY KEY,
    checksum TEXT,
    rating INTEGER,
    face_count INTEGER,
    face_quality INTEGER,
    iso REAL,
    exposure_seconds REAL,
    has_location INTEGER,
    is_favorite INTEGER,
    is_edited INTEGER,
    content_labels_json TEXT,
    content_filter_matches_json TEXT,
    details_json TEXT,
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS asset_filter_results (
    -- Per-album filtering decisions made before analysis and scoring.
    asset_id TEXT,
    album_bucket TEXT,
    included INTEGER,
    reason TEXT,
    details_json TEXT,
    filtered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id, album_bucket),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_asset_filter_results_album_included
ON asset_filter_results(album_bucket, included);

CREATE TABLE IF NOT EXISTS asset_scores (
    -- Explainable score output for a specific scoring context.
    asset_id TEXT,
    album_bucket TEXT DEFAULT 'global',
    score INTEGER,
    raw_score INTEGER,
    components_json TEXT,
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (asset_id, album_bucket),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_asset_scores_album_score
ON asset_scores(album_bucket, score DESC);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    -- Future near-duplicate groups, for example based on perceptual hashes.
    group_id TEXT PRIMARY KEY,
    representative_asset_id TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(representative_asset_id) REFERENCES assets(asset_id)
);

CREATE TABLE IF NOT EXISTS duplicate_group_members (
    group_id TEXT,
    asset_id TEXT,
    distance INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (group_id, asset_id),
    FOREIGN KEY(group_id) REFERENCES duplicate_groups(group_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
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


def without_none_values(mapping: dict) -> dict:
    """Drop absent optional stage values before feeding scoring helpers."""
    return {key: value for key, value in mapping.items() if value is not None}


def init_db(db_path: str):
    """Create the SQLite database and return an open connection."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()
    return conn


def upsert_asset_record(conn, asset_id, checksum, exif, rating):
    """Store Immich-sourced asset metadata for the discovery stage."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO assets (asset_id, checksum, rating, exif_json, synced_at) "
        "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(asset_id) DO UPDATE SET "
        "checksum=excluded.checksum, "
        "rating=excluded.rating, "
        "exif_json=excluded.exif_json, "
        "synced_at=CURRENT_TIMESTAMP",
        (asset_id, checksum, rating, json.dumps(exif or {})),
    )


def upsert_technical_analysis(conn, asset_id, checksum, inputs):
    """Store objective image-analysis facts for later pipeline stages."""
    dimensions = inputs.get("dimensions") or [None, None]
    details = {
        "dimensions": dimensions,
        "subject_sharpness": inputs.get("subject_sharpness"),
        "background_sharpness": inputs.get("background_sharpness"),
        "subject_background_blur_ratio": inputs.get("subject_background_blur_ratio"),
        "subject_box": inputs.get("subject_box"),
    }
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO technical_analysis "
        "(asset_id, checksum, blur_variance, brightness, contrast, phash, "
        "portrait_quality, details_json, analyzed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(asset_id) DO UPDATE SET "
        "checksum=excluded.checksum, "
        "blur_variance=excluded.blur_variance, "
        "brightness=excluded.brightness, "
        "contrast=excluded.contrast, "
        "phash=excluded.phash, "
        "portrait_quality=excluded.portrait_quality, "
        "details_json=excluded.details_json, "
        "analyzed_at=CURRENT_TIMESTAMP",
        (
            asset_id,
            checksum,
            inputs.get("blur_variance"),
            inputs.get("brightness"),
            inputs.get("hist_std"),
            inputs.get("phash"),
            inputs.get("portrait_quality"),
            json.dumps(details),
        ),
    )


def upsert_semantic_analysis(conn, asset_id, checksum, inputs):
    """Store semantic facts and user metadata for later scoring/selection."""
    details = {
        "faces": inputs.get("faces", []),
        "content_filter_penalty": inputs.get("content_filter_penalty", 0),
    }
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO semantic_analysis "
        "(asset_id, checksum, rating, face_count, face_quality, iso, "
        "exposure_seconds, has_location, is_favorite, is_edited, content_labels_json, "
        "content_filter_matches_json, details_json, analyzed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(asset_id) DO UPDATE SET "
        "checksum=excluded.checksum, "
        "rating=excluded.rating, "
        "face_count=excluded.face_count, "
        "face_quality=excluded.face_quality, "
        "iso=excluded.iso, "
        "exposure_seconds=excluded.exposure_seconds, "
        "has_location=excluded.has_location, "
        "is_favorite=excluded.is_favorite, "
        "is_edited=excluded.is_edited, "
        "content_labels_json=excluded.content_labels_json, "
        "content_filter_matches_json=excluded.content_filter_matches_json, "
        "details_json=excluded.details_json, "
        "analyzed_at=CURRENT_TIMESTAMP",
        (
            asset_id,
            checksum,
            inputs.get("rating"),
            inputs.get("face_count"),
            inputs.get("face_quality"),
            inputs.get("iso"),
            inputs.get("exposure_seconds"),
            int(bool(inputs.get("has_location"))),
            int(bool(inputs.get("is_favorite"))),
            int(bool(inputs.get("is_edited"))),
            json.dumps(inputs.get("content_labels", [])),
            json.dumps(inputs.get("content_filter_matches", [])),
            json.dumps(details),
        ),
    )


def upsert_asset_score(conn, asset_id, score_details, album_bucket="global"):
    """Store explainable score output for a scoring context."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO asset_scores "
        "(asset_id, album_bucket, score, raw_score, components_json, calculated_at) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(asset_id, album_bucket) DO UPDATE SET "
        "score=excluded.score, "
        "raw_score=excluded.raw_score, "
        "components_json=excluded.components_json, "
        "calculated_at=CURRENT_TIMESTAMP",
        (
            asset_id,
            album_bucket,
            score_details.get("score"),
            score_details.get("raw_score"),
            json.dumps(score_details.get("components", {})),
        ),
    )


def upsert_asset_filter_result(
    conn,
    asset_id,
    album_bucket,
    included,
    reason,
    details=None,
):
    """Store one filtering-stage decision for an album candidate."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO asset_filter_results "
        "(asset_id, album_bucket, included, reason, details_json, filtered_at) "
        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(asset_id, album_bucket) DO UPDATE SET "
        "included=excluded.included, "
        "reason=excluded.reason, "
        "details_json=excluded.details_json, "
        "filtered_at=CURRENT_TIMESTAMP",
        (
            asset_id,
            album_bucket,
            int(bool(included)),
            reason,
            json.dumps(details or {}),
        ),
    )


def get_asset_filter_result(conn, asset_id, album_bucket):
    """Fetch the filtering-stage decision for one asset/album context."""
    cur = conn.cursor()
    cur.execute(
        "SELECT included, reason, details_json, filtered_at "
        "FROM asset_filter_results WHERE asset_id = ? AND album_bucket = ?",
        (asset_id, album_bucket),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "included": bool(row[0]),
        "reason": row[1],
        "details": json.loads(row[2] or "{}"),
        "filtered_at": row[3],
    }


def get_technical_analysis(conn, asset_id):
    """Fetch stored technical-analysis facts for one asset."""
    cur = conn.cursor()
    cur.execute(
        "SELECT checksum, blur_variance, brightness, contrast, phash, "
        "portrait_quality, details_json FROM technical_analysis WHERE asset_id = ?",
        (asset_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    details = json.loads(row[6] or "{}")
    return without_none_values(
        {
            **details,
            "checksum": row[0],
            "blur_variance": row[1],
            "brightness": row[2],
            "hist_std": row[3],
            "phash": row[4],
            "portrait_quality": row[5],
        }
    )


def get_semantic_analysis(conn, asset_id):
    """Fetch stored semantic-analysis facts for one asset."""
    cur = conn.cursor()
    cur.execute(
        "SELECT checksum, rating, face_count, face_quality, iso, exposure_seconds, "
        "has_location, is_favorite, is_edited, content_labels_json, "
        "content_filter_matches_json, details_json "
        "FROM semantic_analysis WHERE asset_id = ?",
        (asset_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    details = json.loads(row[11] or "{}")
    result = without_none_values(
        {
            **details,
            "checksum": row[0],
            "face_count": row[2],
            "face_quality": row[3],
            "has_location": bool(row[6]),
            "is_favorite": bool(row[7]),
            "is_edited": bool(row[8]),
            "content_labels": json.loads(row[9] or "[]"),
            "content_filter_matches": json.loads(row[10] or "[]"),
        }
    )
    # These keys are required scoring inputs even when the asset is unrated or
    # has no usable EXIF exposure data.
    result["rating"] = row[1]
    result["iso"] = row[4]
    result["exposure_seconds"] = row[5]
    return result


def get_asset_score(conn, asset_id, album_bucket="global"):
    """Fetch stored scoring output for one asset/context."""
    cur = conn.cursor()
    cur.execute(
        "SELECT score, raw_score, components_json, calculated_at "
        "FROM asset_scores WHERE asset_id = ? AND album_bucket = ?",
        (asset_id, album_bucket),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "score": row[0],
        "raw_score": row[1],
        "components": json.loads(row[2] or "{}"),
        "calculated_at": row[3],
    }


def get_scoring_inputs(conn, asset_id):
    """Combine stage outputs into the input shape expected by the scoring engine."""
    technical = get_technical_analysis(conn, asset_id)
    semantic = get_semantic_analysis(conn, asset_id)
    if not technical or not semantic:
        return None
    inputs = {**technical, **semantic}
    inputs.pop("checksum", None)
    return inputs


def upsert_processed_asset(
    conn, asset_id, checksum, score, exif, rating, score_details
):
    """Store the latest score for an asset, replacing stale scan results."""
    cur = conn.cursor()
    # SQLite stores JSON as text; callers receive a dict again in get_processed_asset.
    exif_json = json.dumps(exif or {})
    score_details_json = json.dumps(score_details or {})
    upsert_sql = (
        "INSERT INTO processed_assets "
        "(asset_id, checksum, score, exif_json, rating, score_details_json) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(asset_id) DO UPDATE SET checksum=excluded.checksum, "
        "score=excluded.score, exif_json=excluded.exif_json, "
        "rating=excluded.rating, "
        "score_details_json=excluded.score_details_json, "
        "processed_at=CURRENT_TIMESTAMP"
    )
    cur.execute(
        upsert_sql,
        (asset_id, checksum, score, exif_json, rating, score_details_json),
    )
    inputs = (score_details or {}).get("inputs", {})
    upsert_asset_record(conn, asset_id, checksum, exif, rating)
    upsert_technical_analysis(conn, asset_id, checksum, inputs)
    upsert_semantic_analysis(conn, asset_id, checksum, inputs)
    upsert_asset_score(conn, asset_id, score_details or {})
    conn.commit()


def get_processed_asset(conn, asset_id):
    """Fetch a processed asset row in the shape used by tests and callers."""
    cur = conn.cursor()
    cur.execute(
        "SELECT asset_id, checksum, score, exif_json, rating, score_details_json "
        "FROM processed_assets WHERE asset_id = ?",
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
        "rating": row[4],
        "score_details": json.loads(row[5] or "{}"),
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
