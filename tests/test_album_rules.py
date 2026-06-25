from datetime import datetime, timezone

from src.album_rules import build_time_album_rules


def test_build_time_album_rules_creates_rolling_windows():
    """Default album rules should query Immich before any scoring work starts."""
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)

    rules = build_time_album_rules(now=now, max_candidates=250, album_limit=20)

    assert [rule.name for rule in rules] == [
        "Highlights: Last Week",
        "Highlights: Last Month",
        "Highlights: Last Year",
    ]
    assert [rule.bucket for rule in rules] == ["last-week", "last-month", "last-year"]
    assert rules[0].taken_after_iso() == "2026-06-18T12:00:00+00:00"
    assert rules[1].taken_after_iso() == "2026-05-26T12:00:00+00:00"
    assert rules[2].taken_after_iso() == "2025-06-25T12:00:00+00:00"
    assert all(rule.taken_before_iso() == "2026-06-25T12:00:00+00:00" for rule in rules)
    assert all(rule.max_candidates == 250 for rule in rules)
    assert all(rule.limit == 20 for rule in rules)
