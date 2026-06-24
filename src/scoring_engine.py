from PIL import Image
from PIL import ImageStat
import cv2
import numpy as np
import imagehash
import os


def compute_blur_variance(pil_image: Image.Image) -> float:
    """Estimate sharpness from the variance of the image Laplacian."""
    # Laplacian variance is a common blur proxy: flat/blurred images have less edge
    # energy, while sharp images produce stronger second-derivative responses.
    gray = np.array(pil_image.convert("L"))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect_faces(pil_image: Image.Image) -> int:
    """Return a best-effort count of frontal faces in the image."""
    arr = np.array(pil_image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    cascade_path = os.path.join(
        cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
    )
    face_cascade = cv2.CascadeClassifier(cascade_path)
    # Haar cascades are lightweight and available offline, which fits scheduled jobs.
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )
    return int(len(faces))


def compute_phash(pil_image: Image.Image) -> str:
    """Compute a perceptual hash for future duplicate detection."""
    return str(imagehash.phash(pil_image))


def get_asset_exif(asset_meta: dict) -> dict:
    """Return EXIF data from either historical or current Immich response shapes."""
    return asset_meta.get("exif") or asset_meta.get("exifInfo") or {}


def normalize_rating(rating):
    """Return a valid Immich 1-5 rating, or None when the asset is unrated."""
    if rating is None:
        return None
    try:
        normalized = int(rating)
    except (TypeError, ValueError):
        return None
    if 1 <= normalized <= 5:
        return normalized
    return None


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


def score_media_type(asset_meta: dict) -> int:
    """Penalize media types that are less useful for photo highlight albums."""
    media_type = asset_meta.get("mediaType") or asset_meta.get("type")
    if media_type == "VIDEO":
        return -30
    return 0


def score_faces(face_count: int) -> int:
    """Reward photos with at least one detected face."""
    if face_count > 0:
        return 15
    return 0


def score_rating(rating) -> int:
    """Convert Immich's 1-5 star user rating into a score adjustment."""
    normalized = normalize_rating(rating)
    if normalized is None:
        return 0
    # Treat 3 as neutral so explicit preference nudges, not dominates.
    return (normalized - 3) * 10


def parse_exposure_seconds(exposure):
    """Parse EXIF exposure values represented as fractions or numbers."""
    if not exposure:
        return None
    try:
        # Immich/EXIF libraries may expose exposure as either "1/60" or a float.
        if isinstance(exposure, str) and "/" in exposure:
            num, den = exposure.split("/")
            return float(num) / float(den)
        return float(exposure)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def score_exif_quality(exif: dict) -> int:
    """Penalize EXIF signals that often correlate with lower image quality."""
    score = 0
    iso = exif.get("ISO") or exif.get("iso")
    # Very high ISO often correlates with noisy low-light photos.
    if iso and isinstance(iso, (int, float)) and iso > 3200:
        score -= 5

    exposure = exif.get("ExposureTime") or exif.get("exposure_time")
    exposure_val = parse_exposure_seconds(exposure)
    if exposure_val is not None and exposure_val > 1 / 30:
        score -= 5
    return score


def score_location(exif: dict) -> int:
    """Reward location metadata because it helps create meaningful albums."""
    gps = exif.get("GPSInfo") or exif.get("gps")
    if gps:
        return 3
    return 0


def score_user_flags(asset_meta: dict) -> int:
    """Reward explicit user actions such as favorites and edits."""
    score = 0
    if asset_meta.get("isFavourite") or asset_meta.get("isFavorite"):
        # Support both British and American spellings seen across API/client data.
        score += 10
    if asset_meta.get("isEdited"):
        score += 5
    return score


def compute_contrast_stddev(pil_image: Image.Image):
    """Compute average RGB channel standard deviation as a contrast proxy."""
    stat = ImageStat.Stat(pil_image.convert("RGB"))
    return sum(stat.stddev) / 3


def score_contrast(stddev: float) -> int:
    """Reward high contrast and lightly penalize very flat images."""
    if stddev < 30:
        return -5
    if stddev > 80:
        return 3
    return 0


def clamp_score(score: int) -> int:
    """Keep downstream storage and comparisons predictable."""
    return max(0, min(100, int(score)))


def collect_image_details(asset_meta: dict, pil_image: Image.Image) -> dict:
    """Collect image and metadata signals used by the scoring rules."""
    details = {}
    try:
        details["blur_variance"] = compute_blur_variance(pil_image)
    except Exception:
        # Metadata-only failures should not prevent an asset from receiving a score.
        details["blur_variance"] = 0

    details["dimensions"] = pil_image.size

    try:
        details["face_count"] = detect_faces(pil_image)
    except Exception:
        # Face detection is helpful but optional; OpenCV data issues should not
        # drop an otherwise valid image from the scorer.
        details["face_count"] = 0

    details["exif"] = get_asset_exif(asset_meta)
    details["rating"] = normalize_rating(details["exif"].get("rating"))

    try:
        details["hist_std"] = compute_contrast_stddev(pil_image)
    except Exception:
        pass

    return details


def calculate_score(asset_meta: dict, details: dict) -> int:
    """Combine individual scoring rules into one 0-100 asset score."""
    score = 50
    score += score_blur(details["blur_variance"])

    width, height = details["dimensions"]
    score += score_dimensions(width, height)

    score += score_media_type(asset_meta)
    score += score_faces(details["face_count"])
    score += score_rating(details["rating"])
    score += score_exif_quality(details["exif"])
    score += score_location(details["exif"])
    score += score_user_flags(asset_meta)

    if "hist_std" in details:
        score += score_contrast(details["hist_std"])

    return clamp_score(score)


def score_asset(asset_meta: dict, pil_image: Image.Image) -> dict:
    """Score an asset from 0-100 using cheap local image heuristics."""
    details = collect_image_details(asset_meta, pil_image)
    details["score"] = calculate_score(asset_meta, details)
    return details
