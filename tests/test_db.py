import os
import tempfile
from src.db import init_db, upsert_processed_asset, get_processed_asset


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
