import numpy as np

from mcdata.qa.metrics import black_border_metrics, zero_mean_ncc


def test_black_border_detection_flags_dark_uniform_border() -> None:
    image = np.full((64, 64, 3), 180, dtype=np.uint8)
    image[:8, :, :] = 0
    image[-8:, :, :] = 0
    image[:, :8, :] = 0
    image[:, -8:, :] = 0

    result = black_border_metrics(image, band_px=8)

    assert result["has_black_border"] is True
    assert all(result["flagged_edges"].values())


def test_black_border_detection_ignores_noisy_visible_edges() -> None:
    rng = np.random.default_rng(1)
    image = rng.integers(40, 220, size=(64, 64, 3), dtype=np.uint8)

    result = black_border_metrics(image, band_px=8)

    assert result["has_black_border"] is False


def test_zero_mean_ncc_direction_for_same_and_shifted_images() -> None:
    image = np.zeros((36, 64), dtype=np.float32)
    image[:, 10:30] = 255
    shifted = np.roll(image, 10, axis=1)

    assert zero_mean_ncc(image, image) > 0.99
    assert zero_mean_ncc(image, shifted) < 0.7
