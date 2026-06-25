from PIL import Image, ImageDraw, ImageFilter

from src.asset_analysis import (
    score_asset,
    collect_image_details,
    compute_brightness,
    compute_best_face_quality,
    compute_blur_variance,
    compute_contrast_stddev,
    compute_face_quality,
    compute_phash,
    detect_faces,
    get_exif_exposure_seconds,
    get_exif_iso,
    get_asset_exif,
    has_location,
    is_edited,
    is_favorite,
    normalize_rating,
    parse_exposure_seconds,
    score_face_brightness,
    score_face_center,
    score_face_sharpness,
    score_face_size,
)


def make_checkerboard(size=256, block=8):
    """Create a synthetic high-edge image for deterministic blur tests."""
    img = Image.new("L", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(0, size, block):
        for x in range(0, size, block):
            if ((x // block) + (y // block)) % 2 == 0:
                draw.rectangle([x, y, x + block - 1, y + block - 1], fill=255)
            else:
                draw.rectangle([x, y, x + block - 1, y + block - 1], fill=0)
    return img.convert("RGB")


def test_score_basic_image():
    """A plain image should always produce a bounded score payload."""
    img = Image.new("RGB", (800, 600), color=(120, 120, 120))
    meta = {"mediaType": "IMAGE", "isFavourite": False}
    details = score_asset(meta, img)
    assert "score" in details
    assert 0 <= details["score"] <= 100


def test_phash_and_blur():
    img = Image.new("RGB", (200, 200), color=(200, 180, 160))
    ph = compute_phash(img)
    assert isinstance(ph, str)
    blur = compute_blur_variance(img)
    assert isinstance(blur, float)


def test_blur_variance_sharp_vs_blurred():
    """The blur metric should rank the generated sharp image above its blurred copy."""
    sharp = make_checkerboard(256, 8)
    blurred = sharp.filter(ImageFilter.GaussianBlur(5))

    v_sharp = compute_blur_variance(sharp)
    v_blur = compute_blur_variance(blurred)

    assert isinstance(v_sharp, float)
    assert isinstance(v_blur, float)
    assert v_sharp > v_blur


def test_phash_and_no_faces():
    img = Image.new("RGB", (300, 300), color=(128, 128, 128))
    ph = compute_phash(img)
    assert isinstance(ph, str)
    faces = detect_faces(img)
    assert faces == []


def test_score_small_vs_regular():
    """Small assets should not outrank otherwise similar regular-sized images."""
    meta = {}
    regular = Image.new("RGB", (800, 600), color=(120, 120, 120))
    small = Image.new("RGB", (320, 240), color=(120, 120, 120))

    r = score_asset(meta, regular)
    s = score_asset(meta, small)

    assert 0 <= r["score"] <= 100
    assert 0 <= s["score"] <= 100
    assert s["score"] <= r["score"]


def test_score_favorite_and_gps():
    """Domain signals should move the score in the intended direction."""
    photo_meta = {"mediaType": "PHOTO"}
    img = make_checkerboard(400, 10)

    sp = score_asset(photo_meta, img)

    rich_meta = {
        "mediaType": "PHOTO",
        "exif": {"GPSInfo": {"lat": 1}},
        "isFavourite": True,
        "isEdited": True,
    }
    sr = score_asset(rich_meta, img)
    assert sr["score"] >= sp["score"]


def test_score_uses_immich_rating():
    """User star ratings should influence otherwise identical photos."""
    img = Image.new("RGB", (800, 600), color=(120, 120, 120))
    unrated = score_asset({"type": "IMAGE", "exifInfo": {}}, img)
    five_star = score_asset({"type": "IMAGE", "exifInfo": {"rating": 5}}, img)
    one_star = score_asset({"type": "IMAGE", "exifInfo": {"rating": 1}}, img)

    assert five_star["rating"] == 5
    assert one_star["rating"] == 1
    assert five_star["score"] > unrated["score"]
    assert one_star["score"] < unrated["score"]


def test_rating_helpers_support_current_immich_metadata_shape():
    """Current Immich asset responses expose rating inside exifInfo."""
    meta = {"exifInfo": {"rating": "4", "iso": 200}}

    assert get_asset_exif(meta) == {"rating": "4", "iso": 200}
    assert normalize_rating("4") == 4
    assert normalize_rating(0) is None


def test_metadata_normalization_helpers_support_current_immich_shapes():
    """Immich-specific key handling should stay outside the scoring engine."""
    exif = {"iso": 6400, "exposure_time": "1/15", "gps": {"lat": 1}}

    assert parse_exposure_seconds("1/15") == 1 / 15
    assert parse_exposure_seconds("bad") is None
    assert get_exif_iso(exif) == 6400
    assert get_exif_exposure_seconds(exif) == 1 / 15
    assert has_location(exif)
    assert is_favorite({"isFavorite": True})
    assert is_favorite({"isFavourite": True})
    assert is_edited({"isEdited": True})


def test_contrast_stddev_reads_image_signal():
    """Contrast calculation belongs with image analysis rather than score policy."""
    flat = Image.new("RGB", (100, 100), color=(128, 128, 128))
    checkerboard = make_checkerboard(100, 10)

    assert compute_contrast_stddev(flat) == 0
    assert compute_contrast_stddev(checkerboard) > 80


def test_brightness_reads_image_luminance():
    """Brightness is a cheap signal for underexposed and overexposed photos."""
    dark = Image.new("RGB", (100, 100), color=(10, 10, 10))
    bright = Image.new("RGB", (100, 100), color=(240, 240, 240))

    assert compute_brightness(dark) == 10
    assert compute_brightness(bright) == 240


def test_face_quality_helpers_score_good_face_inputs():
    """Face quality combines size, center, sharpness, and brightness signals."""
    img = make_checkerboard(400, 10)
    face = {"x": 100, "y": 100, "width": 200, "height": 200}

    assert score_face_size(face, img.size) == 8
    assert score_face_center(face, img.size) == 6
    assert score_face_sharpness(img.crop((100, 100, 300, 300))) == 6
    assert score_face_brightness(Image.new("RGB", (50, 50), color=(120, 120, 120))) == 5
    assert compute_face_quality(img, face) == 25
    assert compute_best_face_quality(img, [face]) == 25


def test_face_quality_helpers_penalize_weak_face_inputs():
    """Tiny, off-center, flat, or dark face crops should not get a quality boost."""
    img = Image.new("RGB", (400, 400), color=(10, 10, 10))
    face = {"x": 0, "y": 0, "width": 40, "height": 40}

    assert score_face_size(face, img.size) == 0
    assert score_face_center(face, img.size) == 0
    assert score_face_sharpness(img.crop((0, 0, 40, 40))) == 0
    assert score_face_brightness(img.crop((0, 0, 40, 40))) == 0
    assert compute_face_quality(img, face) == 0
    assert compute_best_face_quality(img, []) == 0


def test_collect_image_details_returns_scoring_inputs():
    """Detail collection should gather all inputs used by calculate_score."""
    img = Image.new("RGB", (800, 600), color=(120, 120, 120))
    meta = {
        "type": "IMAGE",
        "exifInfo": {
            "rating": "4",
            "iso": 200,
            "exposure_time": "1/60",
            "gps": {"lat": 1},
        },
        "isFavorite": True,
        "isEdited": True,
    }

    details = collect_image_details(meta, img)

    assert details["dimensions"] == (800, 600)
    assert "blur_variance" in details
    assert "face_count" in details
    assert "face_quality" in details
    assert "faces" in details
    assert details["exif"]["rating"] == "4"
    assert details["rating"] == 4
    assert details["iso"] == 200
    assert details["exposure_seconds"] == 1 / 60
    assert details["has_location"]
    assert details["is_favorite"]
    assert details["is_edited"]
    assert "hist_std" in details
    assert "brightness" in details
