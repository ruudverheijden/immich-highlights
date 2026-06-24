import os
import logging
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
logger = logging.getLogger('scorer')


def checksum_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def run_once():
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    conn = init_db(SCORER_DB_PATH)
    client = ImmichClient(
        IMMICH_API_URL,
        IMMICH_API_KEY,
        dry_run=SCORER_DRY_RUN,
    )
    alb_mgr = AlbumManager(client)

    # Simple list: first page only for MVP
    assets = client.list_assets(page=1, per_page=20)
    processed = []
    processed_count = 0
    if isinstance(assets, dict):
        iterator = assets.get("data", assets)
    else:
        iterator = assets
    for asset in iterator:
        # enforce maximum assets to process in a single run (useful for testing)
        if processed_count >= SCORER_MAX_ASSETS:
            logger.info("Reached SCORER_MAX_ASSETS=%s, stopping early", SCORER_MAX_ASSETS)
            break
        asset_id = asset.get('id') or asset.get('assetId') or asset.get('uuid')
        if not asset_id:
            continue
        processed_count += 1
        try:
            meta = client.get_asset_metadata(asset_id)
        except Exception as e:
            logger.exception('metadata failed for %s: %s', asset_id, e)
            continue
        tmp_path = os.path.join(TEMP_DIR, f"{asset_id}")
        try:
            client.download_asset(asset_id, tmp_path)
        except Exception as e:
            logger.exception('download failed for %s: %s', asset_id, e)
            continue
        try:
            pil = Image.open(tmp_path)
            details = score_asset(meta, pil)
            cs = checksum_file(tmp_path)
            exif_val = meta.get('exif') if isinstance(meta, dict) else {}
            upsert_processed_asset(
                conn,
                asset_id,
                cs,
                details['score'],
                exif_val,
                details.get('blur_variance'),
                details.get('face_count'),
            )
            processed.append((asset_id, details['score']))
        except Exception as e:
            logger.exception('scoring failed for %s: %s', asset_id, e)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # Build a simple album: top 10
    processed.sort(key=lambda x: x[1], reverse=True)
    top_ids = [p[0] for p in processed[:10]]
    if top_ids:
        name = f"Highlights: {os.getenv('SCORER_BUCKET', 'MVP')}"
        res = alb_mgr.ensure_album(
            name, top_ids, description="Auto-generated highlights (MVP)"
        )
        logger.info("Album result: %s", res)


if __name__ == '__main__':
    run_once()
