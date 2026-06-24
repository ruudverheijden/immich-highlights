import pytest

from src.config import (
    parse_base_url_env,
    parse_bool_env,
    parse_log_level_env,
    parse_non_empty_env,
    parse_positive_int_env,
)


def test_parse_bool_env_accepts_true_and_false(monkeypatch):
    """Boolean env vars are intentionally strict for readability."""
    monkeypatch.setenv("TEST_BOOL", "true")
    assert parse_bool_env("TEST_BOOL") is True

    monkeypatch.setenv("TEST_BOOL", "false")
    assert parse_bool_env("TEST_BOOL") is False


def test_parse_bool_env_rejects_other_values(monkeypatch):
    """Typos should fail loudly instead of changing write behavior silently."""
    monkeypatch.setenv("TEST_BOOL", "yes")

    with pytest.raises(ValueError, match="TEST_BOOL must be"):
        parse_bool_env("TEST_BOOL")


def test_parse_base_url_env_accepts_http_url_without_api(monkeypatch):
    monkeypatch.setenv("TEST_URL", "http://immich.local:2283/")

    assert parse_base_url_env("TEST_URL", "http://localhost:2283") == (
        "http://immich.local:2283"
    )


def test_parse_base_url_env_rejects_api_suffix(monkeypatch):
    monkeypatch.setenv("TEST_URL", "http://immich.local:2283/api")

    with pytest.raises(ValueError, match="without /api"):
        parse_base_url_env("TEST_URL", "http://localhost:2283")


def test_parse_positive_int_env_validates_bounds(monkeypatch):
    monkeypatch.setenv("TEST_INT", "10")
    assert parse_positive_int_env("TEST_INT", "1", max_value=20) == 10

    monkeypatch.setenv("TEST_INT", "0")
    with pytest.raises(ValueError, match="positive integer"):
        parse_positive_int_env("TEST_INT", "1")

    monkeypatch.setenv("TEST_INT", "21")
    with pytest.raises(ValueError, match="<= 20"):
        parse_positive_int_env("TEST_INT", "1", max_value=20)


def test_parse_non_empty_env_rejects_blank_values(monkeypatch):
    monkeypatch.setenv("TEST_STRING", "  ")

    with pytest.raises(ValueError, match="must not be empty"):
        parse_non_empty_env("TEST_STRING", "fallback")


def test_parse_log_level_env_accepts_known_levels(monkeypatch):
    monkeypatch.setenv("TEST_LOG_LEVEL", "warning")
    assert parse_log_level_env("TEST_LOG_LEVEL") == "WARNING"

    monkeypatch.setenv("TEST_LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="TEST_LOG_LEVEL must be one of"):
        parse_log_level_env("TEST_LOG_LEVEL")
