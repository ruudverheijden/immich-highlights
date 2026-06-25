from src.scoring_engine import (
    score_blur,
    score_dimensions,
    score_faces,
    score_rating,
    score_exif_quality,
    score_location,
    score_user_flags,
    score_brightness,
    score_contrast,
    clamp_score,
    calculate_score,
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
    assert score_faces(2) == 15


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
    assert clamp_score(-10) == 0
    assert clamp_score(110) == 100


def test_calculate_score_combines_scoring_inputs():
    """Final score calculation should be deterministic from collected details."""
    details = {
        "blur_variance": 250,
        "dimensions": (4000, 3000),
        "face_count": 1,
        "rating": 5,
        "iso": 200,
        "exposure_seconds": None,
        "has_location": True,
        "is_favorite": True,
        "is_edited": True,
        "hist_std": 100,
        "brightness": 120,
    }

    assert calculate_score(details) == 100
