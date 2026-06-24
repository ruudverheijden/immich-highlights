import requests
from typing import Iterator, List, Optional


class ImmichClient:
    """Small wrapper around the Immich HTTP API used by the scorer."""

    def __init__(
        self,
        api_url: str,
        api_key: Optional[str] = None,
        dry_run: bool = True,
        verify: bool = False,
    ):
        self.base_url = api_url.rstrip("/")
        self.api_url = f"{self.base_url}/api"
        self.api_key = api_key
        self.session = requests.Session()
        self.dry_run = dry_run
        self._verified = False
        if api_key:
            # Immich uses a custom API-key header rather than bearer auth.
            self.session.headers.update({"x-api-key": api_key})
        if verify and api_key:
            try:
                self.verify_permissions()
                self._verified = True
            except Exception:
                # Construction should not fail just because an optional probe fails.
                pass

    def _url(self, path: str):
        """Join endpoint paths without leaking slash details to callers."""
        return f"{self.api_url}/{path.lstrip('/')}"

    def _json(self, resp: requests.Response):
        """Parse JSON or raise an error that explains what Immich returned."""
        try:
            return resp.json()
        except requests.exceptions.JSONDecodeError as e:
            content_type = resp.headers.get("content-type", "unknown")
            preview = resp.text[:200].replace("\n", " ")
            raise requests.exceptions.InvalidJSONError(
                "Immich returned non-JSON response from "
                f"{resp.url} "
                f"(status={resp.status_code}, content-type={content_type}): "
                f"{preview!r}"
            ) from e

    def search_assets(self, page: int = 1, size: int = 1000) -> dict:
        """Search assets through Immich's documented paginated metadata endpoint."""
        payload = {
            "page": page,
            "size": min(size, 1000),
            "type": "IMAGE",
            "withExif": True,
        }
        resp = self.session.post(self._url("search/metadata"), json=payload, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def list_assets(self, page: int = 1, per_page: int = 20) -> List[dict]:
        """Return one page of assets from Immich's metadata search endpoint."""
        results = self.search_assets(page=page, size=per_page)
        return results.get("assets", {}).get("items", [])

    def iter_assets(
        self, page_size: int = 1000, max_assets: Optional[int] = None
    ) -> Iterator[dict]:
        """Yield assets page-by-page until Immich has no next page."""
        page = 1
        yielded = 0
        while True:
            remaining = None if max_assets is None else max_assets - yielded
            if remaining is not None and remaining <= 0:
                return

            response = self.search_assets(
                page=page,
                size=min(page_size, remaining) if remaining else page_size,
            )
            asset_page = response.get("assets", {})
            items = asset_page.get("items", [])
            for asset in items:
                yield asset
                yielded += 1
                if max_assets is not None and yielded >= max_assets:
                    return

            next_page = asset_page.get("nextPage")
            if not next_page:
                return
            page = int(next_page)

    def get_asset_metadata(self, asset_id: str) -> dict:
        # The metadata endpoint returns metadata entries; the asset endpoint
        # returns the full AssetResponseDto that the scorer needs.
        url = self._url(f"assets/{asset_id}")
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def download_asset(self, asset_id: str, dest_path: str) -> str:
        url = self._url(f"assets/{asset_id}/original")
        resp = self.session.get(url, stream=True, timeout=30)
        return self._write_stream(resp, dest_path)

    def download_asset_preview(self, asset_id: str, dest_path: str) -> str:
        """Download an Immich-generated preview image for local scoring."""
        params = {"size": "preview"}
        url = self._url(f"assets/{asset_id}/thumbnail")
        resp = self.session.get(url, params=params, stream=True, timeout=30)
        return self._write_stream(resp, dest_path)

    def _write_stream(self, resp: requests.Response, dest_path: str) -> str:
        """Persist a streaming response to disk."""
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            # Stream downloads so large videos/photos do not sit fully in memory.
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
            # Preserve the response shape enough for callers to log/test safely.
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
        resp = self.session.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def get_album(self, album_id: str) -> dict:
        url = self._url(f"albums/{album_id}")
        resp = self.session.get(url, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def add_assets_to_album(self, album_id: str, asset_ids: List[str]) -> dict:
        if self.dry_run:
            # Report intended side effects without mutating the user's library.
            return {
                "album_id": album_id,
                "added": len(asset_ids),
                "dry_run": True,
            }
        url = self._url(f"albums/{album_id}/assets")
        payload = {"assetIds": asset_ids}
        resp = self.session.put(url, json=payload, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def verify_permissions(self) -> dict:
        """Perform lightweight checks to validate common Immich API permissions.

        This method attempts harmless GET requests for read-type permissions and
        reports results. Write/create/update permissions are harder to validate
        safely; when `dry_run` is True those checks are skipped and reported
        as unchecked.
        Returns a mapping of permission -> (ok: bool, detail: str).
        """
        checks = {}
        # Server health is a useful first failure point before endpoint-specific checks.
        try:
            r = self.session.get(self._url("server/about"), timeout=5)
            checks["server.about"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["server.about"] = (False, str(e))

        # asset read
        try:
            # A single item is enough to validate access while keeping probes cheap.
            payload = {"page": 1, "size": 1}
            r = self.session.post(self._url("search/metadata"), json=payload, timeout=5)
            checks["asset.read"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["asset.read"] = (False, str(e))

        # album read
        try:
            params = {"limit": 1}
            r = self.session.get(self._url("albums"), params=params, timeout=5)
            checks["album.read"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["album.read"] = (False, str(e))

        # tag read
        try:
            params = {"limit": 1}
            r = self.session.get(self._url("tags"), params=params, timeout=5)
            checks["tag.read"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["tag.read"] = (False, str(e))

        # face read
        try:
            params = {"limit": 1}
            r = self.session.get(self._url("faces"), params=params, timeout=5)
            checks["face.read"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["face.read"] = (False, str(e))

        # Write-type permissions cannot be safely validated by creating data here.
        write_perms = [
            "asset.update",
            "album.create",
            "album.update",
            "albumAsset.create",
            "albumAsset.delete",
            "tag.create",
            "tag.update",
        ]
        for p in write_perms:
            if self.dry_run:
                checks[p] = (None, "skipped (dry_run)")
            else:
                # OPTIONS is only a capability hint, but it avoids mutating albums.
                try:
                    r = self.session.options(self._url("albums"), timeout=5)
                    checks[p] = (
                        r.status_code in (200, 204),
                        str(r.status_code),
                    )
                except Exception as e:
                    checks[p] = (False, str(e))

        return checks
