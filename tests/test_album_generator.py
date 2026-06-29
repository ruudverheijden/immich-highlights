from datetime import datetime, timezone
from PIL import Image

from src.album_generator import (
    collect_content_filter_matches,
    content_filter_state,
    generate_album_for_rule,
    score_or_reuse_asset,
)
from src.album_rules import AlbumRule, ContentFilter
from src.db import get_processed_asset, init_db, upsert_processed_asset
from src.scoring_engine import ScoringConfig


class FakeClient:
    """Small Immich client double for album-generator tests."""

    def __init__(self, count_results=None, smart_results=None):
        self.iter_calls = []
        self.smart_calls = []
        self.count_calls = []
        self.metadata_calls = []
        self.preview_calls = []
        self.count_results = list(count_results or [])
        self.smart_results = smart_results or [{"id": "a2"}]

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

    def iter_smart_search_assets(
        self,
        query,
        page_size,
        max_assets,
        taken_after=None,
        taken_before=None,
    ):
        self.smart_calls.append(
            {
                "query": query,
                "page_size": page_size,
                "max_assets": max_assets,
                "taken_after": taken_after,
                "taken_before": taken_before,
            }
        )
        return iter(self.smart_results)

    def count_assets(self, taken_after=None, taken_before=None):
        self.count_calls.append(
            {
                "taken_after": taken_after,
                "taken_before": taken_before,
            }
        )
        if self.count_results:
            return self.count_results.pop(0)
        return 500

    def download_asset_preview(self, asset_id, dest_path):
        self.preview_calls.append(asset_id)
        Image.new("RGB", (800, 600), color=(120, 120, 120)).save(
            dest_path, format="JPEG"
        )
        return dest_path

    def get_asset_faces(self, asset_id):
        return []


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


def test_collect_content_filter_matches_scopes_smart_search_to_rule_window():
    """Configured content filters should label assets through Immich smart search."""
    client = FakeClient()
    filters = [
        ContentFilter(
            label="screenshot",
            query="screenshot",
            penalty=-40,
            max_results=25,
        )
    ]

    matches = collect_content_filter_matches(client, make_rule(), filters, {"a2"})

    assert client.count_calls == [
        {
            "taken_after": "2026-06-18T00:00:00+00:00",
            "taken_before": "2026-06-25T00:00:00+00:00",
        }
    ]
    assert client.smart_calls == [
        {
            "query": "screenshot",
            "page_size": 25,
            "max_assets": 25,
            "taken_after": "2026-06-18T00:00:00+00:00",
            "taken_before": "2026-06-25T00:00:00+00:00",
        }
    ]
    assert matches == {
        "a2": [
            {"label": "screenshot", "query": "screenshot", "penalty": -40, "rank": 1}
        ]
    }


def test_collect_content_filter_matches_expands_small_search_pool():
    """Smart-search filters need enough context to avoid weak ranked matches."""
    client = FakeClient(count_results=[100, 250, 600])
    filters = [
        ContentFilter(
            label="screenshot",
            query="screenshot",
            penalty=-40,
            max_results=25,
            min_search_pool=500,
        )
    ]

    matches = collect_content_filter_matches(client, make_rule(), filters, {"a2"})

    assert [call["taken_after"] for call in client.count_calls] == [
        "2026-06-18T00:00:00+00:00",
        "2026-06-11T00:00:00+00:00",
        "2026-05-28T00:00:00+00:00",
    ]
    assert client.smart_calls[0]["taken_after"] == "2026-05-28T00:00:00+00:00"
    assert matches["a2"][0]["rank"] == 1


def test_collect_content_filter_matches_skips_when_context_stays_too_small():
    """Filters should not run when even the widest context is too small."""
    client = FakeClient(count_results=[20, 40, 80, 160, 320, 400, 450])
    filters = [
        ContentFilter(
            label="screenshot",
            query="screenshot",
            penalty=-40,
            max_results=25,
            min_search_pool=500,
        )
    ]

    matches = collect_content_filter_matches(client, make_rule(), filters, {"a2"})

    assert matches == {}
    assert client.smart_calls == []
    assert client.count_calls[-1]["taken_after"] == "2025-06-25T00:00:00+00:00"


def test_collect_content_filter_matches_only_labels_album_candidates():
    """Expanded smart-search context should not penalize non-candidate assets."""
    client = FakeClient(smart_results=[{"id": "outside"}, {"id": "a2"}])
    filters = [
        ContentFilter(
            label="receipt",
            query="receipt",
            penalty=-30,
            max_results=25,
        )
    ]

    matches = collect_content_filter_matches(client, make_rule(), filters, {"a2"})

    assert matches == {
        "a2": [{"label": "receipt", "query": "receipt", "penalty": -30, "rank": 2}]
    }


