import logging
import os
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

try:
    from .asset_discovery import get_asset_checksum, get_asset_id, iter_rule_assets
    from .db import get_processed_asset, get_scoring_inputs, upsert_processed_asset
    from .scoring_engine import DEFAULT_SCORING_CONFIG, calculate_score_details
    from .selection import select_top_scored_assets
    from .semantic_analysis import (
        analyze_semantic_metadata,
        cached_content_filter_state,
        collect_content_filter_matches,
        content_filter_state,
        get_asset_exif_for_storage,
    )
    from .technical_analysis import analyze_technical_image, checksum_file
except ImportError:
    from asset_discovery import get_asset_checksum, get_asset_id, iter_rule_assets
    from db import get_processed_asset, get_scoring_inputs, upsert_processed_asset
    from scoring_engine import DEFAULT_SCORING_CONFIG, calculate_score_details
    from selection import select_top_scored_assets
    from semantic_analysis import (
        analyze_semantic_metadata,
        cached_content_filter_state,
        collect_content_filter_matches,
        content_filter_state,
        get_asset_exif_for_storage,
    )
    from technical_analysis import analyze_technical_image, checksum_file


logger = logging.getLogger("album_generator")


def immich_asset_url(base_url: str, asset_id: str) -> str:
    """Build a browser URL for opening an asset in Immich."""
    return f"{base_url.rstrip('/')}/photos/{asset_id}"


def immich_album_url(base_url: str, album_id: str) -> str:
    """Build a browser URL for opening an album in Immich."""
    return f"{base_url.rstrip('/')}/albums/{album_id}"


def recalculate_cached_score(
    conn,
    cached: dict,
    asset_id: str,
    checksum: str | None,
    meta: dict,
    scoring_config=DEFAULT_SCORING_CONFIG,
):
    """Recompute a cached asset score from stored inputs when possible."""
    inputs = get_scoring_inputs(conn, asset_id)
    if not inputs:
        inputs = cached.get("score_details", {}).get("inputs")
    if not inputs:
        return None

    try:
        score_details = calculate_score_details(inputs, scoring_config)
    except (KeyError, TypeError, ValueError):
        logger.debug("Cached scoring inputs for photo %s are incomplete", asset_id)
        return None

    upsert_processed_asset(
        conn,
        asset_id,
        checksum,
        score_details["score"],
        cached.get("exif") or get_asset_exif_for_storage(meta),
        inputs.get("rating", cached.get("rating")),
        score_details,
    )
    return score_details


def score_or_reuse_asset(
    client,
    conn,
    asset: dict,
    temp_dir: str,
    base_url: str,
    content_filter_matches: list[dict] | None = None,
    scoring_config=DEFAULT_SCORING_CONFIG,
    force_rescore: bool = False,
):
    """Return `(asset_id, score)` by using the DB cache or scoring the preview."""
    asset_id = get_asset_id(asset)
    if not asset_id:
        return None

    content_filter_matches = content_filter_matches or []
    content_labels, content_penalty = content_filter_state(
        content_filter_matches,
        scoring_config,
    )
    meta = client.get_asset_metadata(asset_id)
    checksum = get_asset_checksum(asset, meta)
    cached = get_processed_asset(conn, asset_id)
    cached_state = cached_content_filter_state(cached) if cached else None
    if (
        not force_rescore
        and cached
        and checksum
        and cached.get("checksum") == checksum
        and cached_state == (content_labels, content_penalty)
    ):
        score_details = recalculate_cached_score(
            conn,
            cached,
            asset_id,
            checksum,
            meta,
            scoring_config,
        )
        if score_details:
            logger.debug(
                "Recalculated cached score for photo %s: score=%s, url=%s",
                asset_id,
                score_details["score"],
                immich_asset_url(base_url, asset_id),
            )
            return asset_id, score_details["score"]

        logger.debug(
            "Reused cached score for photo %s: score=%s, url=%s",
            asset_id,
            cached["score"],
            immich_asset_url(base_url, asset_id),
        )
        return asset_id, cached["score"]
    if force_rescore and cached:
        logger.info(
            "Force rescoring photo %s; ignoring cached score and analyzing preview",
            asset_id,
        )
    if not force_rescore and cached and checksum and cached.get("checksum") == checksum:
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
            semantic_details = analyze_semantic_metadata(
                meta,
                pil,
                immich_faces=immich_faces,
                content_filter_matches=content_filter_matches,
                content_filter_penalty=content_penalty,
            )
            technical_details = analyze_technical_image(
                pil,
                faces=semantic_details.get("faces", []),
            )
            details = {**technical_details, **semantic_details}
            score_details = calculate_score_details(details, scoring_config)
            details["score"] = score_details["score"]
            details["score_details"] = score_details

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


def generate_album_for_rule(
    client,
    conn,
    album_manager,
    rule,
    temp_dir: str,
    base_url: str,
    content_filters=None,
    scoring_config=DEFAULT_SCORING_CONFIG,
    force_rescore: bool = False,
):
    """Score a rule's Immich candidates and create or update its album."""
    logger.info(
        "Generating album '%s' from Immich query: takenAfter=%s, takenBefore=%s",
        rule.name,
        rule.taken_after_iso(),
        rule.taken_before_iso(),
    )
    Path(temp_dir).mkdir(parents=True, exist_ok=True)
    candidates = list(iter_rule_assets(client, rule))
    candidate_asset_ids = {
        asset_id for asset in candidates if (asset_id := get_asset_id(asset))
    }
    content_matches = collect_content_filter_matches(
        client,
        rule,
        content_filters or [],
        candidate_asset_ids,
        logger,
    )
    scored = []
    candidate_count = 0
    penalized_candidate_count = 0
    for asset in candidates:
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
            scoring_config=scoring_config,
            force_rescore=force_rescore,
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

    top_ids = select_top_scored_assets(scored, rule.limit)
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
    scoring_config=DEFAULT_SCORING_CONFIG,
    force_rescore: bool = False,
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
                scoring_config=scoring_config,
                force_rescore=force_rescore,
            )
        except requests.RequestException:
            logger.exception("Immich API failed while generating '%s'", rule.name)
            raise
        if result:
            results.append(result)
    return results
