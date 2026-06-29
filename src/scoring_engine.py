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
        return 10
    return 0


def score_face_quality(face_quality: int) -> int:
    """Reward the best detected face without letting it dominate the score."""
    return max(0, min(25, int(face_quality)))


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


def score_portrait_quality(portrait_quality: int) -> int:
    """Reward sharp-subject/soft-background photos without dominating the score."""
    return max(0, min(15, int(portrait_quality)))


def score_content_filters(content_filter_penalty: int) -> int:
    """Apply configured smart-search penalties for unwanted content types."""
    return min(0, int(content_filter_penalty or 0))


def clamp_score(score: int) -> int:
    """Keep downstream storage and comparisons predictable."""
    return max(0, min(100, int(score)))


def calculate_score_details(details: dict) -> dict:
    """Return scoring inputs, score components, and final score."""
    width, height = details["dimensions"]
    inputs = details.copy()
    # Raw EXIF is stored separately in exif_json; keep score details focused on
    # normalized scoring inputs and component results.
    inputs.pop("exif", None)
    components = {
        "base": 50,
        "blur": score_blur(details["blur_variance"]),
        "dimensions": score_dimensions(width, height),
        "faces": score_faces(details["face_count"]),
        "face_quality": score_face_quality(details["face_quality"]),
        "rating": score_rating(details["rating"]),
        "exif_quality": score_exif_quality(
            details["iso"],
            details["exposure_seconds"],
        ),
        "location": score_location(details["has_location"]),
        "user_flags": score_user_flags(details["is_favorite"], details["is_edited"]),
        "contrast": score_contrast(details["hist_std"]) if "hist_std" in details else 0,
        "brightness": (
            score_brightness(details["brightness"]) if "brightness" in details else 0
        ),
        "portrait_quality": score_portrait_quality(details.get("portrait_quality", 0)),
        "content_filter_penalty": score_content_filters(
            details.get("content_filter_penalty", 0)
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


def calculate_score(details: dict) -> int:
    """Combine individual scoring rules into one 0-100 asset score."""
    return calculate_score_details(details)["score"]
