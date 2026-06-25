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
MAX_CONTENT_FILTER_PENALTY = -50


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


def cached_content_filter_state(cached: dict) -> tuple[list[str], int]:
    """Read stored content-filter state from cached score details."""
    inputs = cached.get("score_details", {}).get("inputs", {})
    return (
        inputs.get("content_labels", []),
        inputs.get("content_filter_penalty", 0),
    )


def content_filter_state(matches: list[dict]) -> tuple[list[str], int]:
    """Return labels and capped total penalty for smart-search matches."""
    labels = [match["label"] for match in matches]
    penalty = sum(match["penalty"] for match in matches)
    return labels, max(MAX_CONTENT_FILTER_PENALTY, penalty)


def get_asset_exif_for_storage(meta: dict) -> dict:
    """Return EXIF plus useful asset-level datetime fields for review exports."""
    exif = dict(get_asset_exif(meta) if isinstance(meta, dict) else {})
    for key in ("localDateTime", "fileCreatedAt", "createdAt"):
        if key in meta and key not in exif:
            exif[key] = meta[key]
    return exif


def immich_asset_url(base_url: str, asset_id: str) -> str:
    """Build a browser URL for opening an asset in Immich."""
    return f"{base_url.rstrip('/')}/photos/{asset_id}"


def immich_album_url(base_url: str, album_id: str) -> str:
    """Build a browser URL for opening an album in Immich."""
    return f"{base_url.rstrip('/')}/albums/{album_id}"


def score_or_reuse_asset(
    client,
    conn,
    asset: dict,
    temp_dir: str,
    base_url: str,
    content_filter_matches: list[dict] | None = None,
):
    """Return `(asset_id, score)` by using the DB cache or scoring the preview."""
    asset_id = get_asset_id(asset)
    if not asset_id:
        return None

    content_filter_matches = content_filter_matches or []
    content_labels, content_penalty = content_filter_state(content_filter_matches)
    meta = client.get_asset_metadata(asset_id)
    checksum = get_asset_checksum(asset, meta)
    cached = get_processed_asset(conn, asset_id)
    cached_state = cached_content_filter_state(cached) if cached else None
    if (
        cached
        and checksum
        and cached.get("checksum") == checksum
        and cached_state == (content_labels, content_penalty)
    ):
        logger.debug(
            "Reused cached score for photo %s: score=%s, url=%s",
            asset_id,
            cached["score"],
            immich_asset_url(base_url, asset_id),
        )
        return asset_id, cached["score"]
    if cached and checksum and cached.get("checksum") == checksum:
        logger.info(
            "Rescoring photo %s because content filter state changed: " "old=%s new=%s",
            asset_id,
            cached_state,
            (content_labels, content_penalty),
        )

    tmp_path = os.path.join(temp_dir, asset_id)
    try:
        client.download_asset_preview(asset_id, tmp_path)
        immich_faces = client.get_asset_faces(asset_id)
        with Image.open(tmp_path) as pil:
            details = score_asset(
                meta,
                pil,
                immich_faces=immich_faces,
                content_filter_matches=content_filter_matches,
                content_filter_penalty=content_penalty,
            )

        checksum = checksum or checksum_file(tmp_path)
        exif_val = get_asset_exif_for_storage(meta)
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
            "rating=%s, brightness=%s, content_labels=%s, url=%s",
            asset_id,
            meta.get("originalFileName", "unknown"),
            details["score"],
            details.get("blur_variance"),
            details.get("face_count"),
            details.get("face_quality"),
            details.get("portrait_quality"),
            details.get("rating"),
            details.get("brightness"),
            details.get("content_labels"),
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


def collect_content_filter_matches(
    client, rule, content_filters
) -> dict[str, list[dict]]:
    """Run configured smart searches and index filter matches by asset id."""
    matches_by_asset_id = {}
    for content_filter in content_filters:
        logger.info(
            "Running content filter '%s' with smart search query=%r for album '%s'",
            content_filter.label,
            content_filter.query,
            rule.name,
        )
        filter_match_count = 0
        for rank, asset in enumerate(
            client.iter_smart_search_assets(
                query=content_filter.query,
                page_size=min(content_filter.max_results, 1000),
                max_assets=content_filter.max_results,
                taken_after=rule.taken_after_iso(),
                taken_before=rule.taken_before_iso(),
            ),
            start=1,
        ):
            asset_id = get_asset_id(asset)
            if not asset_id:
                continue
            filter_match_count += 1
            logger.debug(
                "Content filter '%s' rank=%s matched asset %s for album '%s'",
                content_filter.label,
                rank,
                asset_id,
                rule.name,
            )
            matches_by_asset_id.setdefault(asset_id, []).append(
                {
                    "label": content_filter.label,
                    "query": content_filter.query,
                    "penalty": content_filter.penalty,
                    "rank": rank,
                }
            )
        logger.info(
            "Content filter '%s' matched %s assets for album '%s'",
            content_filter.label,
            filter_match_count,
            rule.name,
        )
    logger.info(
        "Content filters matched %s unique assets for album '%s'",
        len(matches_by_asset_id),
        rule.name,
    )
    return matches_by_asset_id


def generate_album_for_rule(
    client,
    conn,
    album_manager,
    rule,
    temp_dir: str,
    base_url: str,
    content_filters=None,
):
    """Score a rule's Immich candidates and create or update its album."""
    logger.info(
        "Generating album '%s' from Immich query: takenAfter=%s, takenBefore=%s",
        rule.name,
        rule.taken_after_iso(),
        rule.taken_before_iso(),
    )
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    content_matches = collect_content_filter_matches(
        client,
        rule,
        content_filters or [],
    )
    scored = []
    candidate_count = 0
    penalized_candidate_count = 0
    for asset in iter_rule_assets(client, rule):
        candidate_count += 1
        asset_id = get_asset_id(asset)
        asset_content_matches = content_matches.get(asset_id, [])
        if asset_content_matches:
            penalized_candidate_count += 1
            logger.info(
                "Applying content filter labels to asset %s for album '%s': %s",
                asset_id,
                rule.name,
                [
                    f"{match['label']}#{match.get('rank')}"
                    for match in asset_content_matches
                ],
            )
        result = score_or_reuse_asset(
            client,
            conn,
            asset,
            temp_dir,
            base_url,
            content_filter_matches=asset_content_matches,
        )
        if result:
            scored.append(result)
    logger.info(
        "Album '%s' candidates=%s, scored=%s, candidates_with_content_penalty=%s",
        rule.name,
        candidate_count,
        len(scored),
        penalized_candidate_count,
    )

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


def generate_albums(
    client,
    conn,
    album_manager,
    rules,
    temp_dir: str,
    base_url: str,
    content_filters=None,
):
    """Generate all configured highlight albums."""
    results = []
    for rule in rules:
        try:
            result = generate_album_for_rule(
                client,
                conn,
                album_manager,
                rule,
                temp_dir,
                base_url,
                content_filters=content_filters,
            )
        except requests.RequestException:
            logger.exception("Immich API failed while generating '%s'", rule.name)
            raise
        if result:
            results.append(result)
    return results
