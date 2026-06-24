import os

IMMICH_API_URL = os.getenv("IMMICH_API_URL", "http://localhost:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")
SCORER_DRY_RUN = os.getenv("SCORER_DRY_RUN", "true").lower() in (
    "1",
    "true",
    "yes",
)
SCORER_DB_PATH = os.getenv("SCORER_DB_PATH", "./db/scorer.db")
SCORER_SCAN_INTERVAL_HOURS = int(
    os.getenv("SCORER_SCAN_INTERVAL_HOURS", "24")
)
TEMP_DIR = os.getenv("SCORER_TEMP_DIR", "/tmp/scorer")
LOG_LEVEL = os.getenv("SCORER_LOG_LEVEL", "INFO")
