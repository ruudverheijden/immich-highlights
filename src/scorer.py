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
    SCORER_BUCKET,
    TEMP_DIR,
    LOG_LEVEL,
)
from immich_client import ImmichClient
from db import init_db, upsert_processed_asset
from asset_analysis import get_asset_exif, score_asset
from album_manager import AlbumManager
from PIL import Image, UnidentifiedImageError
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


def immich_asset_url(asset_id: str) -> str:
    """Build a browser URL for opening an asset in Immich."""
    # Browser links use the base URL, while API calls use the client's /api URL.
    return f"{IMMICH_API_URL.rstrip('/')}/photos/{asset_id}"


def immich_album_url(album_id: str) -> str:
    """Build a browser URL for opening an album in Immich."""
    # Keep this separate from API URLs so logs can be clicked directly.
    return f"{IMMICH_API_URL.rstrip('/')}/albums/{album_id}"


def run_once():
    """Process one batch of Immich assets and generate a highlights album."""
    logger.info(
        "Starting scorer run: immich_url=%s, dry_run=%s, max_assets=%s",
        IMMICH_API_URL,
        SCORER_DRY_RUN,
        SCORER_MAX_ASSETS,
    )
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    conn = init_db(SCORER_DB_PATH)
    client = ImmichClient(
        IMMICH_API_URL,
        IMMICH_API_KEY,
        dry_run=SCORER_DRY_RUN,
    )
    try:
        perms = client.verify_permissions()
        # Asset reads are required; the other probes are advisory diagnostics.
        critical = ["asset.read"]
        # Keep startup logs compact by aggregating only probes that did not
        # return a successful HTTP status.
        failed_permissions = {
            permission: detail
            for permission, (ok, detail) in perms.items()
            if ok is False
        }
        if failed_permissions:
            message = ", ".join(
                f"{permission} -> {detail}"
                for permission, detail in failed_permissions.items()
            )
            if any(permission in critical for permission in failed_permissions):
                logger.warning("Permission checks failed: %s", message)
            else:
                logger.info("Permission checks failed: %s", message)
    except Exception as e:
        logger.warning("Permission verification failed: %s", e)
    alb_mgr = AlbumManager(client, conn)

    processed = []
    processed_count = 0
    try:
        iterator = client.iter_assets(
            page_size=min(SCORER_MAX_ASSETS, 1000),
            max_assets=SCORER_MAX_ASSETS,
        )
        for asset in iterator:
            # Immich identifiers have varied, so accept known aliases.
            asset_id = asset.get("id") or asset.get("assetId") or asset.get("uuid")
            if not asset_id:
                continue
            processed_count += 1
            try:
                meta = client.get_asset_metadata(asset_id)
            except Exception as e:
                # Metadata failures are asset-local; keep the batch moving.
                logger.exception("metadata failed for %s: %s", asset_id, e)
                continue
            tmp_path = os.path.join(TEMP_DIR, f"{asset_id}")
            try:
                # Preview thumbnails are consistently decodable even for HEIC originals.
                client.download_asset_preview(asset_id, tmp_path)
            except Exception as e:
                logger.exception("download failed for %s: %s", asset_id, e)
                continue
            try:
                pil = Image.open(tmp_path)
                details = score_asset(meta, pil)
                cs = checksum_file(tmp_path)
                if isinstance(meta, dict):
                    exif_val = get_asset_exif(meta)
                else:
                    # Be defensive around unexpected responses; the DB layer accepts {}.
                    exif_val = {}
                upsert_processed_asset(
                    conn,
                    asset_id,
                    cs,
                    details["score"],
                    exif_val,
                    details.get("rating"),
                    details.get("score_details"),
                )
                # Log enough scoring context to understand why a photo made the cut.
                logger.info(
                    "Scored photo %s (%s): score=%s, blur_variance=%s, "
                    "face_count=%s, face_quality=%s, portrait_quality=%s, "
                    "rating=%s, brightness=%s, url=%s",
                    asset_id,
                    meta.get("originalFileName", "unknown"),
                    details["score"],
                    details.get("blur_variance"),
                    details.get("face_count"),
                    details.get("face_quality"),
                    details.get("portrait_quality"),
                    details.get("rating"),
                    details.get("brightness"),
                    immich_asset_url(asset_id),
                )
                processed.append((asset_id, details["score"]))
            except UnidentifiedImageError as e:
                logger.warning("unsupported image file for %s: %s", asset_id, e)
            except Exception as e:
                logger.exception("scoring failed for %s: %s", asset_id, e)
            finally:
                try:
                    # Temporary files may contain original media; remove them promptly.
                    os.remove(tmp_path)
                except Exception:
                    pass
    except requests.RequestException as e:
        logger.error(
            "Unable to list Immich assets from %s: %s",
            IMMICH_API_URL,
            e,
        )
        return
    finally:
        if processed_count >= SCORER_MAX_ASSETS:
            logger.info(
                "Reached SCORER_MAX_ASSETS=%s, stopping early", SCORER_MAX_ASSETS
            )

    # Build a simple highlights album from the best-scoring assets in this batch.
    processed.sort(key=lambda x: x[1], reverse=True)
    top_ids = [p[0] for p in processed[:15]]
    if top_ids:
        name = f"Highlights: {SCORER_BUCKET}"
        # At this stage the list is already sorted, so the first ten are the
        # highest-scoring assets in this run, not necessarily the whole library.
        logger.info(
            "Ensuring highlights album '%s' contains %s scored assets",
            name,
            len(top_ids),
        )
        res = alb_mgr.ensure_album(
            name,
            top_ids,
            description="Auto-generated highlights (MVP)",
            bucket=SCORER_BUCKET,
        )
        album_id = res.get("id", "unknown")
        logger.info(
            "Album generated: id=%s, name=%s, asset_count=%s, dry_run=%s, url=%s",
            album_id,
            res.get("albumName", name),
            res.get("assetCount") or res.get("asset_count", len(top_ids)),
            res.get("dry_run", False),
            immich_album_url(album_id) if album_id != "unknown" else "unknown",
        )
    else:
        logger.info("No scored assets available; skipping album creation")


if __name__ == "__main__":
    run_once()
