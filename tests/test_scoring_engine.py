"""Tests for explainable scoring rules and scoring config loading."""

from src.scoring_engine import (
    DEFAULT_SCORING_CONFIG,
    ScoringConfig,
    score_blur,
    score_dimensions,
    score_face_quality,
    score_faces,
    score_rating,
    score_exif_quality,
    score_location,
    score_user_flags,
    score_brightness,
    score_contrast,
    score_content_filters,
    score_portrait_quality,
    clamp_score,
    calculate_score,
    calculate_score_details,
    load_scoring_config,
)


def test_individual_scoring_rules_are_explainable():
    """Small scoring helpers make each heuristic easy to inspect."""
    assert score_blur(10) == -20
    assert score_blur(250) == 10
    assert score_dimensions(320, 240) == -15
    assert score_dimensions(4000, 3000) == 5
    assert score_rating(5) == 30
    assert score_rating(1) == -30


def test_exposure_parsing_and_quality_penalty():
    """High ISO and long exposures are penalized by already-normalized values."""
    assert score_exif_quality(6400, 1 / 15) == -10
    assert score_exif_quality(200, None) == 0


def test_face_scoring_helper():
    """Face helper exposes a simple domain scoring rule."""
    assert score_faces(0) == 0
    assert score_faces(2) == 10
    assert score_face_quality(0) == 0
    assert score_face_quality(20) == 20
    assert score_face_quality(40) == 25


def test_location_and_user_flag_scoring_helpers():
    """Location, favorite, and edit signals should be easy to reason about."""
    assert score_location(True) == 3
    assert score_location(False) == 0
    assert score_user_flags(True, True) == 30
    assert score_user_flags(False, False) == 0


def test_contrast_helpers_and_clamp_score():
    """Contrast and clamp helpers isolate final-score boundaries."""
    assert score_contrast(0) == -5
    assert score_contrast(100) == 3
    assert score_brightness(20) == -10
    assert score_brightness(240) == -10
    assert score_brightness(120) == 0
    assert score_portrait_quality(10) == 10
    assert score_portrait_quality(30) == 15
    assert score_content_filters(-40) == -40
    assert (
        score_content_filters(-80, ScoringConfig(content_filter_min_penalty=-30)) == -30
    )
    assert score_content_filters(5) == 0
    assert clamp_score(-10) == 0
    assert clamp_score(110) == 100


def test_load_scoring_config_uses_defaults_when_file_is_missing(tmp_path):
    """A missing local config should not block first-time setup."""
    assert load_scoring_config(str(tmp_path / "missing.toml")) == DEFAULT_SCORING_CONFIG


def test_load_scoring_config_overrides_known_numeric_fields(tmp_path):
    """Users can tune selected weights without copying every value."""
    path = tmp_path / "scoring.toml"
    path.write_text(
        """
[weights]
base_score = 40
favorite_bonus = 12

[technical_quality]
blur_low_threshold = 75

[content_filters]
content_filter_min_penalty = -25

[duplicate_detection]
duplicate_detection_enabled = false
duplicate_phash_distance_threshold = 4
timestamp_duplicate_detection_enabled = false
timestamp_duplicate_window_seconds = 3
timestamp_duplicate_phash_threshold = 9
""",
        encoding="utf-8",
    )

    config = load_scoring_config(str(path))

    assert config.base_score == 40
    assert config.favorite_bonus == 12
    assert config.blur_low_threshold == 75
    assert config.content_filter_min_penalty == -25
    assert config.duplicate_detection_enabled is False
    assert config.duplicate_phash_distance_threshold == 4
    assert config.timestamp_duplicate_detection_enabled is False
    assert config.timestamp_duplicate_window_seconds == 3
    assert config.timestamp_duplicate_phash_threshold == 9
    assert config.rating_step == DEFAULT_SCORING_CONFIG.rating_step


def test_load_scoring_config_rejects_unknown_fields(tmp_path):
    """Typos in scoring config should fail loudly instead of changing nothing."""
    path = tmp_path / "scoring.toml"
    path.write_text("[weights]\nfavourite_bonus = 12\n", encoding="utf-8")

    try:
        load_scoring_config(str(path))
    except ValueError as e:
        assert "weights.favourite_bonus" in str(e)
    else:
        raise AssertionError("Expected ValueError for unknown scoring field")


def test_load_scoring_config_rejects_boolean_values(tmp_path):
    """TOML booleans should not be accepted as accidental numeric values."""
    path = tmp_path / "scoring.toml"
    path.write_text("[weights]\nfavorite_bonus = true\n", encoding="utf-8")

    try:
        load_scoring_config(str(path))
    except ValueError as e:
        assert "favorite_bonus" in str(e)
    else:
        raise AssertionError("Expected ValueError for boolean scoring value")


def test_load_scoring_config_rejects_non_boolean_duplicate_toggle(tmp_path):
    """The duplicate detection enabled flag should be a TOML boolean."""
    path = tmp_path / "scoring.toml"
    path.write_text(
        "[duplicate_detection]\nduplicate_detection_enabled = 1\n",
        encoding="utf-8",
    )

    try:
        load_scoring_config(str(path))
    except ValueError as e:
        assert "duplicate_detection_enabled" in str(e)
    else:
        raise AssertionError("Expected ValueError for non-boolean duplicate toggle")


def test_calculate_score_combines_scoring_inputs():
    """Final score calculation should be deterministic from collected details."""
    details = {
        "blur_variance": 250,
        "dimensions": (4000, 3000),
        "face_count": 1,
        "face_quality": 25,
        "rating": 5,
        "iso": 200,
        "exposure_seconds": None,
        "has_location": True,
        "is_favorite": True,
        "is_edited": True,
        "hist_std": 100,
        "brightness": 120,
        "portrait_quality": 15,
    }

    assert calculate_score(details) == 100


def test_calculate_score_details_keeps_inputs_and_components():
    """Score details should make later tuning and recalculation possible."""
    details = {
        "blur_variance": 10,
        "dimensions": (320, 240),
        "face_count": 0,
        "face_quality": 0,
        "rating": 3,
        "iso": 6400,
        "exposure_seconds": 1 / 15,
        "has_location": False,
        "is_favorite": False,
        "is_edited": False,
        "hist_std": 20,
        "brightness": 20,
        "exif": {"iso": 6400},
        "content_labels": ["screenshot"],
        "content_filter_matches": [
            {"label": "screenshot", "query": "screenshot", "penalty": -40}
        ],
        "content_filter_penalty": -40,
    }

    score_details = calculate_score_details(details)

    assert score_details["score"] == 0
    assert score_details["raw_score"] == -50
    assert score_details["components"]["blur"] == -20
    assert score_details["components"]["brightness"] == -10
    assert score_details["components"]["portrait_quality"] == 0
    assert score_details["components"]["content_filter_penalty"] == -40
    assert score_details["inputs"]["blur_variance"] == 10
    assert score_details["inputs"]["face_count"] == 0
    assert "exif" not in score_details["inputs"]
    assert score_details["inputs"]["iso"] == 6400
    assert score_details["inputs"]["content_labels"] == ["screenshot"]
