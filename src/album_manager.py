from typing import List


class AlbumManager:
    def __init__(self, immich_client):
        self.client = immich_client

    def ensure_album(
        self, name: str, asset_ids: List[str], description: str = ""
    ):
        # MVP: always create new album (or dry-run report)
        return self.client.create_album(name, asset_ids, description)
