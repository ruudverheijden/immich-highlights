from dataclasses import asdict, dataclass, replace
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class ScoringConfig:
    """Stable scoring weights and thresholds that can be tuned by users."""

    base_score: int = 50
    blur_low_threshold: float = 50
    blur_low_penalty: int = -20
    blur_high_threshold: float = 200
    blur_high_bonus: int = 10
    small_image_min_side: int = 640
    small_image_penalty: int = -15
    large_image_max_side: int = 3000
    large_image_bonus: int = 5
    face_present_bonus: int = 10
    max_face_quality_bonus: int = 25
    rating_step: int = 15
    high_iso_threshold: int = 3200
    high_iso_penalty: int = -5
    long_exposure_threshold_seconds: float = 1 / 30
    long_exposure_penalty: int = -5
    location_bonus: int = 3
    favorite_bonus: int = 20
    edited_bonus: int = 10
    low_contrast_threshold: float = 30
    low_contrast_penalty: int = -5
    high_contrast_threshold: float = 80
    high_contrast_bonus: int = 3
    dark_brightness_threshold: float = 35
    bright_brightness_threshold: float = 225
    brightness_penalty: int = -10
    max_portrait_quality_bonus: int = 15
    content_filter_min_penalty: int = -50
    duplicate_detection_enabled: bool = True
    duplicate_phash_distance_threshold: int = 6
    timestamp_duplicate_detection_enabled: bool = True
    timestamp_duplicate_window_seconds: int = 2
    timestamp_duplicate_phash_threshold: int = 10


DEFAULT_SCORING_CONFIG = ScoringConfig()


def load_scoring_config(path: str | None = None) -> ScoringConfig:
    """Load optional scoring overrides from TOML, falling back to defaults."""
    if not path or not Path(path).exists():
        return DEFAULT_SCORING_CONFIG

    data = _load_toml(path)
    allowed_sections = {
        "weights",
        "technical_quality",
        "content_filters",
        "duplicate_detection",
    }
    unknown_sections = set(data) - allowed_sections
    if unknown_sections:
        raise ValueError(
            f"Unknown scoring config section {sorted(unknown_sections)[0]!r}"
        )

    values = asdict(DEFAULT_SCORING_CONFIG)
    for section_name in allowed_sections:
        section = data.get(section_name, {})
        if not isinstance(section, dict):
            raise ValueError(f"Scoring config section {section_name!r} must be a table")
        for key, value in section.items():
            if key not in values:
                raise ValueError(f"Unknown scoring config field {section_name}.{key}")
            values[key] = _typed_value(
                value,
                values[key],
                f"{section_name}.{key}",
            )
    return replace(DEFAULT_SCORING_CONFIG, **values)


def _load_toml(path: str) -> dict:
    """Read a TOML file into a dictionary."""
    with Path(path).open("rb") as f:
        return tomllib.load(f)


def _number(value, field: str):
    """Validate numeric scoring values without accepting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Scoring config field {field} must be a number")
    return value


def _typed_value(value, default_value, field: str):
    """Validate a config value against the matching default field type."""
    if isinstance(default_value, bool):
        if not isinstance(value, bool):
            raise ValueError(f"Scoring config field {field} must be a boolean")
        return value
    return _number(value, field)


def score_blur(blur: float, config: ScoringConfig = DEFAULT_SCORING_CONFIG) -> int:
    """Score image sharpness from a precomputed blur variance."""
    if blur < config.blur_low_threshold:
        return config.blur_low_penalty
    if blur > config.blur_high_threshold:
        return config.blur_high_bonus
    return 0


def score_dimensions(
    width: int,
    height: int,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward large images and penalize very small assets."""
    score = 0
    # Very small images are often screenshots, thumbnails, or received media.
    if min(width, height) < config.small_image_min_side:
        score += config.small_image_penalty
    # Large originals tend to contain more usable detail for highlights.
    if max(width, height) > config.large_image_max_side:
        score += config.large_image_bonus
    return score