def test_content_filter_state_uses_best_ranked_penalty_only():
    """Multiple content labels should not stack penalties for one photo."""
    labels, penalty = content_filter_state(
        [
            {"label": "paperwork", "penalty": -25, "rank": 12},
            {"label": "product-photo", "penalty": -20, "rank": 1},
            {"label": "shopping", "penalty": -15, "rank": 3},
        ]
    )

    assert labels == ["paperwork", "product-photo", "shopping"]
    assert penalty == -20


def test_content_filter_state_uses_configured_penalty_cap():
    """The scoring config controls how hard content filters can penalize."""
    labels, penalty = content_filter_state(
        [{"label": "screenshot", "penalty": -80, "rank": 1}],
        ScoringConfig(content_filter_min_penalty=-25),
    )

    assert labels == ["screenshot"]
    assert penalty == -25


def test_score_or_reuse_asset_recalculates_cached_inputs_with_config(tmp_path):
    """Changing scoring config should update cached scores on the next run."""
    conn = init_db(str(tmp_path / "test.db"))
    score_details = {
        "score": 50,
        "inputs": {
            "blur_variance": 250,
            "dimensions": [800, 800],
            "face_count": 0,
            "face_quality": 0,
            "rating": 3,
            "iso": 200,
            "exposure_seconds": None,
            "has_location": False,
            "is_favorite": False,
            "is_edited": False,
            "content_labels": [],
            "content_filter_penalty": 0,
        },
    }
    upsert_processed_asset(conn, "a1", "checksum-1", 50, {}, None, score_details)
    client = FakeClient()

    result = score_or_reuse_asset(
        client,
        conn,
        {"id": "a1", "checksum": "checksum-1"},
        str(tmp_path),
        "http://immich.local",
        scoring_config=ScoringConfig(base_score=30, blur_high_bonus=0),
    )

    row = get_processed_asset(conn, "a1")
    assert result == ("a1", 30)
    assert row["score"] == 30
    assert client.preview_calls == []


def test_score_or_reuse_asset_force_rescore_ignores_cached_score(tmp_path):
    """A manual full rescan should re-analyze even unchanged cached photos."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(conn, "a1", "checksum-1", 99, {}, None, {"score": 99})
    client = FakeClient()

    result = score_or_reuse_asset(
        client,
        conn,
        {"id": "a1", "checksum": "checksum-1"},
        str(tmp_path),
        "http://immich.local",
        force_rescore=True,
    )

    row = get_processed_asset(conn, "a1")
    assert result == ("a1", row["score"])
    assert client.preview_calls == ["a1"]
    assert row["score"] != 99
    assert "blur_variance" in row["score_details"]["inputs"]


def test_score_or_reuse_asset_rescores_when_content_filter_state_changes(tmp_path):
    """Cached images must be rescored when new content labels alter the score."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_processed_asset(
        conn,
        "a2",
        "checksum-2",
        90,
        {},
        None,
        {
            "score": 90,
            "inputs": {
                "content_labels": [],
                "content_filter_penalty": 0,
            },
        },
    )
    client = FakeClient()

    result = score_or_reuse_asset(
        client,
        conn,
        {"id": "a2", "checksum": "checksum-2"},
        str(tmp_path),
        "http://immich.local",
        content_filter_matches=[
            {"label": "receipt", "query": "receipt", "penalty": -30, "rank": 1}
        ],
    )

    row = get_processed_asset(conn, "a2")
    assert result == ("a2", row["score"])
    assert client.preview_calls == ["a2"]
    assert row["score_details"]["inputs"]["content_labels"] == ["receipt"]
    assert row["score_details"]["inputs"]["content_filter_penalty"] == -30


def test_score_or_reuse_asset_stores_content_filter_penalty(tmp_path):
    """Smart-search labels should be visible in score details and affect score."""
    conn = init_db(str(tmp_path / "test.db"))
    client = FakeClient()

    result = score_or_reuse_asset(
        client,
        conn,
        {"id": "a2", "checksum": "checksum-2"},
        str(tmp_path),
        "http://immich.local",
        content_filter_matches=[
            {"label": "screenshot", "query": "screenshot", "penalty": -40, "rank": 1}
        ],
    )

    row = get_processed_asset(conn, "a2")
    assert result == ("a2", row["score"])
    assert row["score_details"]["inputs"]["content_labels"] == ["screenshot"]
    assert row["score_details"]["inputs"]["content_filter_penalty"] == -40
    assert row["score_details"]["components"]["content_filter_penalty"] == -40
