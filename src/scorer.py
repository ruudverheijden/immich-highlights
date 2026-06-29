import argparse
import logging
import requests

try:
    from .config import (
        IMMICH_API_URL,
        IMMICH_API_KEY,
        SCORER_DRY_RUN,
        SCORER_DB_PATH,
        SCORER_MAX_ASSETS,
        TEMP_DIR,
        LOG_LEVEL,
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        SCORING_CONFIG_PATH,
    )
    from .immich_client import ImmichClient
    from .db import init_db
    from .album_manager import AlbumManager
    from .album_generator import generate_albums
    from .album_rules import load_album_config
    from .scoring_engine import load_scoring_config
except ImportError:
    from config import (
        IMMICH_API_URL,
        IMMICH_API_KEY,
        SCORER_DRY_RUN,
        SCORER_DB_PATH,
        SCORER_MAX_ASSETS,
        TEMP_DIR,
        LOG_LEVEL,
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        SCORING_CONFIG_PATH,
    )
    from immich_client import ImmichClient
    from db import init_db
    from album_manager import AlbumManager
    from album_generator import generate_albums
    from album_rules import load_album_config
    from scoring_engine import load_scoring_config


logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("scorer")


def parse_args(argv=None):
    """Parse command-line options for one scorer run."""
    parser = argparse.ArgumentParser(
        allow_abbrev=False,
        description="Generate Immich highlight albums from scored photo candidates.",
    )
    parser.add_argument(
        "--force-rescore",
        action="store_true",
        help=(
            "Ignore cached asset scores and re-download/re-analyze current "
            "candidates. Generated album mappings are kept."
        ),
    )
    return parser.parse_args(argv)


def run_once(force_rescore: bool = False):
    """Generate rolling highlight albums from Immich search queries."""
    logger.info(
        "Starting scorer run: immich_url=%s, dry_run=%s, max_assets=%s, "
        "force_rescore=%s",
        IMMICH_API_URL,
        SCORER_DRY_RUN,
        SCORER_MAX_ASSETS,
        force_rescore,
    )
    conn = init_db(SCORER_DB_PATH)
    client = ImmichClient(
        IMMICH_API_URL,
        IMMICH_API_KEY,
        dry_run=SCORER_DRY_RUN,
    )
    try:
        perms = client.verify_permissions()
        # Asset reads and statistics are required for scoring and content-filter
        # search-window sizing; other probes are advisory diagnostics.
        critical = ["asset.read", "asset.statistics"]
        # Keep startup logs compact by aggregating only probes that did not
        # return a successful HTTP status.
        failed_permissions = {
            permission: detail
            for permission, (ok, detail) in perms.items()
            if ok is False
        }
        if failed_permissions:
            message = ", ".join(
                f"{permission} -> {detail}"
                for permission, detail in failed_permissions.items()
            )
            if any(permission in critical for permission in failed_permissions):
                logger.warning("Permission checks failed: %s", message)
            else:
                logger.info("Permission checks failed: %s", message)
    except Exception as e:
        logger.warning("Permission verification failed: %s", e)
    alb_mgr = AlbumManager(client, conn)

    rules, content_filters = load_album_config(
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        default_max_candidates=SCORER_MAX_ASSETS,
    )
    scoring_config = load_scoring_config(SCORING_CONFIG_PATH)
    logger.info(
        "Loaded config: album_config=%s, content_filter_config=%s, scoring_config=%s, "
        "albums=%s, content_filters=%s",
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        SCORING_CONFIG_PATH,
        len(rules),
        len(content_filters),
    )
    if not content_filters:
        logger.info(
            "No content filters configured; smart-search penalties will not be applied"
        )
    try:
        generate_albums(
            client,
            conn,
            alb_mgr,
            rules,
            TEMP_DIR,
            IMMICH_API_URL,
            content_filters=content_filters,
            scoring_config=scoring_config,
            force_rescore=force_rescore,
        )
    except requests.RequestException as e:
        logger.error(
            "Unable to generate highlight albums from %s: %s",
            IMMICH_API_URL,
            e,
        )
        return


if __name__ == "__main__":
    args = parse_args()
    run_once(force_rescore=args.force_rescore)
