"""Filtering stage that records per-album candidate include/exclude decisions."""

import logging
from dataclasses import dataclass

try:
    from .asset_discovery import get_asset_checksum, get_asset_id, get_asset_taken_at
    from .db import upsert_asset_filter_result, upsert_asset_record
except ImportError:
    from asset_discovery import get_asset_checksum, get_asset_id, get_asset_taken_at
    from db import upsert_asset_filter_result, upsert_asset_record


logger = logging.getLogger("filtering")


@dataclass(frozen=True)
class FilterDecision:
    """Explain whether a discovered asset continues through the pipeline."""

    asset: dict
    asset_id: str
    included: bool
    reason: str


def filter_album_candidates(conn, rule, candidates: list[dict]) -> list[dict]:
    """Store filter decisions and return assets allowed into analysis/scoring.

    Immich already scopes discovery to normal timeline images. This stage is
    intentionally explicit anyway: it gives later filtering rules a stable home
    and makes every per-album candidate decision visible in SQLite.
    """
    decisions = [
        decision
        for asset in candidates
        if (decision := filter_album_candidate(rule, asset)) is not None
    ]
    for decision in decisions:
        upsert_asset_record(
            conn,
            decision.asset_id,
            get_asset_checksum(decision.asset, {}),
            decision.asset.get("exifInfo") or decision.asset.get("exif") or {},
            decision.asset.get("rating"),
            taken_at=get_asset_taken_at(decision.asset),
        )
        upsert_asset_filter_result(
            conn,
            decision.asset_id,
            rule.bucket,
            decision.included,
            decision.reason,
            details={"album_name": rule.name},
        )
    conn.commit()

    included = [decision.asset for decision in decisions if decision.included]
    rejected_count = len(decisions) - len(included)
    logger.info(
        "Filtering album '%s': candidates=%s, included=%s, rejected=%s",
        rule.name,
        len(candidates),
        len(included),
        rejected_count,
    )
    return included


def filter_album_candidate(rule, asset: dict) -> FilterDecision | None:
    """Return one filter decision for a discovered asset.

    Assets without an identifier cannot be tracked, scored, or synced to Immich,
    so they are skipped instead of being written to the stage table.
    """
    asset_id = get_asset_id(asset)
    if not asset_id:
        logger.warning(
            "Skipping candidate without an Immich asset id for album '%s'",
            rule.name,
        )
        return None

    return FilterDecision(
        asset=asset,
        asset_id=asset_id,
        included=True,
        reason="accepted_timeline_image_candidate",
    )
