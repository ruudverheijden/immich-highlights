"""Immich album persistence helpers for creating, updating, and tracking albums."""

from typing import List

try:
    from .db import get_album_mapping, upsert_album_mapping
except ImportError:
    from db import get_album_mapping, upsert_album_mapping


class AlbumManager:
    """Coordinate album operations without exposing Immich API details upstream."""

    def __init__(self, immich_client, conn=None):
        self.client = immich_client
        self.conn = conn

    def ensure_album(
        self,
        name: str,
        asset_ids: List[str],
        description: str = "",
        bucket: str = "",
    ):
        """Create a bucket album once, then update the same Immich album on reruns."""
        mapping = get_album_mapping(self.conn, bucket) if self.conn and bucket else None
        if mapping:
            album_id = mapping["album_id"]
            existing_asset_ids = set(mapping.get("asset_ids", []))
            desired_asset_ids = set(asset_ids)
            new_asset_ids = [
                asset_id for asset_id in asset_ids if asset_id not in existing_asset_ids
            ]
            removed_asset_ids = [
                asset_id
                for asset_id in mapping.get("asset_ids", [])
                if asset_id not in desired_asset_ids
            ]
            if not new_asset_ids and not removed_asset_ids:
                return {
                    "id": album_id,
                    "albumName": mapping["album_name"] or name,
                    "asset_count": len(asset_ids),
                    "updated": False,
                }

            remove_result = None
            if removed_asset_ids:
                remove_result = self.client.remove_assets_from_album(
                    album_id, removed_asset_ids
                )

            add_result = None
            if new_asset_ids:
                add_result = self.client.add_assets_to_album(album_id, new_asset_ids)

            result = {
                "id": album_id,
                "albumName": mapping["album_name"] or name,
                "asset_count": len(asset_ids),
                "added_asset_count": len(new_asset_ids),
                "removed_asset_count": len(removed_asset_ids),
                "add_result": add_result,
                "remove_result": remove_result,
                "updated": True,
            }
            upsert_album_mapping(
                self.conn,
                bucket,
                album_id,
                name,
                asset_ids,
            )
            return result

        result = self.client.create_album(name, asset_ids, description)
        album_id = result.get("id")
        if self.conn and bucket and album_id:
            upsert_album_mapping(
                self.conn,
                bucket,
                album_id,
                result.get("albumName", name),
                asset_ids,
            )
        return result
