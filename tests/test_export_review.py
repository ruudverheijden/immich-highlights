"""Tests for the development HTML review export."""

from src.db import (
    init_db,
    replace_duplicate_groups,
    upsert_album_mapping,
    upsert_asset_filter_result,
    upsert_processed_asset,
)
from src.export_review import (
    attach_album_memberships,
    attach_duplicate_memberships,
    attach_pipeline_statuses,
    content_filter_labels,
    duplicate_roles,
    download_thumbnail,
    first_photo_datetime,
    format_datetime,
    immich_asset_url,
    immich_thumbnail_url,
    load_album_memberships,
    load_duplicate_memberships,
    load_filter_memberships,
    load_pipeline_summaries,
    load_processed_assets,
    PLACEHOLDER_THUMBNAIL,
    render_content_filter_options,
    write_review_html,
)


class FakeResponse:
    """Minimal response double for thumbnail downloads."""

    def __init__(self, content=b"thumbnail-bytes"):
        self.content = content

    def raise_for_status(self):
        pass


class FakeSession:
    """Capture thumbnail requests without network calls."""

    def __init__(self):
        self.gets = []

    def get(self, url, headers=None, timeout=None):
        self.gets.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()


def test_immich_asset_url_uses_browser_url():
    """Review reports should link to Immich's browser route, not the API route."""
    assert (
        immich_asset_url("http://immich.local/", "asset-1")
        == "http://immich.local/photos/asset-1"
    )


def test_immich_thumbnail_url_uses_preview_endpoint():
    """Review cards should render Immich preview thumbnails directly."""
    assert (
        immich_thumbnail_url("http://immich.local/", "asset-1")
        == "http://immich.local/api/assets/asset-1/thumbnail?size=preview"
    )


def test_download_thumbnail_writes_local_file(tmp_path):
    """Local thumbnails avoid browser http/https upgrade issues in the report."""
    session = FakeSession()

    path = download_thumbnail(
        session,
        "http://immich.local",
        "secret",
        "asset-1",
        tmp_path,
    )

    assert path == tmp_path / "asset-1.jpg"
    assert path.read_bytes() == b"thumbnail-bytes"
    assert session.gets == [
        {
            "url": "http://immich.local/api/assets/asset-1/thumbnail?size=preview",
            "headers": {"x-api-key": "secret"},
            "timeout": 15,
        }
    ]


