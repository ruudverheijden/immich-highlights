import os
from urllib.parse import urlparse

from dotenv import load_dotenv

# Keep local development simple: environment variables still win, but a nearby
# .env file can provide defaults without requiring shell setup.
load_dotenv()


def parse_bool_env(name: str, default: str = "false") -> bool:
    """Parse boolean env vars that must be exactly true or false."""
    value = os.getenv(name, default).strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"{name} must be 'true' or 'false', got {value!r}")
    return value == "true"


def parse_base_url_env(name: str, default: str) -> str:
    """Parse an http(s) base URL without the Immich /api suffix."""
    value = os.getenv(name, default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"{name} must be an http(s) URL, got {value!r}")
    if parsed.path.rstrip("/") == "/api":
        raise ValueError(f"{name} must be the base URL without /api")
    return value


def parse_non_empty_env(name: str, default: str) -> str:
    """Parse a string env var that may not be empty."""
    value = os.getenv(name, default).strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def parse_positive_int_env(
    name: str, default: str, max_value: int | None = None
) -> int:
    """Parse a positive integer env var with an optional upper bound."""
    raw_value = os.getenv(name, default).strip()
    try:
        value = int(raw_value)
    except ValueError as e:
        raise ValueError(f"{name} must be a positive integer, got {raw_value!r}") from e
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value}")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name} must be <= {max_value}, got {value}")
    return value


def parse_log_level_env(name: str, default: str = "INFO") -> str:
    """Parse Python logging levels accepted by logging.basicConfig."""
    value = os.getenv(name, default).strip().upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if value not in valid_levels:
        raise ValueError(f"{name} must be one of {sorted(valid_levels)}, got {value!r}")
    return value


# Browser/base URL only; ImmichClient appends /api for HTTP calls.
IMMICH_API_URL = parse_base_url_env("IMMICH_API_URL", "http://localhost:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "").strip()

SCORER_DB_PATH = parse_non_empty_env("SCORER_DB_PATH", "./db/scorer.db")
SCORER_DRY_RUN = parse_bool_env("SCORER_DRY_RUN", "true")
LOG_LEVEL = parse_log_level_env("SCORER_LOG_LEVEL", "INFO")
SCORER_SCAN_INTERVAL_HOURS = parse_positive_int_env("SCORER_SCAN_INTERVAL_HOURS", "24")
SCORER_MAX_ASSETS = parse_positive_int_env("SCORER_MAX_ASSETS", "100", max_value=1000)
TEMP_DIR = parse_non_empty_env("SCORER_TEMP_DIR", "/tmp/scorer")
SCORER_BUCKET = parse_non_empty_env("SCORER_BUCKET", "MVP")
ALBUM_CONFIG_PATH = parse_non_empty_env("SCORER_ALBUM_CONFIG_PATH", "./albums.toml")
CONTENT_FILTER_CONFIG_PATH = parse_non_empty_env(
    "SCORER_CONTENT_FILTER_CONFIG_PATH",
    "./content_filters.toml",
)
