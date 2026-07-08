from __future__ import annotations

from typing import Any

import numpy as np


def luminance(rgb: np.ndarray) -> np.ndarray:
    arr = rgb.astype(np.float32)
    if arr.ndim == 2:
        return arr
    return 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]


def brightness_percentiles(rgb: np.ndarray) -> dict[str, float]:
    gray = luminance(rgb)
    p5, p50, p95 = np.percentile(gray, [5, 50, 95])
    return {"p5": float(p5), "p50": float(p50), "p95": float(p95)}


def black_border_metrics(
    rgb: np.ndarray,
    *,
    band_px: int = 8,
    mean_threshold: float = 6.0,
    var_threshold: float = 8.0,
) -> dict[str, Any]:
    gray = luminance(rgb)
    h, w = gray.shape[:2]
    band_px = max(1, min(band_px, h // 2, w // 2))
    bands = {
        "top": gray[:band_px, :],
        "bottom": gray[h - band_px :, :],
        "left": gray[:, :band_px],
        "right": gray[:, w - band_px :],
    }
    per_edge = {
        name: {"mean": float(values.mean()), "var": float(values.var())}
        for name, values in bands.items()
    }
    flagged = {
        name: values["mean"] <= mean_threshold and values["var"] <= var_threshold
        for name, values in per_edge.items()
    }
    return {
        "band_px": band_px,
        "mean_threshold": mean_threshold,
        "var_threshold": var_threshold,
        "edges": per_edge,
        "flagged_edges": flagged,
        "has_black_border": any(flagged.values()),
    }


def zero_mean_ncc(a: np.ndarray, b: np.ndarray) -> float:
    left = luminance(a).astype(np.float32)
    right = luminance(b).astype(np.float32)
    if left.shape != right.shape:
        raise ValueError(f"NCC inputs must have same shape, got {left.shape} and {right.shape}")
    left = left - float(left.mean())
    right = right - float(right.mean())
    denom = float(np.sqrt(np.sum(left * left) * np.sum(right * right)))
    if denom == 0:
        return 1.0 if np.allclose(left, right) else 0.0
    return float(np.sum(left * right) / denom)
