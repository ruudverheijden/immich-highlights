from PIL import Image, ImageDraw, ImageFilter

from src.scoring_engine import (
    score_asset,
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
