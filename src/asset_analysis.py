from PIL import Image
from PIL import ImageDraw
from PIL import ImageStat
import cv2
import imagehash
import numpy as np
import os

try:
    from .scoring_engine import calculate_score_details
except ImportError:
    from scoring_engine import calculate_score_details


def compute_blur_variance(pil_image: Image.Image) -> float:
    """Estimate sharpness from the variance of the image Laplacian."""
    # Laplacian variance is a common blur proxy: flat/blurred images have less edge
    # energy, while sharp images produce stronger second-derivative responses.
    gray = np.array(pil_image.convert("L"))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect_faces(pil_image: Image.Image) -> list[dict]:
    """Return best-effort frontal face boxes in image coordinates."""
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
    return [
        {
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        }
        for x, y, width, height in faces
    ]


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


def first_present(mapping: dict, keys: tuple[str, ...]):
    """Return the first present value, preserving valid falsey values like 0.0."""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def has_location(exif: dict) -> bool:
    """Return True when EXIF contains GPS metadata."""
    if exif.get("GPSInfo") or exif.get("gps"):
        return True

    latitude = first_present(
        exif,
        ("latitude", "Latitude", "GPSLatitude", "gpsLatitude", "lat"),
    )
    longitude = first_present(
        exif,
        ("longitude", "Longitude", "GPSLongitude", "gpsLongitude", "lng", "lon"),
    )
    return latitude is not None and longitude is not None


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


def compute_brightness(pil_image: Image.Image) -> float:
    """Compute average luminance on a 0-255 scale."""
    stat = ImageStat.Stat(pil_image.convert("L"))
    return float(stat.mean[0])


def crop_face(pil_image: Image.Image, face: dict) -> Image.Image:
    """Crop a detected face box from the image."""
    x = face["x"]
    y = face["y"]
    return pil_image.crop((x, y, x + face["width"], y + face["height"]))


def score_face_size(face: dict, image_size: tuple[int, int]) -> int:
    """Score whether a face is large enough to matter in a highlight photo."""
    image_width, image_height = image_size
    face_area = face["width"] * face["height"]
    image_area = image_width * image_height
    if image_area <= 0:
        return 0
    area_ratio = face_area / image_area
    if area_ratio >= 0.08:
        return 8
    if area_ratio >= 0.04:
        return 4
    return 0


def score_face_center(face: dict, image_size: tuple[int, int]) -> int:
    """Score whether a face is near the visual center of the image."""
    image_width, image_height = image_size
    if image_width <= 0 or image_height <= 0:
        return 0

    face_center_x = face["x"] + face["width"] / 2
    face_center_y = face["y"] + face["height"] / 2
    normalized_x = abs(face_center_x / image_width - 0.5)
    normalized_y = abs(face_center_y / image_height - 0.5)

    if normalized_x <= 0.25 and normalized_y <= 0.25:
        return 6
    if normalized_x <= 0.35 and normalized_y <= 0.35:
        return 3
    return 0


def score_face_sharpness(face_image: Image.Image) -> int:
    """Score whether the face crop itself is sharp enough."""
    blur = compute_blur_variance(face_image)
    if blur > 150:
        return 6
    if blur >= 75:
        return 3
    return 0


def score_face_brightness(face_image: Image.Image) -> int:
    """Score whether the face crop is in a useful brightness range."""
    brightness = compute_brightness(face_image)
    if 60 <= brightness <= 190:
        return 5
    if 40 <= brightness <= 220:
        return 2
    return 0


def compute_face_quality(pil_image: Image.Image, face: dict) -> int:
    """Score one detected face from size, position, sharpness, and brightness."""
    face_image = crop_face(pil_image, face)
    return (
        score_face_size(face, pil_image.size)
        + score_face_center(face, pil_image.size)
        + score_face_sharpness(face_image)
        + score_face_brightness(face_image)
    )


def compute_best_face_quality(pil_image: Image.Image, faces: list[dict]) -> int:
    """Return the strongest face quality score found in an image."""
    if not faces:
        return 0
    return max(compute_face_quality(pil_image, face) for face in faces)


