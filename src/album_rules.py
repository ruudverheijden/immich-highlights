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

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    albums = data.get("albums")
    if not isinstance(albums, list):
        raise ValueError("Album config must contain an [[albums]] list")

    rules = [
        _album_rule_from_config(album, index, now, default_max_candidates)
        for index, album in enumerate(albums, start=1)
        if _enabled(album, index)
    ]
    if not rules:
        raise ValueError("Album config must enable at least one album")
    return rules


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


def _required_string(album: dict, key: str, index: int) -> str:
    """Read a required non-empty string from one album config entry."""
    value = album.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Album config entry #{index} must set non-empty {key!r}")
    return value.strip()


def _enabled(album: dict, index: int) -> bool:
    """Read the optional enabled flag without accepting string booleans."""
    value = album.get("enabled", True)
    if not isinstance(value, bool):
        raise ValueError(
            f"Album config entry #{index} field 'enabled' must be true or false"
        )
    return value


def _required_positive_int(album: dict, key: str, index: int) -> int:
    """Read a required positive integer from one album config entry."""
    if key not in album:
        raise ValueError(f"Album config entry #{index} must set {key!r}")
    return _positive_int(album[key], key, index)


def _optional_positive_int(album: dict, key: str, index: int, default: int) -> int:
    """Read an optional positive integer from one album config entry."""
    if key not in album:
        return default
    return _positive_int(album[key], key, index)


def _positive_int(value, key: str, index: int) -> int:
    """Validate positive integer fields without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(
            f"Album config entry #{index} field {key!r} must be a positive integer"
        )
    return value
