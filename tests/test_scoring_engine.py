from PIL import Image

from src.scoring_engine import (
    score_asset,
    compute_phash,
    compute_blur_variance,
)


def test_score_basic_image():
    img = Image.new('RGB', (800, 600), color=(120, 120, 120))
    meta = {'mediaType': 'IMAGE', 'isFavourite': False}
    details = score_asset(meta, img)
    assert 'score' in details
    assert 0 <= details['score'] <= 100


def test_phash_and_blur():
    img = Image.new('RGB', (200, 200), color=(200, 180, 160))
    ph = compute_phash(img)
    assert isinstance(ph, str)
    blur = compute_blur_variance(img)
    assert isinstance(blur, float)
