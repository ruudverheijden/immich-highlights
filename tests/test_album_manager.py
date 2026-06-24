from src.album_manager import AlbumManager
from src.db import get_album_mapping, init_db, upsert_album_mapping


class FakeImmichClient:
    """Capture album writes without calling Immich."""

    def __init__(self):
        self.created = []
        self.updated = []

    def create_album(self, name, asset_ids, description=""):
        self.created.append(
            {"name": name, "asset_ids": asset_ids, "description": description}
        )
        return {"id": "new-album", "albumName": name, "assetCount": len(asset_ids)}

    def add_assets_to_album(self, album_id, asset_ids):
        self.updated.append({"album_id": album_id, "asset_ids": asset_ids})
        return [{"id": asset_id, "success": True} for asset_id in asset_ids]


def test_ensure_album_creates_and_stores_new_bucket_mapping(tmp_path):
    """First run for a bucket should create an Immich album and store its id."""
    conn = init_db(str(tmp_path / "test.db"))
    client = FakeImmichClient()
    manager = AlbumManager(client, conn)

    result = manager.ensure_album(
        "Highlights: monthly",
        ["a1", "a2"],
        description="Generated",
        bucket="monthly",
    )

    assert result["id"] == "new-album"
    assert client.created == [
        {
            "name": "Highlights: monthly",
            "asset_ids": ["a1", "a2"],
            "description": "Generated",
        }
    ]
    assert client.updated == []
    assert get_album_mapping(conn, "monthly")["asset_ids"] == ["a1", "a2"]


def test_ensure_album_skips_update_when_assets_are_already_mapped(tmp_path):
    """Second run with the same assets should not call Immich unnecessarily."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_album_mapping(
        conn, "monthly", "album-1", "Highlights: monthly", ["a1", "a2"]
    )
    client = FakeImmichClient()
    manager = AlbumManager(client, conn)

    result = manager.ensure_album(
        "Highlights: monthly",
        ["a1", "a2"],
        description="Generated",
        bucket="monthly",
    )

    assert result["id"] == "album-1"
    assert not result["updated"]
    assert client.created == []
    assert client.updated == []
    assert get_album_mapping(conn, "monthly")["asset_ids"] == ["a1", "a2"]


def test_ensure_album_adds_only_new_assets_to_existing_bucket_album(tmp_path):
    """Later runs should only send assets not already known for the album."""
    conn = init_db(str(tmp_path / "test.db"))
    upsert_album_mapping(
        conn, "monthly", "album-1", "Highlights: monthly", ["a1", "a2"]
    )
    client = FakeImmichClient()
    manager = AlbumManager(client, conn)

    result = manager.ensure_album(
        "Highlights: monthly",
        ["a2", "a3"],
        description="Generated",
        bucket="monthly",
    )

    assert result["id"] == "album-1"
    assert result["updated"]
    assert result["added_asset_count"] == 1
    assert result["add_result"] == [{"id": "a3", "success": True}]
    assert client.created == []
    assert client.updated == [{"album_id": "album-1", "asset_ids": ["a3"]}]
    assert get_album_mapping(conn, "monthly")["asset_ids"] == ["a1", "a2", "a3"]
