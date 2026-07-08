"""Near-duplicate detection using pHash and timestamp-confirmed burst matching."""

import logging
from datetime import datetime

try:
    from .db import get_asset_record, get_technical_analysis, replace_duplicate_groups
except ImportError:
    from db import get_asset_record, get_technical_analysis, replace_duplicate_groups


logger = logging.getLogger("duplicate_detection")


def phash_hamming_distance(left: str, right: str) -> int:
    """Return bit distance between two hexadecimal perceptual hashes."""
    left_value = int(_normalize_phash(left), 16)
    right_value = int(_normalize_phash(right), 16)
    return (left_value ^ right_value).bit_count()


def _normalize_phash(value: str) -> str:
    """Normalize imagehash-style hex strings before distance comparison."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("pHash must be a non-empty hexadecimal string")
    normalized = value.strip().lower()
    int(normalized, 16)
    return normalized


def duplicate_groups_from_phashes(scored_assets, phashes, threshold, album_bucket):
    """Group near-duplicates around the highest-scoring available photo.

    This is a deliberately simple first pass: the best-scoring photo becomes the
    representative, and only assets within the threshold of that representative
    join its group. That avoids accidental chain grouping where A matches B and
    B matches C, but A and C are not actually very similar.
    """
    ordered_assets = sorted(
        enumerate(scored_assets),
        key=lambda item: (-item[1][1], item[0]),
    )
    assigned = set()
    groups = []

    for _, (asset_id, score) in ordered_assets:
        if asset_id in assigned or asset_id not in phashes:
            continue

        members = [{"asset_id": asset_id, "distance": 0, "score": score}]
        for _, (other_id, other_score) in ordered_assets:
            if other_id == asset_id or other_id in assigned or other_id not in phashes:
                continue
            distance = phash_hamming_distance(phashes[asset_id], phashes[other_id])
            if distance <= threshold:
                members.append(
                    {
                        "asset_id": other_id,
                        "distance": distance,
                        "score": other_score,
                    }
                )

        if len(members) > 1:
            for member in members:
                assigned.add(member["asset_id"])
            groups.append(
                {
                    "group_id": (f"{album_bucket}:phash:{len(groups) + 1}:{asset_id}"),
                    "representative_asset_id": asset_id,
                    "reason": f"phash_distance<={threshold}",
                    "members": members,
                }
            )

    return groups


def parse_asset_timestamp(value):
    """Parse common Immich/EXIF timestamp strings for duplicate comparisons."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    raw_value = str(value).strip()
    if not raw_value:
        return None

    iso_value = raw_value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_value)
    except ValueError:
        pass

    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(raw_value, fmt)
        except ValueError:
            continue
    return None


def seconds_between(left, right) -> float | None:
    """Return absolute seconds between two parsed timestamps."""
    if not left or not right:
        return None
    if left.tzinfo is None and right.tzinfo is not None:
        right = right.replace(tzinfo=None)
    if right.tzinfo is None and left.tzinfo is not None:
        left = left.replace(tzinfo=None)
    return abs((left - right).total_seconds())


def assigned_asset_ids(groups):
    """Return every asset already included in a duplicate group."""
    return {
        member["asset_id"] for group in groups for member in group.get("members", [])
    }


def duplicate_groups_from_timestamps(
    scored_assets,
    phashes,
    taken_at,
    window_seconds,
    phash_threshold,
    album_bucket,
    existing_groups=None,
):
    """Group burst-like duplicates using timestamp proximity plus pHash."""
    ordered_assets = sorted(
        enumerate(scored_assets),
        key=lambda item: (-item[1][1], item[0]),
    )
    assigned = assigned_asset_ids(existing_groups or [])
    groups = []

    for _, (asset_id, score) in ordered_assets:
        if asset_id in assigned or asset_id not in phashes or asset_id not in taken_at:
            continue

        members = [{"asset_id": asset_id, "distance": 0, "score": score}]
        for _, (other_id, other_score) in ordered_assets:
            if (
                other_id == asset_id
                or other_id in assigned
                or other_id not in phashes
                or other_id not in taken_at
            ):
                continue
            time_distance = seconds_between(taken_at[asset_id], taken_at[other_id])
            if time_distance is None or time_distance > window_seconds:
                continue

            phash_distance = phash_hamming_distance(
                phashes[asset_id], phashes[other_id]
            )
            if phash_distance <= phash_threshold:
                members.append(
                    {
                        "asset_id": other_id,
                        "distance": phash_distance,
                        "score": other_score,
                    }
                )

        if len(members) > 1:
            for member in members:
                assigned.add(member["asset_id"])
            groups.append(
                {
                    "group_id": (
                        f"{album_bucket}:timestamp:{len(groups) + 1}:{asset_id}"
                    ),
                    "representative_asset_id": asset_id,
                    "reason": (
                        f"timestamp<={window_seconds}s"
                        f"+phash_distance<={phash_threshold}"
                    ),
                    "members": members,
                }
            )

    return groups


