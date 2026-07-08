"""Tests for pHash and timestamp-confirmed duplicate detection."""

from src.db import get_duplicate_groups, init_db, upsert_processed_asset
from src.duplicate_detection import (
    deduplicate_scored_assets,
    duplicate_groups_from_immich,
    duplicate_groups_from_phashes,
    duplicate_groups_from_timestamps,
    parse_asset_timestamp,
    phash_hamming_distance,
)


def store_phash(conn, asset_id, phash, taken_at=None):
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
        taken_at=taken_at,
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


def test_parse_asset_timestamp_supports_immich_and_exif_shapes():
    """Timestamp parsing should handle common Immich ISO and EXIF strings."""
    assert parse_asset_timestamp("2026-06-24T19:15:30.000Z").year == 2026
    assert parse_asset_timestamp("2026:06:24 19:15:30").month == 6
    assert parse_asset_timestamp("not-a-date") is None


def test_timestamp_groups_require_close_time_and_similar_phash():
    """Timestamp proximity should not suppress visually different photos."""
    scored = [("a", 90), ("b", 80), ("c", 70)]
    phashes = {
        "a": "0000",
        "b": "000f",
        "c": "ffff",
    }
    taken_at = {
        "a": parse_asset_timestamp("2026-06-24T19:15:30Z"),
        "b": parse_asset_timestamp("2026-06-24T19:15:31Z"),
        "c": parse_asset_timestamp("2026-06-24T19:15:31Z"),
    }

    groups = duplicate_groups_from_timestamps(
        scored,
        phashes,
        taken_at,
        window_seconds=2,
        phash_threshold=4,
        album_bucket="last-week",
    )

    assert groups == [
        {
            "group_id": "last-week:timestamp:1:a",
            "representative_asset_id": "a",
            "reason": "timestamp<=2s+phash_distance<=4",
            "members": [
                {"asset_id": "a", "distance": 0, "score": 90},
                {"asset_id": "b", "distance": 4, "score": 80},
            ],
        }
    ]


def test_timestamp_groups_ignore_similar_photos_outside_window():
    """Similar photos should not use timestamp grouping when time is too far apart."""
    scored = [("a", 90), ("b", 80)]
    phashes = {"a": "0000", "b": "000f"}
    taken_at = {
        "a": parse_asset_timestamp("2026-06-24T19:15:30Z"),
        "b": parse_asset_timestamp("2026-06-24T19:15:40Z"),
    }

    assert (
        duplicate_groups_from_timestamps(
            scored,
            phashes,
            taken_at,
            window_seconds=2,
            phash_threshold=4,
            album_bucket="last-week",
        )
        == []
    )


def test_immich_duplicate_groups_are_scoped_to_scored_candidates():
    """Immich groups should suppress only assets eligible for the current album."""
    scored = [("a", 70), ("b", 90), ("c", 80)]
    immich_groups = [
        {
            "duplicateId": "immich-group-1",
            "suggestedKeepAssetIds": ["a"],
            "assets": [
                {"id": "a"},
                {"id": "b"},
                {"id": "outside-window"},
            ],
        },
        {
            "duplicateId": "immich-group-2",
            "assets": [
                {"id": "c"},
                {"id": "outside-window-2"},
            ],
        },
    ]

    groups = duplicate_groups_from_immich(
        scored,
        immich_groups,
        album_bucket="last-week",
    )

    assert groups == [
        {
            "group_id": "last-week:immich:immich-group-1",
            "representative_asset_id": "b",
            "reason": "immich_duplicate",
            "members": [
                {"asset_id": "a", "distance": None, "score": 70},
                {"asset_id": "b", "distance": 0, "score": 90},
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


def test_deduplicate_scored_assets_uses_timestamp_confirmed_duplicates(tmp_path):
    """Timestamp grouping can catch burst duplicates beyond the strict pHash limit."""
    conn = init_db(str(tmp_path / "test.db"))
    store_phash(conn, "a", "0000", taken_at="2026-06-24T19:15:30Z")
    store_phash(conn, "b", "000f", taken_at="2026-06-24T19:15:31Z")
    store_phash(conn, "c", "ffff", taken_at="2026-06-24T19:15:31Z")

    deduplicated = deduplicate_scored_assets(
        conn,
        "last-week",
        [("a", 90), ("b", 80), ("c", 70)],
        enabled=True,
        threshold=1,
        timestamp_enabled=True,
        timestamp_window_seconds=2,
        timestamp_phash_threshold=4,
    )

    assert deduplicated == [("a", 90), ("c", 70)]
    groups = get_duplicate_groups(conn, "last-week")
    assert groups[0]["reason"] == "timestamp<=2s+phash_distance<=4"


def test_deduplicate_scored_assets_uses_immich_duplicate_groups(tmp_path):
    """Immich duplicate groups should be an additional post-scoring signal."""
    conn = init_db(str(tmp_path / "test.db"))
    store_phash(conn, "a", "0000")
    store_phash(conn, "b", "ffff")
    store_phash(conn, "c", "00f0")

    deduplicated = deduplicate_scored_assets(
        conn,
        "last-week",
        [("a", 90), ("b", 80), ("c", 70)],
        enabled=True,
        threshold=1,
        timestamp_enabled=False,
        immich_duplicate_groups=[
            {
                "duplicateId": "immich-group-1",
                "assets": [{"id": "a"}, {"id": "b"}],
                "suggestedKeepAssetIds": ["b"],
            }
        ],
    )

    assert deduplicated == [("a", 90), ("c", 70)]
    groups = get_duplicate_groups(conn, "last-week")
    assert groups[0]["reason"] == "immich_duplicate"
    assert groups[0]["members"] == [
        {"asset_id": "a", "distance": 0},
        {"asset_id": "b", "distance": None},
    ]
