import logging

try:
    from .db import get_technical_analysis, replace_duplicate_groups
except ImportError:
    from db import get_technical_analysis, replace_duplicate_groups


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


def deduplicate_scored_assets(
    conn,
    album_bucket,
    scored_assets,
    enabled=True,
    threshold=6,
):
    """Suppress near-duplicate assets after scoring and persist the groups."""
    if not enabled:
        replace_duplicate_groups(conn, album_bucket, [])
        return scored_assets

    phashes = {}
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

    groups = duplicate_groups_from_phashes(
        scored_assets,
        phashes,
        threshold,
        album_bucket,
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
