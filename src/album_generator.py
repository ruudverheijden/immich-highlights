import hashlib
import logging
import os
from datetime import timedelta
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

try:
    from .asset_analysis import get_asset_exif, score_asset
    from .db import get_processed_asset, upsert_processed_asset
    from .scoring_engine import DEFAULT_SCORING_CONFIG, calculate_score_details
except ImportError:
    from asset_analysis import get_asset_exif, score_asset
    from db import get_processed_asset, upsert_processed_asset
    from scoring_engine import DEFAULT_SCORING_CONFIG, calculate_score_details


logger = logging.getLogger("album_generator")
CONTENT_FILTER_MAX_CONTEXT_DAYS = 365


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


def content_filter_state(
    matches: list[dict],
    scoring_config=DEFAULT_SCORING_CONFIG,
) -> tuple[list[str], int]:
    """Return labels and the penalty from the strongest smart-search match."""
    labels = [match["label"] for match in matches]
    if not matches:
        return labels, 0

    # A photo can appear in multiple smart-search filters. Stacking every
    # penalty overreacts to similar queries, so the score uses only the filter
    # where Immich ranked the photo highest. Rank 1 is strongest.
    strongest_match = min(matches, key=lambda match: match.get("rank", 999999))
    return labels, max(
        scoring_config.content_filter_min_penalty,
        strongest_match["penalty"],
    )


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


def recalculate_cached_score(
    conn,
    cached: dict,
    asset_id: str,
    checksum: str | None,
    meta: dict,
    scoring_config=DEFAULT_SCORING_CONFIG,
):
    """Recompute a cached asset score from stored inputs when possible."""
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
            details = score_asset(
                meta,
                pil,
                immich_faces=immich_faces,
                content_filter_matches=content_filter_matches,
                content_filter_penalty=content_penalty,
                scoring_config=scoring_config,
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


def content_filter_search_windows(rule):
    """Yield widening context windows used to make smart-search filters reliable."""
    # Immich smart search returns ranked results, not confidence scores. If we
    # ask for the "best" screenshot-like photos inside a tiny album window, the
    # last returned items may simply be the least-bad matches. Widening the
    # context gives Immich enough photos to make the top results meaningful.
    album_days = max(1, (rule.taken_before - rule.taken_after).days)
    days = album_days
    yielded = set()
    while True:
        window_days = max(album_days, min(days, CONTENT_FILTER_MAX_CONTEXT_DAYS))
        if window_days not in yielded:
            yielded.add(window_days)
            yield rule.taken_before - timedelta(days=window_days), rule.taken_before
        if window_days >= CONTENT_FILTER_MAX_CONTEXT_DAYS:
            return
        days *= 2


def content_filter_context_window(client, rule, content_filter):
    """Find the smallest widened window with enough photos for smart search."""
    last_window = None
    last_count = 0
    for taken_after, taken_before in content_filter_search_windows(rule):
        count = client.count_assets(
            taken_after=taken_after.isoformat(),
            taken_before=taken_before.isoformat(),
        )
        logger.debug(
            "Content filter '%s' context window for album '%s': "
            "takenAfter=%s, takenBefore=%s, pool=%s, required=%s",
            content_filter.label,
            rule.name,
            taken_after.isoformat(),
            taken_before.isoformat(),
            count,
            content_filter.min_search_pool,
        )
        last_window = (taken_after, taken_before)
        last_count = count
        if count >= content_filter.min_search_pool:
            return taken_after, taken_before, count
    return (*last_window, last_count) if last_window else (None, None, 0)


def collect_content_filter_matches(
    client, rule, content_filters, candidate_asset_ids: set[str]
) -> dict[str, list[dict]]:
    """Run configured smart searches and index filter matches by asset id."""
    matches_by_asset_id = {}
    for content_filter in content_filters:
        taken_after, taken_before, pool_count = content_filter_context_window(
            client,
            rule,
            content_filter,
        )
        if pool_count < content_filter.min_search_pool:
            logger.info(
                "Skipping content filter '%s' for album '%s': "
                "largest context pool has %s assets, required=%s",
                content_filter.label,
                rule.name,
                pool_count,
                content_filter.min_search_pool,
            )
            continue

        logger.info(
            "Running content filter '%s' with smart search query=%r for album '%s' "
            "against context pool of %s assets",
            content_filter.label,
            content_filter.query,
            rule.name,
            pool_count,
        )
        filter_match_count = 0
        filter_overlap_count = 0
        for rank, asset in enumerate(
            client.iter_smart_search_assets(
                query=content_filter.query,
                page_size=min(content_filter.max_results, 1000),
                max_assets=content_filter.max_results,
                taken_after=taken_after.isoformat(),
                taken_before=taken_before.isoformat(),
            ),
            start=1,
        ):
            asset_id = get_asset_id(asset)
            if not asset_id:
                continue
            filter_match_count += 1
            if asset_id not in candidate_asset_ids:
                continue
            filter_overlap_count += 1
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
            "Content filter '%s' returned %s ranked assets and matched %s "
            "album candidates for album '%s'",
            content_filter.label,
            filter_match_count,
            filter_overlap_count,
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
