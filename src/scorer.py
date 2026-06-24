import os
import logging
import requests
from pathlib import Path

from config import (
    IMMICH_API_URL,
    IMMICH_API_KEY,
    SCORER_DRY_RUN,
    SCORER_DB_PATH,
    SCORER_MAX_ASSETS,
    TEMP_DIR,
    LOG_LEVEL,
)
from immich_client import ImmichClient
from db import init_db, upsert_processed_asset
from scoring_engine import score_asset
from album_manager import AlbumManager
from PIL import Image
import hashlib


logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("scorer")


def checksum_file(path: str) -> str:
    """Hash downloaded bytes so rescans can detect changed assets."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        # Read in chunks to keep memory stable for large originals.
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def run_once():
    """Process one batch of Immich assets and generate a highlights album."""
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    conn = init_db(SCORER_DB_PATH)
    client = ImmichClient(
        IMMICH_API_URL,
        IMMICH_API_KEY,
        dry_run=SCORER_DRY_RUN,
    )
    try:
        perms = client.verify_permissions()
        logger.info("Permission check results: %s", perms)
        # These reads are required; write checks are advisory.
        critical = ["asset.read", "album.read", "server.about"]
        for p in critical:
            ok, detail = perms.get(p, (None, "missing"))
            if ok is False:
                logger.warning("Critical permission missing: %s -> %s", p, detail)
    except Exception as e:
        logger.warning("Permission verification failed: %s", e)
    alb_mgr = AlbumManager(client)

    try:
        # Simple list: first page only for MVP.
        assets = client.list_assets(page=1, per_page=20)
    except requests.RequestException as e:
        logger.error("Unable to list Immich assets from %s: %s", IMMICH_API_URL, e)
        return

    processed = []
    processed_count = 0
    if isinstance(assets, dict):
        # Immich versions/endpoints may return either a list or a paginated wrapper.
        iterator = assets.get("data", assets)
    else:
        iterator = assets
    for asset in iterator:
        # Bound each scheduled run so a large library does not monopolize the process.
        if processed_count >= SCORER_MAX_ASSETS:
            logger.info(
                "Reached SCORER_MAX_ASSETS=%s, stopping early",
                SCORER_MAX_ASSETS,
            )
            break
        # Immich identifiers have varied across API responses, so accept known aliases.
        asset_id = asset.get("id") or asset.get("assetId") or asset.get("uuid")
        if not asset_id:
            continue
        processed_count += 1
        try:
            meta = client.get_asset_metadata(asset_id)
        except Exception as e:
            logger.exception("metadata failed for %s: %s", asset_id, e)
            continue
        tmp_path = os.path.join(TEMP_DIR, f"{asset_id}")
        try:
            # Scoring libraries work with local files/PIL images, not streamed bytes.
            client.download_asset(asset_id, tmp_path)
        except Exception as e:
            logger.exception("download failed for %s: %s", asset_id, e)
            continue
        try:
            pil = Image.open(tmp_path)
            details = score_asset(meta, pil)
            cs = checksum_file(tmp_path)
            if isinstance(meta, dict):
                exif_val = meta.get("exif")
            else:
                # Be defensive around unexpected API responses; the DB layer accepts {}.
                exif_val = {}
            upsert_processed_asset(
                conn,
                asset_id,
                cs,
                details["score"],
                exif_val,
                details.get("blur_variance"),
                details.get("face_count"),
            )
            processed.append((asset_id, details["score"]))
        except Exception as e:
            logger.exception("scoring failed for %s: %s", asset_id, e)
        finally:
            try:
                # Temporary files may contain original media, so remove them promptly.
                os.remove(tmp_path)
            except Exception:
                pass

    # Build a simple highlights album from the best-scoring assets in this batch.
    processed.sort(key=lambda x: x[1], reverse=True)
    top_ids = [p[0] for p in processed[:10]]
    if top_ids:
        name = f"Highlights: {os.getenv('SCORER_BUCKET', 'MVP')}"
        res = alb_mgr.ensure_album(
            name, top_ids, description="Auto-generated highlights (MVP)"
        )
        logger.info("Album result: %s", res)


if __name__ == "__main__":
    run_once()