def duplicate_groups_from_immich(
    scored_assets,
    immich_duplicate_groups,
    album_bucket,
    existing_groups=None,
):
    """Convert Immich duplicate groups into album-scoped suppression groups."""
    scored_by_id = {asset_id: score for asset_id, score in scored_assets}
    scored_order = {
        asset_id: index for index, (asset_id, _score) in enumerate(scored_assets)
    }
    assigned = assigned_asset_ids(existing_groups or [])
    groups = []

    for immich_group in immich_duplicate_groups or []:
        group_asset_ids = []
        for asset in immich_group.get("assets") or []:
            asset_id = asset.get("id") if isinstance(asset, dict) else None
            if (
                asset_id
                and asset_id in scored_by_id
                and asset_id not in assigned
                and asset_id not in group_asset_ids
            ):
                group_asset_ids.append(asset_id)

        if len(group_asset_ids) < 2:
            continue

        representative_id = sorted(
            group_asset_ids,
            key=lambda asset_id: (-scored_by_id[asset_id], scored_order[asset_id]),
        )[0]
        members = [
            {
                "asset_id": asset_id,
                "distance": 0 if asset_id == representative_id else None,
                "score": scored_by_id[asset_id],
            }
            for asset_id in group_asset_ids
        ]
        for member in members:
            assigned.add(member["asset_id"])
        duplicate_id = immich_group.get("duplicateId") or len(groups) + 1
        groups.append(
            {
                "group_id": f"{album_bucket}:immich:{duplicate_id}",
                "representative_asset_id": representative_id,
                "reason": "immich_duplicate",
                "members": members,
            }
        )

    return groups


def deduplicate_scored_assets(
    conn,
    album_bucket,
    scored_assets,
    enabled=True,
    threshold=6,
    timestamp_enabled=True,
    timestamp_window_seconds=2,
    timestamp_phash_threshold=10,
    immich_duplicate_groups=None,
):
    """Suppress near-duplicate assets after scoring and persist the groups."""
    if not enabled:
        replace_duplicate_groups(conn, album_bucket, [])
        return scored_assets

    phashes = {}
    taken_at = {}
    for asset_id, _score in scored_assets:
        technical = get_technical_analysis(conn, asset_id) or {}
        phash = technical.get("phash")
        if not phash:
            continue
        try:
            _normalize_phash(phash)
        except ValueError:
            logger.warning(
                "Skipping invalid pHash for duplicate detection: %s", asset_id
            )
            continue
        phashes[asset_id] = phash

        asset_record = get_asset_record(conn, asset_id) or {}
        parsed_taken_at = parse_asset_timestamp(asset_record.get("taken_at"))
        if parsed_taken_at:
            taken_at[asset_id] = parsed_taken_at

    groups = duplicate_groups_from_phashes(
        scored_assets,
        phashes,
        threshold,
        album_bucket,
    )
    if timestamp_enabled:
        groups.extend(
            duplicate_groups_from_timestamps(
                scored_assets,
                phashes,
                taken_at,
                timestamp_window_seconds,
                timestamp_phash_threshold,
                album_bucket,
                existing_groups=groups,
            )
        )
    groups.extend(
        duplicate_groups_from_immich(
            scored_assets,
            immich_duplicate_groups,
            album_bucket,
            existing_groups=groups,
        )
    )
    replace_duplicate_groups(conn, album_bucket, groups)

    suppressed_ids = {
        member["asset_id"]
        for group in groups
        for member in group["members"]
        if member["asset_id"] != group["representative_asset_id"]
    }
    if groups:
        logger.info(
            "Duplicate detection for album bucket '%s': groups=%s, suppressed=%s, "
            "threshold=%s",
            album_bucket,
            len(groups),
            len(suppressed_ids),
            threshold,
        )

    return [
        (asset_id, score)
        for asset_id, score in scored_assets
        if asset_id not in suppressed_ids
    ]
