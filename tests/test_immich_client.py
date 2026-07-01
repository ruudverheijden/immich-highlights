"""Tests for Immich API request payloads and response handling."""

from src.immich_client import ImmichClient
import pytest
import requests


class FakeResponse:
    """Tiny response double that exercises client parsing without network calls."""

    def __init__(self, payload):
        self.payload = payload
        self.headers = {"content-type": "application/json"}
        self.status_code = 200
        self.text = ""
        self.url = "http://immich.local/api/search/metadata"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("error", response=self)

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        return [b"preview-bytes"]


class FakeSession:
    """Capture outgoing requests so tests can assert Immich API contracts."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.gets = []
        self.puts = []
        self.deletes = []

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.responses.pop(0))

    def get(self, url, params=None, stream=False, timeout=None):
        self.gets.append(
            {"url": url, "params": params, "stream": stream, "timeout": timeout}
        )
        payload = self.responses.pop(0) if self.responses else {}
        return FakeResponse(payload)

    def put(self, url, json=None, timeout=None):
        self.puts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.responses.pop(0))

    def delete(self, url, json=None, timeout=None):
        self.deletes.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.responses.pop(0))


def test_list_assets_uses_metadata_search_endpoint():
    """Immich exposes paginated asset listing through metadata search."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [{"assets": {"items": [{"id": "asset-1"}], "nextPage": None}}]
    )

    assets = client.list_assets(page=2, per_page=25)

    assert assets == [{"id": "asset-1"}]
    assert client.session.posts[0]["url"] == "http://immich.local/api/search/metadata"
    assert client.session.posts[0]["json"] == {
        "page": 2,
        "size": 25,
        "type": "IMAGE",
        "visibility": "timeline",
        "withDeleted": False,
        "withExif": True,
    }


def test_list_assets_can_filter_by_taken_date_range():
    """Album rules should narrow candidates before scoring starts."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession([{"assets": {"items": [], "nextPage": None}}])

    assets = client.list_assets(
        page=1,
        per_page=50,
        taken_after="2026-06-01T00:00:00+00:00",
        taken_before="2026-06-25T00:00:00+00:00",
    )

    assert assets == []
    assert client.session.posts[0]["json"] == {
        "page": 1,
        "size": 50,
        "type": "IMAGE",
        "visibility": "timeline",
        "withDeleted": False,
        "withExif": True,
        "takenAfter": "2026-06-01T00:00:00+00:00",
        "takenBefore": "2026-06-25T00:00:00+00:00",
    }


def test_count_assets_reads_total_from_statistics_search():
    """Content filters use Immich statistics for readable exact pool counts."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession([{"total": 123}])

    count = client.count_assets(
        taken_after="2026-06-01T00:00:00+00:00",
        taken_before="2026-06-25T00:00:00+00:00",
    )

    assert count == 123
    assert client.session.posts[0]["url"] == "http://immich.local/api/search/statistics"
    assert client.session.posts[0]["json"] == {
        "type": "IMAGE",
        "visibility": "timeline",
        "withDeleted": False,
        "takenAfter": "2026-06-01T00:00:00+00:00",
        "takenBefore": "2026-06-25T00:00:00+00:00",
    }


def test_count_assets_rejects_statistics_response_without_total():
    """A changed Immich statistics response should fail instead of returning 0."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession([{"assets": {"items": []}}])

    with pytest.raises(ValueError, match="missing integer total"):
        client.count_assets()


def test_verify_permissions_checks_asset_statistics():
    """Startup diagnostics should catch missing statistics permission."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [
            {"assets": {"items": []}},
            {"total": 0},
            [],
            [],
        ]
    )

    checks = client.verify_permissions()

    assert checks["asset.read"] == (True, "200")
    assert client.session.posts[0] == {
        "url": "http://immich.local/api/search/metadata",
        "json": {
            "type": "IMAGE",
            "visibility": "timeline",
            "withDeleted": False,
            "page": 1,
            "size": 1,
        },
        "timeout": 5,
    }
    assert checks["asset.statistics"] == (True, "200")
    assert client.session.posts[1] == {
        "url": "http://immich.local/api/search/statistics",
        "json": {
            "type": "IMAGE",
            "visibility": "timeline",
            "withDeleted": False,
        },
        "timeout": 5,
    }


def test_verify_permissions_reports_asset_statistics_failure():
    """Missing statistics permission should be visible in startup diagnostics."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [
            {"assets": {"items": []}},
            {"message": "Forbidden"},
            [],
            [],
        ]
    )
    original_post = client.session.post

    def post_with_forbidden_statistics(url, json=None, timeout=None):
        response = original_post(url, json=json, timeout=timeout)
        if url.endswith("/search/statistics"):
            response.status_code = 403
        return response

    client.session.post = post_with_forbidden_statistics

    checks = client.verify_permissions()

    assert checks["asset.read"] == (True, "200")
    assert checks["asset.statistics"] == (False, "403")


def test_iter_assets_follows_next_page_until_limit():
    """The scorer can stream pages without loading the whole library at once."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [
            {
                "assets": {
                    "items": [{"id": "asset-1"}, {"id": "asset-2"}],
                    "nextPage": "2",
                }
            },
            {
                "assets": {
                    "items": [{"id": "asset-3"}, {"id": "asset-4"}],
                    "nextPage": None,
                }
            },
        ]
    )

    assets = list(client.iter_assets(page_size=2, max_assets=3))

    # The second page has two items, but max_assets should stop after the third.
    assert assets == [{"id": "asset-1"}, {"id": "asset-2"}, {"id": "asset-3"}]
    assert [post["json"]["page"] for post in client.session.posts] == [1, 2]


