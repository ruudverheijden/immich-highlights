import os
import tempfile
from src.db import init_db, upsert_processed_asset, get_processed_asset


def test_db_upsert_and_get():
    d = tempfile.mkdtemp()
    db_path = os.path.join(d, 'test.db')
    conn = init_db(db_path)
    upsert_processed_asset(conn, 'a1', 'chksum1', 77, {'iso': 100}, 123.4, 0)
    row = get_processed_asset(conn, 'a1')
    assert row is not None
    assert row['asset_id'] == 'a1'
    assert row['checksum'] == 'chksum1'
    assert row['score'] == 77
    conn.close()
