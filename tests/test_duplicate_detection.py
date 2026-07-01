from src.db import get_duplicate_groups, init_db, upsert_processed_asset
from src.duplicate_detection import (
    deduplicate_scored_assets,
    duplicate_groups_from_phashes,
    phash_hamming_distance,
)


def store_phash(conn, asset_id, phash):
    """Store enough stage data for duplicate detection tests."""
    upsert_processed_asset(
        conn,
        asset_id,
        f"checksum-{asset_id}",
        80,
        {},
        None,
        {
            "score": 80,
            "inputs": {"phash": phash},
        },
    )


def test_phash_hamming_distance_counts_different_bits():
    """pHash distance should compare hexadecimal hashes as bits."""
    assert phash_hamming_distance("0000", "000f") == 4
    assert phash_hamming_distance("ffff", "ffff") == 0


def test_duplicate_groups_are_anchored_to_best_scoring_representative():
    """Grouping should avoid chain matches that drift from the representative."""
    scored = [("a", 90), ("b", 80), ("c", 70)]
    phashes = {
        "a": "0000",
        "b": "0001",
        # c is close to b, but not close enough to the best representative a.
        "c": "0003",
    }

    groups = duplicate_groups_from_phashes(
        scored,
        phashes,
        threshold=1,
        album_bucket="last-week",
    )

    assert groups == [
        {
            "group_id": "last-week:phash:1:a",
            "representative_asset_id": "a",
            "reason": "phash_distance<=1",
            "members": [
                {"asset_id": "a", "distance": 0, "score": 90},
                {"asset_id": "b", "distance": 1, "score": 80},
            ],
        }
    ]


def test_deduplicate_scored_assets_persists_groups_and_suppresses_duplicates(
    tmp_path,
):
    """Duplicate detection should keep the best asset and store the group."""
    conn = init_db(str(tmp_path / "test.db"))
    store_phash(conn, "a", "0000")
    store_phash(conn, "b", "0001")
    store_phash(conn, "c", "00f0")

    deduplicated = deduplicate_scored_assets(
        conn,
        "last-week",
        [("a", 90), ("b", 80), ("c", 70)],
        enabled=True,
        threshold=1,
    )

    assert deduplicated == [("a", 90), ("c", 70)]
    groups = get_duplicate_groups(conn, "last-week")
    assert groups[0]["representative_asset_id"] == "a"
    assert groups[0]["members"] == [
        {"asset_id": "a", "distance": 0},
        {"asset_id": "b", "distance": 1},
    ]


def test_disabled_duplicate_detection_clears_existing_groups(tmp_path):
    """Disabled duplicate detection should not leave stale stored groups."""
    conn = init_db(str(tmp_path / "test.db"))
    store_phash(conn, "a", "0000")
    store_phash(conn, "b", "0001")
    deduplicate_scored_assets(
        conn,
        "last-week",
        [("a", 90), ("b", 80)],
        enabled=True,
        threshold=1,
    )

    deduplicated = deduplicate_scored_assets(
        conn,
        "last-week",
        [("a", 90), ("b", 80)],
        enabled=False,
        threshold=1,
    )

    assert deduplicated == [("a", 90), ("b", 80)]
    assert get_duplicate_groups(conn, "last-week") == []
