from datetime import datetime, timezone

from src.album_generator import generate_album_for_rule, score_or_reuse_asset
from src.album_rules import AlbumRule
from src.db import init_db, upsert_processed_asset


class FakeClient:
    """Small Immich client double for album-generator tests."""

    def __init__(self):
        self.iter_calls = []
        self.metadata_calls = []

    def iter_assets(self, page_size, max_assets, taken_after=None, taken_before=None):
        self.iter_calls.append(
            {
                "page_size": page_size,
                "max_assets": max_assets,
                "taken_after": taken_after,
                "taken_before": taken_before,
            }
        )
        return iter(
            [
                {"id": "a1", "checksum": "checksum-1"},
                {"id": "a2", "checksum": "checksum-2"},
            ]
        )

    def get_asset_metadata(self, asset_id):
        self.metadata_calls.append(asset_id)
        return {"id": asset_id, "checksum": f"checksum-{asset_id[-1]}"}


class FakeAlbumManager:
    """Capture generated album requests without calling Immich."""

    def __init__(self):
        self.calls = []

    def ensure_album(self, name, asset_ids, description="", bucket=""):
        self.calls.append(
            {
                "name": name,
                "asset_ids": asset_ids,
                "description": description,
                "bucket": bucket,
            }
        )
        return {"id": "album-1", "albumName": name, "asset_count": len(asset_ids)}


def make_rule():
    return AlbumRule(
        name="Highlights: Last Week",
        bucket="last-week",
        taken_after=datetime(2026, 6, 18, tzinfo=timezone.utc),
        taken_before=datetime(2026, 6, 25, tzinfo=timezone.utc),
        limit=1,
        max_candidates=50,
    )


def test_score_or_reuse_asset_uses_cached_score_when_checksum_matches(tmp_path):
    """A cached score avoids downloading and re-analyzing the same photo."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(conn, "a1", "checksum-1", 88, {}, None, {"score": 88})
    client = FakeClient()

    result = score_or_reuse_asset(
        client,
        conn,
        {"id": "a1", "checksum": "checksum-1"},
        str(tmp_path),
        "http://immich.local",
    )

    assert result == ("a1", 88)
    assert client.metadata_calls == ["a1"]


def test_generate_album_for_rule_queries_immich_then_selects_top_cached_asset(tmp_path):
    """Album generation should search Immich first and rank cached scores."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(conn, "a1", "checksum-1", 40, {}, None, {"score": 40})
    upsert_processed_asset(conn, "a2", "checksum-2", 90, {}, None, {"score": 90})
    client = FakeClient()
    album_manager = FakeAlbumManager()

    result = generate_album_for_rule(
        client,
        conn,
        album_manager,
        make_rule(),
        str(tmp_path),
        "http://immich.local",
    )

    assert result["id"] == "album-1"
    assert client.iter_calls == [
        {
            "page_size": 50,
            "max_assets": 50,
            "taken_after": "2026-06-18T00:00:00+00:00",
            "taken_before": "2026-06-25T00:00:00+00:00",
        }
    ]
    assert album_manager.calls == [
        {
            "name": "Highlights: Last Week",
            "asset_ids": ["a2"],
            "description": "Auto-generated highlights",
            "bucket": "last-week",
        }
    ]
