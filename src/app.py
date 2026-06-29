import argparse
import logging

try:
    from .config import LOG_LEVEL
    from .pipeline import PipelineOptions, run_pipeline
except ImportError:
    from config import LOG_LEVEL
    from pipeline import PipelineOptions, run_pipeline


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
    """Run one photo curation pipeline pass from the CLI."""
    return run_pipeline(PipelineOptions(force_rescore=force_rescore))


if __name__ == "__main__":
    args = parse_args()
    run_once(force_rescore=args.force_rescore)
