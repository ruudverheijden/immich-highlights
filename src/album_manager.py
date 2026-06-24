from typing import List


class AlbumManager:
    """Coordinate album operations without exposing Immich API details upstream."""

    def __init__(self, immich_client):
        self.client = immich_client

    def ensure_album(self, name: str, asset_ids: List[str], description: str = ""):
        # The MVP intentionally creates a fresh album each run; later versions can
        # use stored album mappings to update an existing generated album instead.
        return self.client.create_album(name, asset_ids, description)
