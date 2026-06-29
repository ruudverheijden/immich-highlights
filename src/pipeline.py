import logging
from dataclasses import dataclass

import requests

try:
    from .album_generator import generate_albums
    from .album_manager import AlbumManager
    from .album_rules import load_album_config
    from .config import (
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        IMMICH_API_KEY,
        IMMICH_API_URL,
        SCORER_DB_PATH,
        SCORER_DRY_RUN,
        SCORER_MAX_ASSETS,
        SCORING_CONFIG_PATH,
        TEMP_DIR,
    )
    from .db import init_db
    from .immich_client import ImmichClient
    from .scoring_engine import load_scoring_config
except ImportError:
    from album_generator import generate_albums
    from album_manager import AlbumManager
    from album_rules import load_album_config
    from config import (
        ALBUM_CONFIG_PATH,
        CONTENT_FILTER_CONFIG_PATH,
        IMMICH_API_KEY,
        IMMICH_API_URL,
        SCORER_DB_PATH,
        SCORER_DRY_RUN,
        SCORER_MAX_ASSETS,
        SCORING_CONFIG_PATH,
        TEMP_DIR,
    )
    from db import init_db
    from immich_client import ImmichClient
    from scoring_engine import load_scoring_config


logger = logging.getLogger("pipeline")


@dataclass(frozen=True)
class PipelineOptions:
    """Runtime options that control one pipeline execution."""

    force_rescore: bool = False


@dataclass(frozen=True)
class PipelineConfig:
    """Resolved user configuration consumed by downstream pipeline stages."""

    album_rules: list
    content_filters: list
    scoring_config: object


@dataclass(frozen=True)
class PipelineContext:
    """Shared services created once and passed through the pipeline."""

    client: object
    conn: object
    album_manager: object


def create_pipeline_context() -> PipelineContext:
    """Create shared API/database clients for one pipeline run."""
    conn = init_db(SCORER_DB_PATH)
    client = ImmichClient(
        IMMICH_API_URL,
        IMMICH_API_KEY,
        dry_run=SCORER_DRY_RUN,
    )
    return PipelineContext(
        client=client,
        conn=conn,
        album_manager=AlbumManager(client, conn),
    )


def verify_permissions(client) -> None:
    """Run advisory Immich permission checks before expensive work starts."""
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


def load_pipeline_config() -> PipelineConfig:
    """Load album, content-filter, and scoring configuration."""
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
    return PipelineConfig(
        album_rules=rules,
        content_filters=content_filters,
        scoring_config=scoring_config,
    )


def run_album_generation_stage(
    context: PipelineContext,
    config: PipelineConfig,
    options: PipelineOptions,
):
    """Run the current combined curation and album-sync stage."""
    return generate_albums(
        context.client,
        context.conn,
        context.album_manager,
        config.album_rules,
        TEMP_DIR,
        IMMICH_API_URL,
        content_filters=config.content_filters,
        scoring_config=config.scoring_config,
        force_rescore=options.force_rescore,
    )


def run_pipeline(options: PipelineOptions | None = None):
    """Run one end-to-end photo curation pipeline pass."""
    options = options or PipelineOptions()
    logger.info(
        "Starting pipeline run: immich_url=%s, dry_run=%s, max_assets=%s, "
        "force_rescore=%s",
        IMMICH_API_URL,
        SCORER_DRY_RUN,
        SCORER_MAX_ASSETS,
        options.force_rescore,
    )
    context = create_pipeline_context()
    verify_permissions(context.client)
    config = load_pipeline_config()
    try:
        return run_album_generation_stage(context, config, options)
    except requests.RequestException as e:
        logger.error(
            "Unable to generate highlight albums from %s: %s",
            IMMICH_API_URL,
            e,
        )
        return None
