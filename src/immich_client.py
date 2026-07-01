"""HTTP client wrapper for the Immich API endpoints used by the service."""

import requests
from typing import Iterator, List, Optional
import logging


logger = logging.getLogger("immich_client")


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
        # Keep configuration friendly for humans: callers pass the browser base URL,
        # and the client adds the API prefix for all HTTP calls.
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

    def _timeline_image_search_payload(self) -> dict:
        """Return the common filters for highlight-eligible image searches."""
        return {
            # This service scores still images, so skip videos/audio early.
            "type": "IMAGE",
            # Only score normal timeline photos. Archived/hidden/locked assets are
            # intentional user exclusions and should not become highlights.
            "visibility": "timeline",
            # Be explicit about deleted/trashed assets so this does not depend on
            # Immich endpoint defaults changing between versions.
            "withDeleted": False,
        }

    def search_assets(
        self,
        page: int = 1,
        size: int = 1000,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> dict:
        """Search assets through Immich's documented paginated metadata endpoint."""
        # Immich metadata search endpoint is the stable way to page through assets.
        payload = {
            **self._timeline_image_search_payload(),
            "page": page,
            "size": min(size, 1000),
            "withExif": True,
        }
        if taken_after:
            payload["takenAfter"] = taken_after
        if taken_before:
            payload["takenBefore"] = taken_before
        resp = self.session.post(self._url("search/metadata"), json=payload, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def smart_search_assets(
        self,
        query: str,
        page: int = 1,
        size: int = 1000,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> dict:
        """Search assets using Immich's contextual smart-search endpoint."""
        payload = {
            **self._timeline_image_search_payload(),
            "query": query,
            "page": page,
            "size": min(size, 1000),
            "withExif": True,
        }
        if taken_after:
            payload["takenAfter"] = taken_after
        if taken_before:
            payload["takenBefore"] = taken_before
        resp = self.session.post(self._url("search/smart"), json=payload, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def search_statistics(
        self,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> dict:
        """Return aggregate search statistics for image assets."""
        payload = self._timeline_image_search_payload()
        if taken_after:
            payload["takenAfter"] = taken_after
        if taken_before:
            payload["takenBefore"] = taken_before
        resp = self.session.post(
            self._url("search/statistics"),
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return self._json(resp)

    def list_assets(
        self,
        page: int = 1,
        per_page: int = 20,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> List[dict]:
        """Return one page of assets from Immich's metadata search endpoint."""
        results = self.search_assets(
            page=page,
            size=per_page,
            taken_after=taken_after,
            taken_before=taken_before,
        )
        return results.get("assets", {}).get("items", [])

    def count_assets(
        self,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> int:
        """Return Immich's exact image count for a search window."""
        response = self.search_statistics(
            taken_after=taken_after,
            taken_before=taken_before,
        )
        total = response.get("total")
        if isinstance(total, int):
            return total
        raise ValueError(
            f"Immich statistics response is missing integer total: {response}"
        )

    def iter_assets(
        self,
        page_size: int = 1000,
        max_assets: Optional[int] = None,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> Iterator[dict]:
        """Yield assets page-by-page until Immich has no next page."""
        page = 1
        yielded = 0
        while True:
            # Trim the final request instead of fetching a full page we will ignore.
            remaining = None if max_assets is None else max_assets - yielded
            if remaining is not None and remaining <= 0:
                return

            response = self.search_assets(
                page=page,
                size=min(page_size, remaining) if remaining else page_size,
                taken_after=taken_after,
                taken_before=taken_before,
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
            # The OpenAPI schema describes nextPage as a token string, but in
            # practice it is the next numeric page for metadata search.
            page = int(next_page)

    def iter_smart_search_assets(
        self,
        query: str,
        page_size: int = 1000,
        max_assets: Optional[int] = None,
        taken_after: Optional[str] = None,
        taken_before: Optional[str] = None,
    ) -> Iterator[dict]:
        """Yield smart-search results page-by-page."""
        page = 1
        yielded = 0
        while True:
            remaining = None if max_assets is None else max_assets - yielded
            if remaining is not None and remaining <= 0:
                return

            response = self.smart_search_assets(
                query=query,
                page=page,
                size=min(page_size, remaining) if remaining else page_size,
                taken_after=taken_after,
                taken_before=taken_before,
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

    def get_asset_faces(self, asset_id: str) -> List[dict]:
        """Return Immich's own face detections for one asset."""
        # Immich stores face boxes against the original asset dimensions. The
        # analysis layer scales them to the preview image used for scoring.
        resp = self.session.get(self._url("faces"), params={"id": asset_id}, timeout=10)
        resp.raise_for_status()
        return self._json(resp)

    def download_asset(self, asset_id: str, dest_path: str) -> str:
        url = self._url(f"assets/{asset_id}/original")
        resp = self.session.get(url, stream=True, timeout=30)
        return self._write_stream(resp, dest_path)

    def download_asset_preview(self, asset_id: str, dest_path: str) -> str:
        """Download an Immich-generated preview image for local scoring."""
        # Previews are small and consistently decodable, unlike some originals
        # such as HEIC files on systems without the right codec support.
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
                "albumName": name,
                "asset_count": len(asset_ids),
                "dry_run": True,
            }
        payload = {
            # Immich's DTO uses albumName; using "name" silently does not create
            # the expected album in current API versions.
            "albumName": name,
            "assetIds": asset_ids,
            "description": description,
        }
        url = self._url("albums")
        resp = self.session.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        result = self._json(resp)
        logger.info(
            "Created Immich album '%s' with id=%s",
            result.get("albumName", name),
            result.get("id", "unknown"),
        )
        return result

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
        # Immich's single-album endpoint uses BulkIdsDto, whose field is "ids".
        payload = {"ids": asset_ids}
        resp = self.session.put(url, json=payload, timeout=10)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            if resp.status_code == 403:
                raise PermissionError(
                    "Immich refused adding assets to the existing album. "
                    "Grant the API key the albumAsset.create permission, or the "
                    "scorer can only reuse the album when no new assets need adding."
                ) from e
            raise
        return self._json(resp)

    def remove_assets_from_album(self, album_id: str, asset_ids: List[str]) -> dict:
        """Remove assets from an existing Immich album."""
        if self.dry_run:
            return {
                "album_id": album_id,
                "removed": len(asset_ids),
                "dry_run": True,
            }
        url = self._url(f"albums/{album_id}/assets")
        payload = {"ids": asset_ids}
        resp = self.session.delete(url, json=payload, timeout=10)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            if resp.status_code == 403:
                raise PermissionError(
                    "Immich refused removing assets from the existing album. "
                    "Grant the API key the albumAsset.delete permission, or the "
                    "scorer cannot keep generated albums in sync."
                ) from e
            raise
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

        # asset read
        try:
            # A single item is enough to validate access while keeping probes cheap.
            payload = {
                **self._timeline_image_search_payload(),
                "page": 1,
                "size": 1,
            }
            r = self.session.post(self._url("search/metadata"), json=payload, timeout=5)
            checks["asset.read"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["asset.read"] = (False, str(e))

        # asset.statistics
        try:
            payload = self._timeline_image_search_payload()
            r = self.session.post(
                self._url("search/statistics"),
                json=payload,
                timeout=5,
            )
            checks["asset.statistics"] = (
                r.status_code == 200,
                str(r.status_code),
            )
        except Exception as e:
            checks["asset.statistics"] = (False, str(e))

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

        # face.read needs an asset id, so a generic startup probe would return a
        # false failure on current Immich versions. Per-asset face lookups are
        # mandatory for face scoring and will stop the run if they fail.
        checks["face.read"] = (None, "unchecked")

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
            # Immich does not provide a safe generic permission-test endpoint
            # for writes; OPTIONS can return false negatives even when writes work.
            checks[p] = (None, "unchecked")

        return checks
