"""Selection helpers that choose final album assets from scored candidates."""


def select_top_scored_assets(
    scored_assets: list[tuple[str, int]], limit: int
) -> list[str]:
    """Select the highest-scoring asset ids for an album."""
    scored_assets.sort(key=lambda item: item[1], reverse=True)
    return [asset_id for asset_id, _score in scored_assets[:limit]]