def test_write_review_html_exports_scoring_details(tmp_path):
    """The development report should show score details and persist local labels."""
    db_path = tmp_path / "scorer.db"
    output_path = tmp_path / "review" / "index.html"
    conn = init_db(str(db_path))
    upsert_processed_asset(
        conn,
        "asset-1",
        "checksum",
        87,
        {"iso": 100, "localDateTime": "2026-06-24T19:15:30.000Z"},
        5,
        {
            "score": 87,
            "components": {"rating": 30, "blur": 10},
            "inputs": {
                "blur_variance": 250,
                "content_filter_matches": [
                    {
                        "label": "screenshot",
                        "query": "screenshot",
                        "penalty": -40,
                        "rank": 1,
                    }
                ],
                "content_filter_penalty": -40,
                "content_labels": ["screenshot"],
                "dimensions": [400, 300],
                "face_count": 1,
                "faces": [{"x": 40, "y": 50, "width": 80, "height": 90}],
            },
        },
    )
    upsert_processed_asset(conn, "asset-2", "checksum-2", 70, {}, None, {"score": 70})
    upsert_album_mapping(
        conn,
        "last-week",
        "album-1",
        "Highlights: Last Week",
        ["asset-1"],
    )
    upsert_asset_filter_result(
        conn,
        "asset-1",
        "last-week",
        included=True,
        reason="accepted_timeline_image_candidate",
    )
    replace_duplicate_groups(
        conn,
        "last-week",
        [
            {
                "group_id": "last-week:phash:1:asset-1",
                "representative_asset_id": "asset-2",
                "reason": "phash_distance<=6",
                "members": [
                    {"asset_id": "asset-2", "distance": 0},
                    {"asset_id": "asset-1", "distance": 3},
                ],
            }
        ],
    )
    conn.close()

    path = write_review_html(
        db_path=str(db_path),
        immich_url="http://immich.local",
        output_path=str(output_path),
        download_thumbnails=False,
    )
    html = path.read_text(encoding="utf-8")

    assert path == output_path
    assert "http://immich.local/photos/asset-1" in html
    assert "http://immich.local/api/assets/asset-1/thumbnail?size=preview" in html
    assert 'class="thumbnail"' in html
    assert "asset-1" in html
    assert 'class="asset-datetime"' in html
    assert "2026-06-24 19:15" in html
    assert "Notes" not in html
    assert 'data-field="notes"' not in html
    assert "rating" in html
    assert "blur_variance" in html
    assert "Content filters" in html
    assert "screenshot #1 -40" in html
    assert 'title="Smart search: screenshot rank 1"' in html
    assert 'id="toggle-faces"' in html
    assert 'id="toggle-components"' in html
    assert 'id="toggle-inputs"' in html
    assert '<details class="score-components">' in html
    assert '<details class="score-components" open>' not in html
    assert '<details class="scoring-inputs">' in html
    assert '<details class="scoring-inputs" open>' not in html
    assert "show-score-components" in html
    assert "show-scoring-inputs" in html
    assert 'class="face-overlay"' in html
    assert "data-faces=" in html
    assert "data-dimensions=" in html
    assert 'id="album-filter"' in html
    assert 'id="content-filter"' in html
    assert "Highlights: Last Week" in html
    assert "data-albums=" in html
    assert "&quot;last-week&quot;" in html
    assert "data-content-filters=" in html
    assert "&quot;screenshot&quot;" in html
    assert '<option value="any">Has any content label</option>' in html
    assert '<option value="screenshot">screenshot</option>' in html
    assert '<option value="none">No content label</option>' in html
    assert "localStorage" in html
    assert "Pipeline Summary" in html
    assert "pipelineSummaries" in html
    assert "Asset Discovery" in html
    assert "Duplicate Detection" in html
    assert 'id="duplicate-filter"' in html
    assert '<option value="suppressed">Suppressed duplicates</option>' in html
    assert "data-duplicate-roles=" in html
    assert "&quot;suppressed&quot;" in html
    assert "phash_distance&lt;=6" in html
    assert "Pipeline status" in html
    assert "Candidate: yes" in html
    assert "Filter: accepted: accepted_timeline_image_candidate" in html
    assert "Duplicate: suppressed d=3" in html
    assert "Selection: selected" in html


def test_load_pipeline_summaries_counts_stage_outputs(tmp_path):
    """Pipeline summaries should expose per-stage counts for each album."""
    db_path = tmp_path / "scorer.db"
    conn = init_db(str(db_path))
    score_details = {
        "score": 87,
        "components": {"rating": 30},
        "inputs": {
            "blur_variance": 250,
            "brightness": 120,
            "hist_std": 40,
            "phash": "0000",
            "dimensions": [400, 300],
            "face_count": 1,
            "face_quality": 10,
            "rating": 5,
            "iso": 100,
            "exposure_seconds": None,
            "has_location": True,
            "is_favorite": False,
            "is_edited": False,
            "content_labels": ["screenshot"],
            "content_filter_matches": [{"label": "screenshot", "rank": 1}],
        },
    }
    upsert_processed_asset(conn, "asset-1", "checksum-1", 87, {}, 5, score_details)
    upsert_processed_asset(
        conn,
        "asset-2",
        "checksum-2",
        65,
        {},
        None,
        {
            **score_details,
            "score": 65,
            "inputs": {**score_details["inputs"], "phash": "0001"},
        },
    )
    upsert_asset_filter_result(
        conn,
        "asset-1",
        "last-week",
        included=True,
        reason="accepted_timeline_image_candidate",
    )
    upsert_asset_filter_result(
        conn,
        "asset-2",
        "last-week",
        included=False,
        reason="too_small",
    )
    upsert_album_mapping(
        conn,
        "last-week",
        "album-1",
        "Highlights: Last Week",
        ["asset-1"],
    )
    replace_duplicate_groups(
        conn,
        "last-week",
        [
            {
                "group_id": "last-week:phash:1:asset-1",
                "representative_asset_id": "asset-1",
                "reason": "phash_distance<=6",
                "members": [
                    {"asset_id": "asset-1", "distance": 0},
                    {"asset_id": "asset-2", "distance": 1},
                ],
            }
        ],
    )
    conn.close()

    albums, _memberships = load_album_memberships(str(db_path))
    summaries = load_pipeline_summaries(str(db_path), albums)
    last_week = summaries["last-week"]
    stages = {stage["title"]: stage for stage in last_week["stages"]}

    assert stages["Asset Discovery"]["metrics"]["Candidates found"] == 2
    assert stages["Filtering"]["metrics"] == {"Accepted": 1, "Rejected": 1}
    assert stages["Filtering"]["details"]["too_small"] == 1
    assert stages["Technical Analysis"]["metrics"]["With pHash"] == 2
    assert stages["Semantic Analysis"]["metrics"]["With faces"] == 2
    assert stages["Content Filters"]["metrics"]["Matched assets"] == 2
    assert stages["Duplicate Detection"]["metrics"] == {"Groups": 1, "Suppressed": 1}
    assert stages["Album Selection"]["metrics"]["Selected"] == 1


