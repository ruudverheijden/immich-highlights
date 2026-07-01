"""TOML-backed album and content-filter configuration models and loaders."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class AlbumRule:
    """Describe one generated highlights album and its Immich candidate query."""

    name: str
    bucket: str
    taken_after: datetime
    taken_before: datetime
    limit: int
    max_candidates: int

    def taken_after_iso(self) -> str:
        """Return the lower date bound in Immich's expected ISO format."""
        return _as_utc(self.taken_after).isoformat()

    def taken_before_iso(self) -> str:
        """Return the upper date bound in Immich's expected ISO format."""
        return _as_utc(self.taken_before).isoformat()


@dataclass(frozen=True)
class ContentFilter:
    """Describe one Immich smart-search label and scoring penalty."""

    label: str
    query: str
    penalty: int
    max_results: int
    min_search_pool: int = 500


def _as_utc(value: datetime) -> datetime:
    """Normalize naive and aware datetimes before serializing API filters."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_time_album_rules(
    now: datetime | None = None,
    max_candidates: int = 100,
    album_limit: int = 15,
) -> list[AlbumRule]:
    """Build the default rolling time-window highlight albums."""
    now = _as_utc(now or datetime.now(timezone.utc))
    return [
        AlbumRule(
            name="Highlights: Last Week",
            bucket="last-week",
            taken_after=now - timedelta(days=7),
            taken_before=now,
            limit=album_limit,
            max_candidates=max_candidates,
        ),
        AlbumRule(
            name="Highlights: Last Month",
            bucket="last-month",
            taken_after=now - timedelta(days=30),
            taken_before=now,
            limit=album_limit,
            max_candidates=max_candidates,
        ),
        AlbumRule(
            name="Highlights: Last Year",
            bucket="last-year",
            taken_after=now - timedelta(days=365),
            taken_before=now,
            limit=album_limit,
            max_candidates=max_candidates,
        ),
    ]


def build_default_content_filters() -> list[ContentFilter]:
    """Build default smart-search filters for non-highlight-like content."""
    return [
        ContentFilter(
            label="screenshot",
            query="screenshot",
            penalty=-40,
            max_results=25,
            min_search_pool=500,
        ),
        ContentFilter(
            label="document",
            query="document receipt paper with text",
            penalty=-25,
            max_results=25,
            min_search_pool=500,
        ),
        ContentFilter(
            label="display",
            query="computer screen phone screen monitor",
            penalty=-20,
            max_results=25,
            min_search_pool=500,
        ),
    ]


def load_album_config(
    path: str,
    content_filter_path: str | None = None,
    now: datetime | None = None,
    default_max_candidates: int = 100,
) -> tuple[list[AlbumRule], list[ContentFilter]]:
    """Load album rules and content filters from separate TOML config files."""
    return (
        load_album_rules(
            path,
            now=now,
            default_max_candidates=default_max_candidates,
        ),
        load_content_filters(content_filter_path),
    )


def load_album_rules(
    path: str,
    now: datetime | None = None,
    default_max_candidates: int = 100,
) -> list[AlbumRule]:
    """Load rolling time-window album rules from a TOML config file."""
    config_path = Path(path)
    if not config_path.exists():
        return build_time_album_rules(
            now=now,
            max_candidates=default_max_candidates,
        )

    data = _load_toml(config_path)
    return _load_album_rules_from_data(data, now, default_max_candidates)


def load_content_filters(path: str | None = None) -> list[ContentFilter]:
    """Load smart-search content filters from a TOML config file."""
    if not path:
        return build_default_content_filters()

    config_path = Path(path)
    if not config_path.exists():
        return build_default_content_filters()

    data = _load_toml(config_path)
    return _load_content_filters_from_data(data)


def _load_toml(config_path: Path) -> dict:
    """Read a TOML config file into a dictionary."""
    with config_path.open("rb") as f:
        return tomllib.load(f)


def _load_album_rules_from_data(
    data: dict,
    now: datetime | None,
    default_max_candidates: int,
) -> list[AlbumRule]:
    """Load and validate album rules from parsed TOML data."""
    albums = data.get("albums")
    if not isinstance(albums, list):
        raise ValueError("Album config must contain an [[albums]] list")

    rules = []
    for index, album in enumerate(albums, start=1):
        if not isinstance(album, dict):
            raise ValueError(f"Album config entry #{index} must be a table")
        if _enabled(album, index):
            rules.append(
                _album_rule_from_config(album, index, now, default_max_candidates)
            )
    if not rules:
        raise ValueError("Album config must enable at least one album")
    return rules


def _load_content_filters_from_data(data: dict) -> list[ContentFilter]:
    """Load and validate smart-search content filters from parsed TOML data."""
    filters = data.get("content_filters")
    if filters is None:
        return []
    if not isinstance(filters, list):
        raise ValueError(
            "Content filter config must contain a [[content_filters]] list"
        )

    content_filters = []
    for index, content_filter in enumerate(filters, start=1):
        if not isinstance(content_filter, dict):
            raise ValueError(f"Content filter config entry #{index} must be a table")
        if _enabled(content_filter, index, prefix="Content filter"):
            content_filters.append(_content_filter_from_config(content_filter, index))
    return content_filters


def _album_rule_from_config(
    album: dict,
    index: int,
    now: datetime | None,
    default_max_candidates: int,
) -> AlbumRule:
    """Validate one TOML album entry and convert it into an AlbumRule."""
    if not isinstance(album, dict):
        raise ValueError(f"Album config entry #{index} must be a table")

    name = _required_string(album, "name", index)
    bucket = _required_string(album, "bucket", index)
    window_days = _required_positive_int(album, "window_days", index)
    limit = _required_positive_int(album, "limit", index)
    max_candidates = _optional_positive_int(
        album,
        "max_candidates",
        index,
        default_max_candidates,
    )
    now = _as_utc(now or datetime.now(timezone.utc))
    return AlbumRule(
        name=name,
        bucket=bucket,
        taken_after=now - timedelta(days=window_days),
        taken_before=now,
        limit=limit,
        max_candidates=max_candidates,
    )


def _content_filter_from_config(content_filter: dict, index: int) -> ContentFilter:
    """Validate one content filter entry and convert it into a ContentFilter."""
    if not isinstance(content_filter, dict):
        raise ValueError(f"Content filter config entry #{index} must be a table")

    return ContentFilter(
        label=_required_string(content_filter, "label", index, prefix="Content filter"),
        query=_required_string(content_filter, "query", index, prefix="Content filter"),
        penalty=_required_int(
            content_filter, "penalty", index, prefix="Content filter"
        ),
        max_results=_optional_positive_int(
            content_filter,
            "max_results",
            index,
            100,
            prefix="Content filter",
        ),
        min_search_pool=_optional_positive_int(
            content_filter,
            "min_search_pool",
            index,
            500,
            prefix="Content filter",
        ),
    )


def _required_string(
    album: dict,
    key: str,
    index: int,
    prefix: str = "Album config entry",
) -> str:
    """Read a required non-empty string from one album config entry."""
    value = album.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix} #{index} must set non-empty {key!r}")
    return value.strip()


def _enabled(album: dict, index: int, prefix: str = "Album config entry") -> bool:
    """Read the optional enabled flag without accepting string booleans."""
    value = album.get("enabled", True)
    if not isinstance(value, bool):
        raise ValueError(f"{prefix} #{index} field 'enabled' must be true or false")
    return value


def _required_positive_int(album: dict, key: str, index: int) -> int:
    """Read a required positive integer from one album config entry."""
    if key not in album:
        raise ValueError(f"Album config entry #{index} must set {key!r}")
    return _positive_int(album[key], key, index)


def _required_int(
    album: dict,
    key: str,
    index: int,
    prefix: str = "Album config entry",
) -> int:
    """Read a required integer from one config entry."""
    if key not in album:
        raise ValueError(f"{prefix} #{index} must set {key!r}")
    value = album[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{prefix} #{index} field {key!r} must be an integer")
    return value


def _optional_positive_int(
    album: dict,
    key: str,
    index: int,
    default: int,
    prefix: str = "Album config entry",
) -> int:
    """Read an optional positive integer from one album config entry."""
    if key not in album:
        return default
    return _positive_int(album[key], key, index, prefix=prefix)


def _positive_int(
    value,
    key: str,
    index: int,
    prefix: str = "Album config entry",
) -> int:
    """Validate positive integer fields without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{prefix} #{index} field {key!r} must be a positive integer")
    return value
