from datetime import datetime, timezone

import pytest

from src.album_rules import (
    build_default_content_filters,
    build_time_album_rules,
    load_album_config,
    load_album_rules,
)


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


def test_load_album_rules_reads_custom_toml_config(tmp_path):
    """Users should be able to change generated albums without code changes."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        """
[[albums]]
name = "Highlights: Weekend"
bucket = "weekend"
window_days = 3
limit = 8
max_candidates = 40
enabled = true

[[content_filters]]
label = "screenshot"
query = "screenshot"
penalty = -40
max_results = 25
min_search_pool = 300
enabled = true

[[content_filters]]
label = "disabled"
query = "disabled"
penalty = -5
enabled = false

[[albums]]
name = "Disabled"
bucket = "disabled"
window_days = 10
limit = 5
enabled = false
""".strip()
    )
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)

    rules = load_album_rules(str(config_path), now=now, default_max_candidates=100)
    rules_from_config, content_filters = load_album_config(
        str(config_path),
        now=now,
        default_max_candidates=100,
    )

    assert len(rules) == 1
    assert rules_from_config == rules
    assert rules[0].name == "Highlights: Weekend"
    assert rules[0].bucket == "weekend"
    assert rules[0].taken_after_iso() == "2026-06-22T12:00:00+00:00"
    assert rules[0].taken_before_iso() == "2026-06-25T12:00:00+00:00"
    assert rules[0].limit == 8
    assert rules[0].max_candidates == 40
    assert len(content_filters) == 1
    assert content_filters[0].label == "screenshot"
    assert content_filters[0].query == "screenshot"
    assert content_filters[0].penalty == -40
    assert content_filters[0].max_results == 25
    assert content_filters[0].min_search_pool == 300


def test_load_album_rules_uses_env_default_max_candidates(tmp_path):
    """Albums can omit max_candidates and inherit the runtime default."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        """
[[albums]]
name = "Highlights: Recent"
bucket = "recent"
window_days = 14
limit = 12
""".strip()
    )

    rules = load_album_rules(str(config_path), default_max_candidates=77)

    assert rules[0].max_candidates == 77


def test_load_album_config_without_content_filters_disables_filters(tmp_path):
    """A user-provided config fully replaces default content filters."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        """
[[albums]]
name = "Highlights: Recent"
bucket = "recent"
window_days = 14
limit = 12
""".strip()
    )

    _rules, content_filters = load_album_config(str(config_path))

    assert content_filters == []


def test_load_album_rules_falls_back_to_defaults_when_file_is_missing(tmp_path):
    """Existing installs should keep working until a config file is mounted."""
    now = datetime(2026, 6, 25, 12, 0, tzinfo=timezone.utc)

    rules, content_filters = load_album_config(
        str(tmp_path / "missing.toml"),
        now=now,
        default_max_candidates=42,
    )

    assert [rule.bucket for rule in rules] == ["last-week", "last-month", "last-year"]
    assert all(rule.max_candidates == 42 for rule in rules)
    assert content_filters == build_default_content_filters()


def test_load_album_rules_rejects_invalid_values(tmp_path):
    """Bad album config should fail loudly at startup."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        """
[[albums]]
name = "Broken"
bucket = "broken"
window_days = "7"
limit = 15
enabled = "true"
""".strip()
    )

    with pytest.raises(ValueError, match="enabled"):
        load_album_rules(str(config_path))


def test_load_album_config_rejects_invalid_content_filter(tmp_path):
    """Bad content filter config should fail loudly at startup."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        """
[[albums]]
name = "Highlights"
bucket = "highlights"
window_days = 7
limit = 15

[[content_filters]]
label = "screenshot"
query = "screenshot"
penalty = "bad"
""".strip()
    )

    with pytest.raises(ValueError, match="penalty"):
        load_album_config(str(config_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_results", "0"),
        ("min_search_pool", "false"),
        ("enabled", '"true"'),
    ],
)
def test_load_album_config_rejects_invalid_optional_content_filter_fields(
    tmp_path,
    field,
    value,
):
    """Optional content-filter fields should fail loudly when mistyped."""
    config_path = tmp_path / "albums.toml"
    config_path.write_text(
        f"""
[[albums]]
name = "Highlights"
bucket = "highlights"
window_days = 7
limit = 15

[[content_filters]]
label = "screenshot"
query = "screenshot"
penalty = -40
{field} = {value}
""".strip()
    )

    with pytest.raises(ValueError, match=field):
        load_album_config(str(config_path))