def score_faces(
    face_count: int,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward photos with at least one detected face."""
    if face_count > 0:
        return config.face_present_bonus
    return 0


def score_face_quality(
    face_quality: int,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward the best detected face without letting it dominate the score."""
    return max(0, min(config.max_face_quality_bonus, int(face_quality)))


def score_rating(rating, config: ScoringConfig = DEFAULT_SCORING_CONFIG) -> int:
    """Convert Immich's 1-5 star user rating into a score adjustment."""
    if rating is None:
        return 0
    # Treat 3 as neutral so explicit preference nudges, not dominates.
    return (rating - 3) * config.rating_step


def score_exif_quality(
    iso,
    exposure_seconds,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Penalize EXIF signals that often correlate with lower image quality."""
    score = 0
    # Very high ISO often correlates with noisy low-light photos.
    if iso and isinstance(iso, (int, float)) and iso > config.high_iso_threshold:
        score += config.high_iso_penalty

    if (
        exposure_seconds is not None
        and exposure_seconds > config.long_exposure_threshold_seconds
    ):
        score += config.long_exposure_penalty
    return score


def score_location(
    has_location: bool,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward location metadata because it helps create meaningful albums."""
    if has_location:
        return config.location_bonus
    return 0


def score_user_flags(
    is_favorite: bool,
    is_edited: bool,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward explicit user actions such as favorites and edits."""
    score = 0
    if is_favorite:
        score += config.favorite_bonus
    if is_edited:
        score += config.edited_bonus
    return score


def score_contrast(
    stddev: float,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward high contrast and lightly penalize very flat images."""
    if stddev < config.low_contrast_threshold:
        return config.low_contrast_penalty
    if stddev > config.high_contrast_threshold:
        return config.high_contrast_bonus
    return 0


def score_brightness(
    brightness: float,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Penalize images that are likely underexposed or overexposed."""
    if brightness < config.dark_brightness_threshold:
        return config.brightness_penalty
    if brightness > config.bright_brightness_threshold:
        return config.brightness_penalty
    return 0


def score_portrait_quality(
    portrait_quality: int,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Reward sharp-subject/soft-background photos without dominating the score."""
    return max(0, min(config.max_portrait_quality_bonus, int(portrait_quality)))


def score_content_filters(
    content_filter_penalty: int,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Apply configured smart-search penalties for unwanted content types."""
    penalty = min(0, int(content_filter_penalty or 0))
    return max(
        config.content_filter_min_penalty,
        penalty,
    )


def clamp_score(score: int) -> int:
    """Keep downstream storage and comparisons predictable."""
    return max(0, min(100, int(score)))


def calculate_score_details(
    details: dict,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> dict:
    """Return scoring inputs, score components, and final score."""
    width, height = details["dimensions"]
    inputs = details.copy()
    # Raw EXIF is stored separately in exif_json; keep score details focused on
    # normalized scoring inputs and component results.
    inputs.pop("exif", None)
    components = {
        "base": config.base_score,
        "blur": score_blur(details["blur_variance"], config),
        "dimensions": score_dimensions(width, height, config),
        "faces": score_faces(details["face_count"], config),
        "face_quality": score_face_quality(details["face_quality"], config),
        "rating": score_rating(details["rating"], config),
        "exif_quality": score_exif_quality(
            details["iso"],
            details["exposure_seconds"],
            config,
        ),
        "location": score_location(details["has_location"], config),
        "user_flags": score_user_flags(
            details["is_favorite"],
            details["is_edited"],
            config,
        ),
        "contrast": (
            score_contrast(details["hist_std"], config) if "hist_std" in details else 0
        ),
        "brightness": (
            score_brightness(details["brightness"], config)
            if "brightness" in details
            else 0
        ),
        "portrait_quality": score_portrait_quality(
            details.get("portrait_quality", 0),
            config,
        ),
        "content_filter_penalty": score_content_filters(
            details.get("content_filter_penalty", 0),
            config,
        ),
    }
    raw_score = sum(components.values())
    final_score = clamp_score(raw_score)
    return {
        "score": final_score,
        "raw_score": raw_score,
        "components": components,
        "inputs": inputs,
    }


def calculate_score(
    details: dict,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
) -> int:
    """Combine individual scoring rules into one 0-100 asset score."""
    return calculate_score_details(details, config)["score"]