def test_content_filter_options_are_deduplicated_and_sorted():
    """The content-label dropdown should stay stable as labels accumulate."""
    assets = [
        {
            "score_details": {
                "inputs": {
                    "content_filter_matches": [
                        {"label": "shopping"},
                        {"label": "receipt"},
                        {"label": "shopping"},
                    ]
                }
            }
        },
        {"score_details": {"inputs": {"content_filter_matches": []}}},
    ]

    assert content_filter_labels(assets[0]) == ["shopping", "receipt"]
    options = render_content_filter_options(assets)

    assert options.index('value="receipt"') < options.index('value="shopping"')
    assert options.count('value="shopping"') == 1
    assert '<option value="none">No content label</option>' in options


def test_write_review_html_uses_placeholder_when_thumbnail_download_fails(
    tmp_path,
    monkeypatch,
):
    """Default exports should not fall back to remote preview URLs."""
    monkeypatch.setattr("src.export_review.download_thumbnail", lambda *args: None)
    db_path = tmp_path / "scorer.db"
    output_path = tmp_path / "review" / "index.html"
    conn = init_db(str(db_path))
    upsert_processed_asset(conn, "asset-1", "checksum", 87, {}, None, {"score": 87})
    conn.close()

    path = write_review_html(
        db_path=str(db_path),
        immich_url="http://immich.local",
        output_path=str(output_path),
    )
    html = path.read_text(encoding="utf-8")

    assert PLACEHOLDER_THUMBNAIL.split(",", 1)[0] in html
    assert "http://immich.local/api/assets/asset-1/thumbnail" not in html


def test_load_processed_assets_sorts_by_score(tmp_path):
    """The review list should put highest-scoring assets first."""
    db_path = tmp_path / "scorer.db"
    conn = init_db(str(db_path))
    upsert_processed_asset(conn, "low", "c1", 10, {}, None, {"score": 10})
    upsert_processed_asset(conn, "high", "c2", 90, {}, None, {"score": 90})
    conn.close()

    assets = load_processed_assets(str(db_path))

    assert [asset["asset_id"] for asset in assets] == ["high", "low"]


def test_photo_datetime_helpers_prefer_exif_and_format_iso_values():
    """Review cards should display photo datetime instead of asset ids."""
    assert format_datetime("2026-06-24T19:15:30.000Z") == "2026-06-24 19:15"
    assert (
        first_photo_datetime(
            {"localDateTime": "2026-06-24T19:15:30.000Z"},
            fallback="2026-06-25 10:00:00",
        )
        == "2026-06-24 19:15"
    )
    assert first_photo_datetime({}, fallback="2026-06-25 10:00:00") == (
        "2026-06-25 10:00"
    )


