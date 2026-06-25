import hashlib
import logging
import os
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

try:
    from .asset_analysis import get_asset_exif, score_asset
    from .db import get_processed_asset, upsert_processed_asset
except ImportError:
    from asset_analysis import get_asset_exif, score_asset
    from db import get_processed_asset, upsert_processed_asset


logger = logging.getLogger("album_generator")


def checksum_file(path: str) -> str:
    """Hash downloaded bytes when Immich does not expose an asset checksum."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_asset_id(asset: dict) -> str | None:
    """Return an Immich asset identifier across known response shapes."""
    return asset.get("id") or asset.get("assetId") or asset.get("uuid")


def get_asset_checksum(asset: dict, meta: dict) -> str | None:
    """Return the stable Immich checksum when available."""
    return meta.get("checksum") or asset.get("checksum")


def immich_asset_url(base_url: str, asset_id: str) -> str:
    """Build a browser URL for opening an asset in Immich."""
    return f"{base_url.rstrip('/')}/photos/{asset_id}"


def immich_album_url(base_url: str, album_id: str) -> str:
    """Build a browser URL for opening an album in Immich."""
    return f"{base_url.rstrip('/')}/albums/{album_id}"


def score_or_reuse_asset(client, conn, asset: dict, temp_dir: str, base_url: str):
    """Return `(asset_id, score)` by using the DB cache or scoring the preview."""
    asset_id = get_asset_id(asset)
    if not asset_id:
        return None

    meta = client.get_asset_metadata(asset_id)
    checksum = get_asset_checksum(asset, meta)
    cached = get_processed_asset(conn, asset_id)
    if cached and checksum and cached.get("checksum") == checksum:
        logger.debug(
            "Reused cached score for photo %s: score=%s, url=%s",
            asset_id,
            cached["score"],
            immich_asset_url(base_url, asset_id),
        )
        return asset_id, cached["score"]

    tmp_path = os.path.join(temp_dir, asset_id)
    try:
        client.download_asset_preview(asset_id, tmp_path)
        immich_faces = client.get_asset_faces(asset_id)
        with Image.open(tmp_path) as pil:
            details = score_asset(meta, pil, immich_faces=immich_faces)

        checksum = checksum or checksum_file(tmp_path)
        exif_val = get_asset_exif(meta) if isinstance(meta, dict) else {}
        upsert_processed_asset(
            conn,
            asset_id,
            checksum,
            details["score"],
            exif_val,
            details.get("rating"),
            details.get("score_details"),
        )
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
            immich_asset_url(base_url, asset_id),
        )
        return asset_id, details["score"]
    except UnidentifiedImageError as e:
        logger.warning("unsupported image file for %s: %s", asset_id, e)
        return None
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def iter_rule_assets(client, rule):
    """Yield Immich assets that match a single album rule."""
    yield from client.iter_assets(
        page_size=min(rule.max_candidates, 1000),
        max_assets=rule.max_candidates,
        taken_after=rule.taken_after_iso(),
        taken_before=rule.taken_before_iso(),
    )


def generate_album_for_rule(
    client, conn, album_manager, rule, temp_dir: str, base_url: str
):
    """Score a rule's Immich candidates and create or update its album."""
    logger.info(
        "Generating album '%s' from Immich query: takenAfter=%s, takenBefore=%s",
        rule.name,
        rule.taken_after_iso(),
        rule.taken_before_iso(),
    )
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    scored = []
    for asset in iter_rule_assets(client, rule):
        result = score_or_reuse_asset(client, conn, asset, temp_dir, base_url)
        if result:
            scored.append(result)

    scored.sort(key=lambda item: item[1], reverse=True)
    top_ids = [asset_id for asset_id, _score in scored[: rule.limit]]
    if not top_ids:
        logger.info("No scored assets available for album '%s'", rule.name)
        return None

    result = album_manager.ensure_album(
        rule.name,
        top_ids,
        description="Auto-generated highlights",
        bucket=rule.bucket,
    )
    album_id = result.get("id", "unknown")
    logger.info(
        "Album generated: id=%s, name=%s, asset_count=%s, dry_run=%s, url=%s",
        album_id,
        result.get("albumName", rule.name),
        result.get("assetCount") or result.get("asset_count", len(top_ids)),
        result.get("dry_run", False),
        immich_album_url(base_url, album_id) if album_id != "unknown" else "unknown",
    )
    return result


def generate_albums(client, conn, album_manager, rules, temp_dir: str, base_url: str):
    """Generate all configured highlight albums."""
    results = []
    for rule in rules:
        try:
            result = generate_album_for_rule(
                client, conn, album_manager, rule, temp_dir, base_url
            )
        except requests.RequestException:
            logger.exception("Immich API failed while generating '%s'", rule.name)
            raise
        if result:
            results.append(result)
    return results
