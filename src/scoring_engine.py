from PIL import Image
from PIL import ImageStat
import cv2
import numpy as np
import imagehash
import os


def compute_blur_variance(pil_image: Image.Image) -> float:
    gray = np.array(pil_image.convert("L"))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect_faces(pil_image: Image.Image) -> int:
    arr = np.array(pil_image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    cascade_path = os.path.join(
        cv2.data.haarcascades, "haarcascade_frontalface_default.xml"
    )
    face_cascade = cv2.CascadeClassifier(cascade_path)
    faces = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
    )
    return int(len(faces))


def compute_phash(pil_image: Image.Image) -> str:
    return str(imagehash.phash(pil_image))


def score_asset(asset_meta: dict, pil_image: Image.Image) -> dict:
    score = 50
    details = {}
    try:
        blur = compute_blur_variance(pil_image)
    except Exception:
        blur = 0
    details['blur_variance'] = blur
    if blur < 50:
        score -= 20
    elif blur > 200:
        score += 10

    w, h = pil_image.size
    details['dimensions'] = (w, h)
    if min(w, h) < 640:
        score -= 15
    if max(w, h) > 3000:
        score += 5

    if asset_meta.get('mediaType') == 'VIDEO':
        score -= 30

    faces = 0
    try:
        faces = detect_faces(pil_image)
    except Exception:
        faces = 0
    details['face_count'] = faces
    if faces > 0:
        score += 15

    exif = asset_meta.get('exif', {}) or {}
    details['exif'] = exif
    iso = exif.get('ISO') or exif.get('iso')
    if iso and isinstance(iso, (int, float)) and iso > 3200:
        score -= 5

    exposure = exif.get('ExposureTime') or exif.get('exposure_time')
    if exposure:
        try:
            if isinstance(exposure, str) and '/' in exposure:
                num, den = exposure.split('/')
                exposure_val = float(num) / float(den)
            else:
                exposure_val = float(exposure)
            if exposure_val > 1/30:
                score -= 5
        except Exception:
            pass

    gps = exif.get('GPSInfo') or exif.get('gps')
    if gps:
        score += 3

    if asset_meta.get('isFavourite') or asset_meta.get('isFavorite'):
        score += 10
    if asset_meta.get('isEdited'):
        score += 5

    # Histogram contrast proxy
    try:
        stat = ImageStat.Stat(pil_image.convert('RGB'))
        stddev = sum(stat.stddev)/3
        details['hist_std'] = stddev
        if stddev < 30:
            score -= 5
        elif stddev > 80:
            score += 3
    except Exception:
        pass

    # clamp
    score = max(0, min(100, int(score)))
    details['score'] = score
    return details
