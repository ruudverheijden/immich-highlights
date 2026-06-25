def score_blur(blur: float) -> int:
    """Score image sharpness from a precomputed blur variance."""
    if blur < 50:
        return -20
    if blur > 200:
        return 10
    return 0


def score_dimensions(width: int, height: int) -> int:
    """Reward large images and penalize very small assets."""
    score = 0
    # Very small images are often screenshots, thumbnails, or received media.
    if min(width, height) < 640:
        score -= 15
    # Large originals tend to contain more usable detail for highlights.
    if max(width, height) > 3000:
        score += 5
    return score


def score_faces(face_count: int) -> int:
    """Reward photos with at least one detected face."""
    if face_count > 0:
        return 15
    return 0


def score_rating(rating) -> int:
    """Convert Immich's 1-5 star user rating into a score adjustment."""
    if rating is None:
        return 0
    # Treat 3 as neutral so explicit preference nudges, not dominates.
    return (rating - 3) * 15


def score_exif_quality(iso, exposure_seconds) -> int:
    """Penalize EXIF signals that often correlate with lower image quality."""
    score = 0
    # Very high ISO often correlates with noisy low-light photos.
    if iso and isinstance(iso, (int, float)) and iso > 3200:
        score -= 5

    if exposure_seconds is not None and exposure_seconds > 1 / 30:
        score -= 5
    return score


def score_location(has_location: bool) -> int:
    """Reward location metadata because it helps create meaningful albums."""
    if has_location:
        return 3
    return 0


def score_user_flags(is_favorite: bool, is_edited: bool) -> int:
    """Reward explicit user actions such as favorites and edits."""
    score = 0
    if is_favorite:
        score += 20
    if is_edited:
        score += 10
    return score


def score_contrast(stddev: float) -> int:
    """Reward high contrast and lightly penalize very flat images."""
    if stddev < 30:
        return -5
    if stddev > 80:
        return 3
    return 0


def score_brightness(brightness: float) -> int:
    """Penalize images that are likely underexposed or overexposed."""
    if brightness < 35:
        return -10
    if brightness > 225:
        return -10
    return 0


def clamp_score(score: int) -> int:
    """Keep downstream storage and comparisons predictable."""
    return max(0, min(100, int(score)))


def calculate_score(details: dict) -> int:
    """Combine individual scoring rules into one 0-100 asset score."""
    score = 50
    score += score_blur(details["blur_variance"])

    width, height = details["dimensions"]
    score += score_dimensions(width, height)

    score += score_faces(details["face_count"])
    score += score_rating(details["rating"])
    score += score_exif_quality(details["iso"], details["exposure_seconds"])
    score += score_location(details["has_location"])
    score += score_user_flags(details["is_favorite"], details["is_edited"])

    if "hist_std" in details:
        score += score_contrast(details["hist_std"])

    if "brightness" in details:
        score += score_brightness(details["brightness"])

    return clamp_score(score)
