from src.db import init_db, upsert_processed_asset
from src.export_review import (
    download_thumbnail,
    immich_asset_url,
    immich_thumbnail_url,
    load_processed_assets,
    PLACEHOLDER_THUMBNAIL,
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
        {"iso": 100},
        5,
        {
            "score": 87,
            "components": {"rating": 30, "blur": 10},
            "inputs": {"blur_variance": 250, "face_count": 1},
        },
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
    assert "rating" in html
    assert "blur_variance" in html
    assert "localStorage" in html


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
