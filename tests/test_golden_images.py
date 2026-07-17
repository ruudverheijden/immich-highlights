"""Golden-image evaluation tests for human-scored fixture photos."""

from pathlib import Path

from tools.evaluate_golden_images import evaluate_manifest, render_report


MANIFEST = Path("tests/golden_images/manifest.toml")


def test_golden_images_match_human_scoring_preferences():
    """The scoring pipeline should rank the fixture set like the manifest."""
    evaluation = evaluate_manifest(MANIFEST)

    assert evaluation.passed, render_report(evaluation)
    assert evaluation.top_accept_recall >= evaluation.min_top_accept_recall
    assert evaluation.reject_leak_count <= evaluation.max_reject_leak_count
