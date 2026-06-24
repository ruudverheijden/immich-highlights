import os
import tempfile
from src.db import (
    get_album_mapping,
    get_processed_asset,
    init_db,
    upsert_album_mapping,
    upsert_processed_asset,
)


def test_db_upsert_and_get():
    """The DB layer should round-trip scorer fields without callers parsing SQL."""
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, "test.db")
    conn = init_db(db_path)
    upsert_processed_asset(conn, "a1", "chksum1", 77, {"iso": 100}, 123.4, 0, 5)
    row = get_processed_asset(conn, "a1")
    assert row is not None
    assert row["asset_id"] == "a1"
    assert row["checksum"] == "chksum1"
    assert row["score"] == 77
    assert row["rating"] == 5
    conn.close()


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
