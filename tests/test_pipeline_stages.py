"""Tests for individual pipeline stage helpers."""

from src.asset_discovery import get_asset_checksum, get_asset_id, iter_rule_assets
from src.db import get_asset_filter_result, init_db, upsert_processed_asset
from src.filtering import filter_album_candidates
from src.selection import select_top_scored_assets


class FakeClient:
    """Small client double for discovery-stage tests."""

    def __init__(self):
        self.calls = []

    def iter_assets(self, **kwargs):
        self.calls.append(kwargs)
        return iter([{"id": "a1"}])


class FakeRule:
    """Minimal album rule shape used by discovery-stage tests."""

    name = "Highlights: Test"
    bucket = "test"
    max_candidates = 25

    def taken_after_iso(self):
        return "2026-06-01T00:00:00+00:00"

    def taken_before_iso(self):
        return "2026-06-30T00:00:00+00:00"


def test_asset_discovery_normalizes_asset_identity_and_checksum():
    """Discovery helpers should hide Immich response-shape differences."""
    assert get_asset_id({"id": "a1"}) == "a1"
    assert get_asset_id({"assetId": "a2"}) == "a2"
    assert get_asset_id({"uuid": "a3"}) == "a3"
    assert get_asset_checksum({"checksum": "from-asset"}, {}) == "from-asset"
    assert get_asset_checksum({}, {"checksum": "from-meta"}) == "from-meta"


def test_iter_rule_assets_scopes_discovery_to_album_rule_window():
    """Asset discovery should query Immich using the configured rule window."""
    client = FakeClient()

    assets = list(iter_rule_assets(client, FakeRule()))

    assert assets == [{"id": "a1"}]
    assert client.calls == [
        {
            "page_size": 25,
            "max_assets": 25,
            "taken_after": "2026-06-01T00:00:00+00:00",
            "taken_before": "2026-06-30T00:00:00+00:00",
        }
    ]


def test_select_top_scored_assets_returns_highest_scores_first():
    """Selection should be separate from score calculation."""
    selected = select_top_scored_assets(
        [("low", 10), ("high", 90), ("mid", 50)],
        limit=2,
    )

    assert selected == ["high", "mid"]


def test_filter_album_candidates_records_included_decisions(tmp_path):
    """Filtering should keep accepted candidates visible in the database."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(conn, "a1", "checksum-1", 80, {}, None, {"score": 80})

    included = filter_album_candidates(
        conn,
        FakeRule(),
        [{"id": "a1", "checksum": "checksum-1"}, {"checksum": "missing-id"}],
    )

    assert included == [{"id": "a1", "checksum": "checksum-1"}]
    row = get_asset_filter_result(conn, "a1", "test")
    assert row["included"] is True
    assert row["reason"] == "accepted_timeline_image_candidate"
