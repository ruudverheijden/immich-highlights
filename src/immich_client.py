import requests
from typing import Optional, List


class ImmichClient:
    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        dry_run: bool = True,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.dry_run = dry_run
        if api_key:
            self.session.headers.update({"x-api-key": api_key})

    def _url(self, path: str):
        return f"{self.api_url}/{path.lstrip('/')}"

    def list_assets(self, page: int = 1, per_page: int = 20) -> List[dict]:
        # Basic paginated listing for MVP
        params = {"page": page, "limit": per_page}
        resp = self.session.get(self._url("assets"), params=params)
        resp.raise_for_status()
        return resp.json()

    def get_asset_metadata(self, asset_id: str) -> dict:
        url = self._url(f"assets/{asset_id}/metadata")
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def download_asset(self, asset_id: str, dest_path: str) -> str:
        params = {"download": "true"}
        url = self._url(f"file/{asset_id}")
        resp = self.session.get(url, params=params, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return dest_path

    def create_album(
        self,
        name: str,
        asset_ids: List[str],
        description: str = "",
    ) -> Optional[dict]:
        if self.dry_run:
            return {
                "name": name,
                "asset_count": len(asset_ids),
                "dry_run": True,
            }
        payload = {
            "name": name,
            "assetIds": asset_ids,
            "description": description,
        }
        url = self._url("albums")
        resp = self.session.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_album(self, album_id: str) -> dict:
        url = self._url(f"albums/{album_id}")
        resp = self.session.get(url)
        resp.raise_for_status()
        return resp.json()

    def add_assets_to_album(
        self, album_id: str, asset_ids: List[str]
    ) -> dict:
        if self.dry_run:
            return {
                "album_id": album_id,
                "added": len(asset_ids),
                "dry_run": True,
            }
        url = self._url(f"albums/{album_id}/assets")
        payload = {"assetIds": asset_ids}
        resp = self.session.put(url, json=payload)
        resp.raise_for_status()
        return resp.json()
