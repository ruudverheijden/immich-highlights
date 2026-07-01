def get_asset_id(asset: dict) -> str | None:
    """Return an Immich asset identifier across known response shapes."""
    return asset.get("id") or asset.get("assetId") or asset.get("uuid")


def get_asset_checksum(asset: dict, meta: dict) -> str | None:
    """Return the stable Immich checksum when available."""
    return meta.get("checksum") or asset.get("checksum")


def get_asset_taken_at(asset: dict, meta: dict | None = None) -> str | None:
    """Return the best available original photo timestamp from Immich data."""
    meta = meta or {}
    exif = {}
    for source in (meta, asset):
        candidate = source.get("exifInfo") or source.get("exif")
        if isinstance(candidate, dict):
            exif.update(candidate)

    for source in (exif, meta, asset):
        for key in (
            "localDateTime",
            "dateTimeOriginal",
            "DateTimeOriginal",
            "dateTime",
            "DateTime",
            "fileCreatedAt",
            "createdAt",
        ):
            value = source.get(key)
            if value:
                return str(value)
    return None


def iter_rule_assets(client, rule):
    """Yield Immich assets that match a single album rule."""
    yield from client.iter_assets(
        page_size=min(rule.max_candidates, 1000),
        max_assets=rule.max_candidates,
        taken_after=rule.taken_after_iso(),
        taken_before=rule.taken_before_iso(),
    )
