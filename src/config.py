import os
from dotenv import load_dotenv

# Keep local development simple: environment variables still win, but a nearby
# .env file can provide defaults without requiring shell setup.
load_dotenv()

IMMICH_API_URL = os.getenv("IMMICH_API_URL", "http://localhost:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")

SCORER_DB_PATH = os.getenv("SCORER_DB_PATH", "./db/scorer.db")

SCORER_DRY_RUN = os.getenv("SCORER_DRY_RUN", "true")
LOG_LEVEL = os.getenv("SCORER_LOG_LEVEL", "INFO")

# Runtime limits protect scheduled runs from scanning a large library forever.
SCORER_SCAN_INTERVAL_HOURS = int(os.getenv("SCORER_SCAN_INTERVAL_HOURS", "24"))
SCORER_MAX_ASSETS = int(os.getenv("SCORER_MAX_ASSETS", "100"))
TEMP_DIR = os.getenv("SCORER_TEMP_DIR", "/tmp/scorer")
