import os
import tempfile
import json
from src.db import (
    get_asset_filter_result,
    get_asset_score,
    get_album_mapping,
    get_duplicate_groups,
    get_processed_asset,
    get_scoring_inputs,
    get_semantic_analysis,
    get_technical_analysis,
    init_db,
    upsert_asset_filter_result,
    upsert_album_mapping,
    upsert_processed_asset,
    replace_duplicate_groups,
)


def table_names(conn):
    """Return user table names from SQLite."""
    cur = conn.cursor()
    cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cur.fetchall()}


def column_names(conn, table_name):
    """Return column names for a SQLite table."""
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def test_db_upsert_and_get():
    """The DB layer should round-trip scorer fields without callers parsing SQL."""
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    conn = init_db(db_path)
    score_details = {
        "score": 77,
        "components": {"blur": 10},
        "inputs": {"blur_variance": 123.4, "face_count": 0},
    }
    upsert_processed_asset(conn, "a1", "chksum1", 77, {"iso": 100}, 5, score_details)
    row = get_processed_asset(conn, "a1")
    assert row is not None
    assert row["asset_id"] == "a1"
    assert row["checksum"] == "chksum1"
    assert row["score"] == 77
    assert row["rating"] == 5
    assert row["score_details"] == score_details
    conn.close()


def test_init_db_creates_pipeline_stage_tables(tmp_path):
    """The DB schema should expose separate storage for pipeline stage outputs."""
    conn = init_db(str(tmp_path / "test.db"))

    assert {
        "assets",
        "technical_analysis",
        "semantic_analysis",
        "asset_filter_results",
        "asset_scores",
        "duplicate_groups",
        "duplicate_group_members",
    }.issubset(table_names(conn))
    assert "inputs_json" not in column_names(conn, "asset_scores")
    assert "taken_at" in column_names(conn, "assets")
    assert "album_bucket" in column_names(conn, "duplicate_groups")


def test_asset_filter_result_upsert_and_get(tmp_path):
    """Filtering decisions should be queryable per album bucket."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(
        conn,
        "a1",
        "checksum-1",
        88,
        {},
        None,
        {"score": 88},
        taken_at="2026-06-24T19:15:30Z",
    )

    upsert_asset_filter_result(
        conn,
        "a1",
        "last-week",
        included=True,
        reason="accepted_timeline_image_candidate",
        details={"album_name": "Highlights: Last Week"},
    )
    conn.commit()

    row = get_asset_filter_result(conn, "a1", "last-week")
    assert row["included"] is True
    assert row["reason"] == "accepted_timeline_image_candidate"
    assert row["details"] == {"album_name": "Highlights: Last Week"}


def test_duplicate_groups_replace_and_get(tmp_path):
    """Duplicate groups should be replaced per album bucket on each run."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(conn, "a1", "checksum-1", 90, {}, None, {"score": 90})
    upsert_processed_asset(conn, "a2", "checksum-2", 80, {}, None, {"score": 80})

    replace_duplicate_groups(
        conn,
        "last-week",
        [
            {
                "group_id": "last-week:phash:1:a1",
                "representative_asset_id": "a1",
                "reason": "phash_distance<=6",
                "members": [
                    {"asset_id": "a1", "distance": 0},
                    {"asset_id": "a2", "distance": 3},
                ],
            }
        ],
    )
    replace_duplicate_groups(conn, "last-week", [])

    assert get_duplicate_groups(conn, "last-week") == []


def test_processed_asset_upsert_populates_stage_tables(tmp_path):
    """Current writes should also populate the new stage-oriented tables."""
    conn = init_db(str(tmp_path / "test.db"))
    score_details = {
        "score": 77,
        "raw_score": 82,
        "components": {"blur": 10, "rating": 15},
        "inputs": {
            "blur_variance": 123.4,
            "brightness": 120.0,
            "hist_std": 42.0,
            "phash": "abc123",
            "portrait_quality": 7,
            "face_count": 2,
            "face_quality": 18,
            "rating": 4,
            "iso": 200,
            "exposure_seconds": 1 / 60,
            "has_location": True,
            "is_favorite": True,
            "is_edited": False,
            "content_labels": ["screenshot"],
            "content_filter_matches": [
                {"label": "screenshot", "query": "screenshot", "rank": 1}
            ],
        },
    }

    upsert_processed_asset(
        conn,
        "a1",
        "checksum-1",
        77,
        {"iso": 100},
        4,
        score_details,
    )

    cur = conn.cursor()
    cur.execute("SELECT asset_id, checksum, rating, taken_at, exif_json FROM assets")
    asset_row = cur.fetchone()
    assert asset_row[:3] == ("a1", "checksum-1", 4)
    assert asset_row[3] is None
    assert json.loads(asset_row[4]) == {"iso": 100}

    cur.execute(
        "SELECT blur_variance, brightness, contrast, phash, portrait_quality "
        "FROM technical_analysis WHERE asset_id = ?",
        ("a1",),
    )
    assert cur.fetchone() == (123.4, 120.0, 42.0, "abc123", 7)

    cur.execute(
        "SELECT rating, face_count, face_quality, iso, exposure_seconds, "
        "has_location, is_favorite, "
        "is_edited, content_labels_json FROM semantic_analysis WHERE asset_id = ?",
        ("a1",),
    )
    semantic_row = cur.fetchone()
    assert semantic_row[:8] == (4, 2, 18, 200.0, 1 / 60, 1, 1, 0)
    assert json.loads(semantic_row[8]) == ["screenshot"]

    cur.execute(
        "SELECT score, raw_score, components_json "
        "FROM asset_scores WHERE asset_id = ? AND album_bucket = ?",
        ("a1", "global"),
    )
    score_row = cur.fetchone()
    assert score_row[:2] == (77, 82)
    assert json.loads(score_row[2]) == {"blur": 10, "rating": 15}

    technical = get_technical_analysis(conn, "a1")
    semantic = get_semantic_analysis(conn, "a1")
    score = get_asset_score(conn, "a1")
    scoring_inputs = get_scoring_inputs(conn, "a1")
    assert technical["phash"] == "abc123"
    assert semantic["iso"] == 200.0
    assert score["components"] == {"blur": 10, "rating": 15}
    assert scoring_inputs["phash"] == "abc123"
    assert scoring_inputs["iso"] == 200.0


def test_album_mapping_upsert_and_get():
    """Generated bucket albums should be remembered across scorer runs."""
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    conn = init_db(db_path)

    upsert_album_mapping(conn, "monthly", "album-1", "Highlights: monthly", ["a1"])
    upsert_album_mapping(conn, "monthly", "album-1", "Highlights: monthly", ["a2"])

    row = get_album_mapping(conn, "monthly")
    assert row is not None
    assert row["album_id"] == "album-1"
    assert row["album_name"] == "Highlights: monthly"
    assert row["asset_ids"] == ["a2"]
    conn.close()
