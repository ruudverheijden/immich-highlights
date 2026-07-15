"""Evaluate scoring quality against a predefined image benchmark.

The golden-image manifest is intended for human-tuned regression testing: each
image has a personal score and an expected tier. The evaluator runs local
analysis and scoring without requiring Immich.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import statistics
import sys
import tomllib

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.duplicate_detection import duplicate_groups_from_phashes  # noqa: E402
from src.scoring_engine import (  # noqa: E402
    DEFAULT_SCORING_CONFIG,
    ScoringConfig,
    calculate_score_details,
    load_scoring_config,
)
from src.selection import select_top_scored_assets  # noqa: E402
from src.semantic_analysis import analyze_semantic_metadata  # noqa: E402
from src.technical_analysis import analyze_technical_image  # noqa: E402


@dataclass(frozen=True)
class GoldenImage:
    """One human-scored benchmark image from the manifest."""

    asset_id: str
    path: Path
    personal_score: float
    expected_tier: str
    tags: tuple[str, ...]
    metadata: dict
    faces: list[dict]
    content_filter_matches: list[dict]
    content_filter_penalty: int


@dataclass(frozen=True)
class GoldenResult:
    """Scoring result for one golden image."""

    image: GoldenImage
    app_score: int
    raw_score: int
    components: dict
    inputs: dict


@dataclass(frozen=True)
class GoldenEvaluation:
    """Aggregate benchmark output used by tests and the CLI report."""

    results: list[GoldenResult]
    selected_ids: list[str]
    duplicate_groups: list[dict]
    spearman_correlation: float
    top_keeper_recall: float
    reject_leak_count: int
    min_top_keeper_recall: float
    max_reject_leak_count: int

    @property
    def passed(self) -> bool:
        """Return True when all quality gates pass."""
        return (
            self.top_keeper_recall >= self.min_top_keeper_recall
            and self.reject_leak_count <= self.max_reject_leak_count
        )


def load_manifest(path: str | Path) -> tuple[dict, list[GoldenImage]]:
    """Load a golden-image manifest from TOML."""
    manifest_path = Path(path)
    with manifest_path.open("rb") as f:
        data = tomllib.load(f)

    settings = data.get("settings", {})
    images = [
        _load_manifest_image(manifest_path, item, index)
        for index, item in enumerate(data.get("image", []), start=1)
    ]
    if not images:
        raise ValueError("Golden-image manifest must contain at least one [[image]]")
    return settings, images


def _load_manifest_image(
    manifest_path: Path,
    item: dict,
    index: int,
) -> GoldenImage:
    """Normalize one manifest image entry."""
    if "file" not in item:
        raise ValueError(f"Manifest image #{index} is missing 'file'")
    if "personal_score" not in item:
        raise ValueError(f"Manifest image #{index} is missing 'personal_score'")

    relative_path = Path(str(item["file"]))
    path = manifest_path.parent / relative_path
    asset_id = str(item.get("id") or relative_path.stem)
    metadata = dict(item.get("metadata", {}))
    metadata.setdefault("id", asset_id)
    if "exif" not in metadata and "exif" in item:
        metadata["exif"] = item["exif"]

    return GoldenImage(
        asset_id=asset_id,
        path=path,
        personal_score=float(item["personal_score"]),
        expected_tier=str(item.get("expected_tier", "neutral")),
        tags=tuple(str(tag) for tag in item.get("tags", [])),
        metadata=metadata,
        faces=list(item.get("faces", [])),
        content_filter_matches=list(item.get("content_filter_matches", [])),
        content_filter_penalty=int(item.get("content_filter_penalty", 0)),
    )


def score_golden_image(
    image: GoldenImage,
    scoring_config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> GoldenResult:
    """Run local technical, semantic, and scoring stages for one image."""
    with Image.open(image.path) as opened:
        pil_image = opened.convert("RGB")

    technical = analyze_technical_image(pil_image, image.faces)
    semantic = analyze_semantic_metadata(
        image.metadata,
        pil_image,
        immich_faces=image.faces,
        content_filter_matches=image.content_filter_matches,
        content_filter_penalty=image.content_filter_penalty,
        person_detector=lambda _image, **_kwargs: [],
    )
    details = {**technical, **semantic}
    score_details = calculate_score_details(details, scoring_config)
    return GoldenResult(
        image=image,
        app_score=score_details["score"],
        raw_score=score_details["raw_score"],
        components=score_details["components"],
        inputs=score_details["inputs"],
    )


def evaluate_manifest(
    manifest_path: str | Path,
    scoring_config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> GoldenEvaluation:
    """Evaluate all images in a manifest and calculate benchmark metrics."""
    settings, images = load_manifest(manifest_path)
    results = [score_golden_image(image, scoring_config) for image in images]
    scored_assets = [(result.image.asset_id, result.app_score) for result in results]
    non_reject_count = sum(
        1 for result in results if result.image.expected_tier != "reject"
    )
    configured_limit = int(settings.get("selection_limit", min(5, len(results))))
    limit = min(configured_limit, non_reject_count or len(results))
    selected_ids = select_top_scored_assets(list(scored_assets), limit)

    phashes = {
        result.image.asset_id: result.inputs["phash"]
        for result in results
        if result.inputs.get("phash")
    }
    duplicate_groups = duplicate_groups_from_phashes(
        scored_assets,
        phashes,
        threshold=int(settings.get("duplicate_phash_distance_threshold", 6)),
        album_bucket=str(settings.get("album_bucket", "golden-images")),
    )

    selected_set = set(selected_ids)
    keeper_ids = {
        result.image.asset_id
        for result in results
        if result.image.expected_tier == "keeper"
    }
    reject_ids = {
        result.image.asset_id
        for result in results
        if result.image.expected_tier == "reject"
    }

    top_keeper_recall = (
        len(keeper_ids & selected_set) / len(keeper_ids) if keeper_ids else 1.0
    )

    return GoldenEvaluation(
        results=results,
        selected_ids=selected_ids,
        duplicate_groups=duplicate_groups,
        spearman_correlation=spearman_correlation(
            [result.image.personal_score for result in results],
            [result.app_score for result in results],
        ),
        top_keeper_recall=top_keeper_recall,
        reject_leak_count=len(reject_ids & selected_set),
        min_top_keeper_recall=float(settings.get("min_top_keeper_recall", 0.75)),
        max_reject_leak_count=int(settings.get("max_reject_leak_count", 0)),
    )


def spearman_correlation(left: list[float], right: list[float]) -> float:
    """Return Spearman rank correlation for two equal-length numeric lists."""
    if len(left) != len(right):
        raise ValueError("Spearman inputs must have equal lengths")
    if len(left) < 2:
        return 1.0

    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = statistics.fmean(left_ranks)
    right_mean = statistics.fmean(right_ranks)
    numerator = sum(
        (left_rank - left_mean) * (right_rank - right_mean)
        for left_rank, right_rank in zip(left_ranks, right_ranks)
    )
    left_denominator = sum((rank - left_mean) ** 2 for rank in left_ranks)
    right_denominator = sum((rank - right_mean) ** 2 for rank in right_ranks)
    denominator = (left_denominator * right_denominator) ** 0.5
    if denominator == 0:
        return 1.0 if left_ranks == right_ranks else 0.0
    return numerator / denominator


def _average_ranks(values: list[float]) -> list[float]:
    """Return ascending average ranks, handling ties deterministically."""
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2
        for original_index, _value in ordered[index:end]:
            ranks[original_index] = average_rank
        index = end
    return ranks


def render_report(evaluation: GoldenEvaluation) -> str:
    """Render a human-readable evaluation report."""
    lines = [
        "Golden image evaluation",
        f"passed: {evaluation.passed}",
        f"spearman_correlation: {evaluation.spearman_correlation:.3f} (report only)",
        (
            f"top_keeper_recall: {evaluation.top_keeper_recall:.3f} "
            f"(min {evaluation.min_top_keeper_recall:.3f})"
        ),
        (
            f"reject_leak_count: {evaluation.reject_leak_count} "
            f"(max {evaluation.max_reject_leak_count})"
        ),
        f"selected_ids: {', '.join(evaluation.selected_ids)}",
        "",
        "ranked images:",
    ]
    ranked = sorted(
        evaluation.results,
        key=lambda result: result.app_score,
        reverse=True,
    )
    for result in ranked:
        selected = "*" if result.image.asset_id in evaluation.selected_ids else " "
        lines.append(
            f"{selected} {result.image.asset_id:18} "
            f"app={result.app_score:3d} "
            f"human={result.image.personal_score:5.1f} "
            f"tier={result.image.expected_tier}"
        )
    if evaluation.duplicate_groups:
        lines.extend(["", "duplicate groups:"])
        for group in evaluation.duplicate_groups:
            members = ", ".join(
                member["asset_id"] for member in group.get("members", [])
            )
            lines.append(
                f"- {group['group_id']} representative="
                f"{group['representative_asset_id']} members={members}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the golden-image evaluator from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="Path to a golden-image manifest TOML")
    parser.add_argument(
        "--scoring-config",
        help="Optional scoring.toml override to evaluate",
    )
    args = parser.parse_args(argv)

    scoring_config = (
        load_scoring_config(args.scoring_config)
        if args.scoring_config
        else DEFAULT_SCORING_CONFIG
    )
    evaluation = evaluate_manifest(args.manifest, scoring_config)
    print(render_report(evaluation))
    return 0 if evaluation.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