def test_load_album_memberships_indexes_assets_by_generated_album(tmp_path):
    """The review export should know which generated albums contain each asset."""
    db_path = tmp_path / "scorer.db"
    conn = init_db(str(db_path))
    upsert_album_mapping(
        conn,
        "last-week",
        "album-1",
        "Highlights: Last Week",
        ["a1", "a2"],
    )
    upsert_album_mapping(
        conn,
        "last-month",
        "album-2",
        "Highlights: Last Month",
        ["a2"],
    )
    conn.close()

    albums, memberships = load_album_memberships(str(db_path))

    assert [album["bucket"] for album in albums] == ["last-month", "last-week"]
    assert memberships["a1"] == [
        {"name": "Highlights: Last Week", "bucket": "last-week"}
    ]
    assert memberships["a2"] == [
        {"name": "Highlights: Last Month", "bucket": "last-month"},
        {"name": "Highlights: Last Week", "bucket": "last-week"},
    ]


def test_attach_album_memberships_marks_assets_without_albums():
    """Assets outside generated albums should still be filterable in the report."""
    assets = [{"asset_id": "a1"}, {"asset_id": "a2"}]

    attach_album_memberships(
        assets,
        {"a1": [{"name": "Highlights: Last Week", "bucket": "last-week"}]},
    )

    assert assets == [
        {
            "asset_id": "a1",
            "albums": [{"name": "Highlights: Last Week", "bucket": "last-week"}],
        },
        {"asset_id": "a2", "albums": []},
    ]


def test_load_and_attach_duplicate_memberships(tmp_path):
    """Review cards should know whether they are duplicate representatives."""
    db_path = tmp_path / "scorer.db"
    conn = init_db(str(db_path))
    upsert_processed_asset(conn, "a1", "c1", 90, {}, None, {"score": 90})
    upsert_processed_asset(conn, "a2", "c2", 80, {}, None, {"score": 80})
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
                    {"asset_id": "a2", "distance": 2},
                ],
            }
        ],
    )
    conn.close()

    memberships = load_duplicate_memberships(str(db_path))
    assets = [{"asset_id": "a1"}, {"asset_id": "a2"}, {"asset_id": "a3"}]
    attach_duplicate_memberships(assets, memberships)

    assert duplicate_roles(assets[0]) == ["representative"]
    assert duplicate_roles(assets[1]) == ["suppressed"]
    assert duplicate_roles(assets[2]) == []


def test_attach_pipeline_statuses_explains_selection_per_album(tmp_path):
    """Per-card status should explain candidate, filter, duplicate, and selection."""
    db_path = tmp_path / "scorer.db"
    conn = init_db(str(db_path))
    upsert_processed_asset(conn, "a1", "c1", 90, {}, None, {"score": 90})
    upsert_processed_asset(conn, "a2", "c2", 80, {}, None, {"score": 80})
    upsert_asset_filter_result(
        conn,
        "a1",
        "last-week",
        included=True,
        reason="accepted_timeline_image_candidate",
    )
    conn.commit()
    conn.close()

    filters = load_filter_memberships(str(db_path))
    assets = [
        {
            "asset_id": "a1",
            "albums": [{"name": "Highlights: Last Week", "bucket": "last-week"}],
            "duplicates": [],
        },
        {"asset_id": "a2", "albums": [], "duplicates": []},
    ]

    attach_pipeline_statuses(
        assets,
        [{"name": "Highlights: Last Week", "bucket": "last-week"}],
        filters,
    )

    assert assets[0]["pipeline_statuses"] == [
        {
            "album": "Highlights: Last Week",
            "bucket": "last-week",
            "candidate": "yes",
            "filter": "accepted: accepted_timeline_image_candidate",
            "duplicate": "not duplicate",
            "selection": "selected",
        }
    ]
    assert assets[1]["pipeline_statuses"][0]["filter"] == "no candidate record"
    assert assets[1]["pipeline_statuses"][0]["selection"] == "not selected"