def select_subject_box(image_size: tuple[int, int], faces: list[dict]) -> dict:
    """Pick a likely subject box from faces, or fall back to a centered crop."""
    if faces:
        return max(faces, key=lambda face: face["width"] * face["height"])

    image_width, image_height = image_size
    box_width = int(image_width * 0.4)
    box_height = int(image_height * 0.5)
    return {
        "x": int((image_width - box_width) / 2),
        "y": int((image_height - box_height) / 2),
        "width": box_width,
        "height": box_height,
    }


def expand_box(face: dict, image_size: tuple[int, int], scale: float) -> dict:
    """Expand a box around its center while keeping it inside image bounds."""
    image_width, image_height = image_size
    center_x = face["x"] + face["width"] / 2
    center_y = face["y"] + face["height"] / 2
    width = min(image_width, int(face["width"] * scale))
    height = min(image_height, int(face["height"] * scale))
    x = max(0, int(center_x - width / 2))
    y = max(0, int(center_y - height / 2))
    x = min(x, max(0, image_width - width))
    y = min(y, max(0, image_height - height))
    return {"x": x, "y": y, "width": width, "height": height}


def crop_box(pil_image: Image.Image, box: dict) -> Image.Image:
    """Crop an arbitrary image box."""
    x = box["x"]
    y = box["y"]
    return pil_image.crop((x, y, x + box["width"], y + box["height"]))


def crop_background_ring(pil_image: Image.Image, subject_box: dict) -> Image.Image:
    """Crop the area around a subject to approximate background sharpness."""
    outer_box = expand_box(subject_box, pil_image.size, 2.2)
    outer = crop_box(pil_image, outer_box)
    mask = Image.new("L", outer.size, 255)
    inner_x = max(0, subject_box["x"] - outer_box["x"])
    inner_y = max(0, subject_box["y"] - outer_box["y"])
    inner = (
        inner_x,
        inner_y,
        inner_x + subject_box["width"],
        inner_y + subject_box["height"],
    )
    ImageDraw.Draw(mask).rectangle(inner, fill=0)
    background = Image.new("RGB", outer.size)
    background.paste(outer.convert("RGB"), mask=mask)
    return background


def score_portrait_subject(subject_box: dict, image_size: tuple[int, int]) -> int:
    """Reward subject boxes that are centered and not too small or too large."""
    size_score = score_face_size(subject_box, image_size)
    center_score = score_face_center(subject_box, image_size)
    return min(4, int((size_score + center_score) / 3))


def compute_portrait_quality(pil_image: Image.Image, faces: list[dict]) -> dict:
    """Estimate sharp-subject/soft-background portrait-like quality."""
    subject_box = expand_box(
        select_subject_box(pil_image.size, faces), pil_image.size, 1.6
    )
    subject_image = crop_box(pil_image, subject_box)
    background_image = crop_background_ring(pil_image, subject_box)
    subject_sharpness = compute_blur_variance(subject_image)
    background_sharpness = compute_blur_variance(background_image)
    blur_ratio = subject_sharpness / max(background_sharpness, 1.0)

    quality = 0
    if subject_sharpness > 150 and background_sharpness < 80:
        quality += 10
    if blur_ratio >= 2.5:
        quality += 8
    quality += score_portrait_subject(subject_box, pil_image.size)

    return {
        "portrait_quality": min(15, quality),
        "subject_sharpness": subject_sharpness,
        "background_sharpness": background_sharpness,
        "subject_background_blur_ratio": blur_ratio,
        "subject_box": subject_box,
    }


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
        details["faces"] = detect_faces(pil_image)
        details["face_count"] = len(details["faces"])
        details["face_quality"] = compute_best_face_quality(pil_image, details["faces"])
    except Exception:
        # Face detection is helpful but optional; OpenCV data issues should not
        # drop an otherwise valid image from the scorer.
        details["faces"] = []
        details["face_count"] = 0
        details["face_quality"] = 0

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

    try:
        details["brightness"] = compute_brightness(pil_image)
    except Exception:
        pass

    try:
        details.update(compute_portrait_quality(pil_image, details["faces"]))
    except Exception:
        details["portrait_quality"] = 0

    return details


def score_asset(asset_meta: dict, pil_image: Image.Image) -> dict:
    """Analyze an asset and attach its final highlight score."""
    details = collect_image_details(asset_meta, pil_image)
    score_details = calculate_score_details(details)
    details["score"] = score_details["score"]
    details["score_details"] = score_details
    return details
