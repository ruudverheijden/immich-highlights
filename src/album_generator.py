"""Final album sync stage that creates or updates Immich albums from selected assets."""

import logging

import requests

try:
    from .curation import curate_assets_for_rule, score_or_reuse_asset
    from .scoring_engine import DEFAULT_SCORING_CONFIG
    from .semantic_analysis import collect_content_filter_matches, content_filter_state
except ImportError:
    from curation import curate_assets_for_rule, score_or_reuse_asset
    from scoring_engine import DEFAULT_SCORING_CONFIG
    from semantic_analysis import collect_content_filter_matches, content_filter_state


logger = logging.getLogger("album_generator")

__all__ = [
    "collect_content_filter_matches",
    "content_filter_state",
    "generate_album_for_rule",
    "generate_albums",
    "score_or_reuse_asset",
    "sync_album_for_rule",
]


def immich_album_url(base_url: str, album_id: str) -> str:
    """Build a browser URL for opening an album in Immich."""
    return f"{base_url.rstrip('/')}/albums/{album_id}"


def sync_album_for_rule(album_manager, rule, asset_ids: list[str], base_url: str):
    """Create or update the Immich album for already-selected assets."""
    if not asset_ids:
        logger.info("No selected assets available for album '%s'", rule.name)
        return None

    result = album_manager.ensure_album(
        rule.name,
        asset_ids,
        description="Auto-generated highlights",
        bucket=rule.bucket,
    )
    album_id = result.get("id", "unknown")
    logger.info(
        "Album generated: id=%s, name=%s, asset_count=%s, dry_run=%s, url=%s",
        album_id,
        result.get("albumName", rule.name),
        result.get("assetCount") or result.get("asset_count", len(asset_ids)),
        result.get("dry_run", False),
        immich_album_url(base_url, album_id) if album_id != "unknown" else "unknown",
    )
    return result


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
    """Curate selected assets, then sync only the final album to Immich."""
    selected_asset_ids = curate_assets_for_rule(
        client,
        conn,
        rule,
        temp_dir,
        base_url,
        content_filters=content_filters,
        scoring_config=scoring_config,
        force_rescore=force_rescore,
    )
    return sync_album_for_rule(album_manager, rule, selected_asset_ids, base_url)


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
