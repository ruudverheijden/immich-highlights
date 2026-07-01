"""Technical image-analysis stage for objective visual quality facts."""

import hashlib

try:
    from .asset_analysis import (
        compute_blur_variance,
        compute_brightness,
        compute_contrast_stddev,
        compute_phash,
        compute_portrait_quality,
    )
except ImportError:
    from asset_analysis import (
        compute_blur_variance,
        compute_brightness,
        compute_contrast_stddev,
        compute_phash,
        compute_portrait_quality,
    )


def checksum_file(path: str) -> str:
    """Hash downloaded bytes when Immich does not expose an asset checksum."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def analyze_technical_image(pil_image, faces: list[dict] | None = None) -> dict:
    """Compute objective image facts from a preview image."""
    details = {}
    try:
        details["blur_variance"] = compute_blur_variance(pil_image)
    except Exception:
        details["blur_variance"] = 0

    details["dimensions"] = pil_image.size

    try:
        details["phash"] = compute_phash(pil_image)
    except Exception:
        details["phash"] = None

    try:
        details["hist_std"] = compute_contrast_stddev(pil_image)
    except Exception:
        pass

    try:
        details["brightness"] = compute_brightness(pil_image)
    except Exception:
        pass

    try:
        details.update(compute_portrait_quality(pil_image, faces or []))
    except Exception:
        details["portrait_quality"] = 0

    return details