def test_iter_smart_search_assets_uses_query_and_taken_date_range():
    """Content filters should use Immich smart search scoped to the album window."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [
            {
                "assets": {
                    "items": [{"id": "asset-1"}],
                    "nextPage": None,
                }
            }
        ]
    )

    assets = list(
        client.iter_smart_search_assets(
            query="screenshot",
            page_size=25,
            max_assets=25,
            taken_after="2026-06-01T00:00:00+00:00",
            taken_before="2026-06-25T00:00:00+00:00",
        )
    )

    assert assets == [{"id": "asset-1"}]
    assert client.session.posts[0]["url"] == "http://immich.local/api/search/smart"
    assert client.session.posts[0]["json"] == {
        "query": "screenshot",
        "page": 1,
        "size": 25,
        "type": "IMAGE",
        "visibility": "timeline",
        "withDeleted": False,
        "withExif": True,
        "takenAfter": "2026-06-01T00:00:00+00:00",
        "takenBefore": "2026-06-25T00:00:00+00:00",
    }


def test_download_asset_preview_uses_thumbnail_preview_endpoint(tmp_path):
    """Preview thumbnails avoid codec issues with original formats like HEIC."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession([])
    dest_path = tmp_path / "preview"

    client.download_asset_preview("asset-1", str(dest_path))

    assert client.session.gets[0] == {
        "url": "http://immich.local/api/assets/asset-1/thumbnail",
        "params": {"size": "preview"},
        "stream": True,
        "timeout": 30,
    }


def test_get_asset_faces_uses_immich_faces_endpoint():
    """Immich's own face boxes should be available to the scoring pipeline."""
    client = ImmichClient("http://immich.local", dry_run=True)
    client.session = FakeSession(
        [
            [
                {
                    "id": "face-1",
                    "boundingBoxX1": 100,
                    "boundingBoxY1": 120,
                    "boundingBoxX2": 220,
                    "boundingBoxY2": 260,
                    "imageWidth": 1000,
                    "imageHeight": 800,
                }
            ]
        ]
    )

    faces = client.get_asset_faces("asset-1")

    assert faces[0]["id"] == "face-1"
    assert client.session.gets[0] == {
        "url": "http://immich.local/api/faces",
        "params": {"id": "asset-1"},
        "stream": False,
        "timeout": 10,
    }


def test_create_album_uses_immich_album_name_field():
    """Immich's create-album DTO uses albumName, not name."""
    client = ImmichClient("http://immich.local", dry_run=False)
    client.session = FakeSession([{"id": "album-1", "albumName": "Highlights"}])

    result = client.create_album("Highlights", ["asset-1"], "Generated")

    assert result == {"id": "album-1", "albumName": "Highlights"}
    assert client.session.posts[0]["url"] == "http://immich.local/api/albums"
    assert client.session.posts[0]["json"] == {
        "albumName": "Highlights",
        "assetIds": ["asset-1"],
        "description": "Generated",
    }


def test_create_album_dry_run_reports_intent():
    """Dry-run mode should make album writes visible without mutating Immich."""
    client = ImmichClient("http://immich.local", dry_run=True)

    result = client.create_album("Highlights", ["asset-1"], "Generated")

    assert result == {
        "albumName": "Highlights",
        "asset_count": 1,
        "dry_run": True,
    }


def test_add_assets_to_album_uses_bulk_ids_field():
    """Immich's single-album add endpoint expects BulkIdsDto.ids."""
    client = ImmichClient("http://immich.local", dry_run=False)
    client.session = FakeSession([{"added": 2}])

    result = client.add_assets_to_album("album-1", ["asset-1", "asset-2"])

    assert result == {"added": 2}
    assert client.session.puts[0] == {
        "url": "http://immich.local/api/albums/album-1/assets",
        "json": {"ids": ["asset-1", "asset-2"]},
        "timeout": 10,
    }


def test_add_assets_to_album_explains_missing_permission():
    """A 403 on album updates usually means albumAsset.create is missing."""
    client = ImmichClient("http://immich.local", dry_run=False)
    response = {"message": "Forbidden"}
    client.session = FakeSession([response])

    original_put = client.session.put

    def forbidden_put(url, json=None, timeout=None):
        resp = original_put(url, json=json, timeout=timeout)
        resp.status_code = 403
        return resp

    client.session.put = forbidden_put

    try:
        client.add_assets_to_album("album-1", ["asset-1"])
    except PermissionError as e:
        assert "albumAsset.create" in str(e)
    else:
        raise AssertionError("Expected PermissionError")


def test_remove_assets_from_album_uses_bulk_ids_field():
    """Generated albums should be synced by removing stale assets."""
    client = ImmichClient("http://immich.local", dry_run=False)
    client.session = FakeSession([{"removed": 1}])

    result = client.remove_assets_from_album("album-1", ["asset-1"])

    assert result == {"removed": 1}
    assert client.session.deletes[0] == {
        "url": "http://immich.local/api/albums/album-1/assets",
        "json": {"ids": ["asset-1"]},
        "timeout": 10,
    }
