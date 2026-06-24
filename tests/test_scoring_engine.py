from PIL import Image, ImageDraw, ImageFilter

from src.scoring_engine import (
    score_asset,
    score_blur,
    score_dimensions,
    score_media_type,
    score_faces,
    score_rating,
    parse_exposure_seconds,
    score_exif_quality,
    score_location,
    score_user_flags,
    compute_contrast_stddev,
    score_contrast,
    clamp_score,
    collect_image_details,
    calculate_score,
    compute_phash,
    compute_blur_variance,
    detect_faces,
    get_asset_exif,
    normalize_rating,
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
    assert faces == 0


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


def test_score_video_and_favorite_gps():
    """Domain signals should move the score in the intended direction."""
    photo_meta = {"mediaType": "PHOTO"}
    video_meta = {"mediaType": "VIDEO"}
    img = make_checkerboard(400, 10)

    sp = score_asset(photo_meta, img)
    sv = score_asset(video_meta, img)
    assert sv["score"] < sp["score"]

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


def test_individual_scoring_rules_are_explainable():
    """Small scoring helpers make each heuristic easy to inspect."""
    assert score_blur(10) == -20
    assert score_blur(250) == 10
    assert score_dimensions(320, 240) == -15
    assert score_dimensions(4000, 3000) == 5
    assert score_rating(5) == 20
    assert score_rating(1) == -20


def test_exposure_parsing_and_quality_penalty():
    """Long exposures are penalized because they are more likely to blur."""
    assert parse_exposure_seconds("1/15") == 1 / 15
    assert parse_exposure_seconds("bad") is None
    assert score_exif_quality({"iso": 6400, "exposure_time": "1/15"}) == -10


def test_media_type_and_face_scoring_helpers():
    """Media type and face helpers expose simple domain scoring rules."""
    assert score_media_type({"type": "VIDEO"}) == -30
    assert score_media_type({"type": "IMAGE"}) == 0
    assert score_faces(0) == 0
    assert score_faces(2) == 15


def test_location_and_user_flag_scoring_helpers():
    """Location, favorite, and edit signals should be easy to reason about."""
    assert score_location({"gps": {"lat": 1}}) == 3
    assert score_location({}) == 0
    assert score_user_flags({"isFavorite": True, "isEdited": True}) == 15
    assert score_user_flags({}) == 0


def test_contrast_helpers_and_clamp_score():
    """Contrast and clamp helpers isolate final-score boundaries."""
    flat = Image.new("RGB", (100, 100), color=(128, 128, 128))
    checkerboard = make_checkerboard(100, 10)

    assert compute_contrast_stddev(flat) == 0
    assert compute_contrast_stddev(checkerboard) > 80
    assert score_contrast(0) == -5
    assert score_contrast(100) == 3
    assert clamp_score(-10) == 0
    assert clamp_score(110) == 100


def test_collect_image_details_returns_scoring_inputs():
    """Detail collection should gather all inputs used by calculate_score."""
    img = Image.new("RGB", (800, 600), color=(120, 120, 120))
    meta = {"type": "IMAGE", "exifInfo": {"rating": "4"}}

    details = collect_image_details(meta, img)

    assert details["dimensions"] == (800, 600)
    assert "blur_variance" in details
    assert "face_count" in details
    assert details["exif"] == {"rating": "4"}
    assert details["rating"] == 4
    assert "hist_std" in details


def test_calculate_score_combines_scoring_inputs():
    """Final score calculation should be deterministic from collected details."""
    details = {
        "blur_variance": 250,
        "dimensions": (4000, 3000),
        "face_count": 1,
        "rating": 5,
        "exif": {"gps": {"lat": 1}},
        "hist_std": 100,
    }
    meta = {"type": "IMAGE", "isFavorite": True, "isEdited": True}

    assert calculate_score(meta, details) == 100
