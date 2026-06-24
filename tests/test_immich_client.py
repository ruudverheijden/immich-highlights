from src.immich_client import ImmichClient


class FakeResponse:
    """Tiny response double that exercises client parsing without network calls."""

    def __init__(self, payload):
        self.payload = payload
        self.headers = {"content-type": "application/json"}
        self.status_code = 200
        self.text = ""
        self.url = "http://immich.local/api/search/metadata"

    def raise_for_status(self):
        pass

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

    def post(self, url, json=None, timeout=None):
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse(self.responses.pop(0))

    def get(self, url, params=None, stream=False, timeout=None):
        self.gets.append(
            {"url": url, "params": params, "stream": stream, "timeout": timeout}
        )
        return FakeResponse({})


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
        "withExif": True,
    }


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
