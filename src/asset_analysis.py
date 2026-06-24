from PIL import Image
from PIL import ImageStat
import cv2
import imagehash
import numpy as np
import os

try:
    from .scoring_engine import calculate_score
except ImportError:
    from scoring_engine import calculate_score


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


def get_exif_iso(exif: dict):
    """Return ISO from known EXIF key variants."""
    return exif.get("ISO") or exif.get("iso")


def get_exif_exposure_seconds(exif: dict):
    """Return exposure time in seconds from known EXIF key variants."""
    exposure = exif.get("ExposureTime") or exif.get("exposure_time")
    return parse_exposure_seconds(exposure)


def has_location(exif: dict) -> bool:
    """Return True when EXIF contains GPS metadata."""
    return bool(exif.get("GPSInfo") or exif.get("gps"))


def is_favorite(asset_meta: dict) -> bool:
    """Return True when Immich marks the asset as a favorite."""
    # Support both British and American spellings seen across API/client data.
    return bool(asset_meta.get("isFavourite") or asset_meta.get("isFavorite"))


def is_edited(asset_meta: dict) -> bool:
    """Return True when Immich marks the asset as edited."""
    return bool(asset_meta.get("isEdited"))


def compute_contrast_stddev(pil_image: Image.Image):
    """Compute average RGB channel standard deviation as a contrast proxy."""
    stat = ImageStat.Stat(pil_image.convert("RGB"))
    return sum(stat.stddev) / 3


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
    details["iso"] = get_exif_iso(details["exif"])
    details["exposure_seconds"] = get_exif_exposure_seconds(details["exif"])
    details["has_location"] = has_location(details["exif"])
    details["is_favorite"] = is_favorite(asset_meta)
    details["is_edited"] = is_edited(asset_meta)

    try:
        details["hist_std"] = compute_contrast_stddev(pil_image)
    except Exception:
        pass

    return details


def score_asset(asset_meta: dict, pil_image: Image.Image) -> dict:
    """Analyze an asset and attach its final highlight score."""
    details = collect_image_details(asset_meta, pil_image)
    details["score"] = calculate_score(details)
    return details
