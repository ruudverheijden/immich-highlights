from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


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
