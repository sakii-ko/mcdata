from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaptureSettings:
    width: int
    height: int
    fps: int
    display: str
    desktop: bool
    hide_hud: bool
    view_settle_sec: float
    ready_delay_sec: float

    @classmethod
    def from_env(cls, profile: dict[str, Any]) -> "CaptureSettings":
        width, height = _parse_capture_size(
            os.environ.get("MCDATA_CAPTURE_SIZE"),
            default_width=int(profile.get("width")),
            default_height=int(profile.get("height")),
        )
        return cls(
            width=width,
            height=height,
            fps=_parse_positive_int(
                os.environ.get("MCDATA_CAPTURE_FPS"),
                default=int(profile.get("capture_fps", 24)),
                name="MCDATA_CAPTURE_FPS",
            ),
            display=os.environ.get("DISPLAY", ":0"),
            desktop=_parse_bool(os.environ.get("MCDATA_CAPTURE_DESKTOP"), default=False),
            hide_hud=_parse_bool(os.environ.get("MCDATA_HIDE_HUD"), default=False),
            view_settle_sec=_parse_float(
                os.environ.get("MCDATA_VIEW_SETTLE_SEC"),
                default=1.0,
                name="MCDATA_VIEW_SETTLE_SEC",
            ),
            ready_delay_sec=_parse_float(
                os.environ.get("MCDATA_CAPTURE_READY_DELAY"),
                default=float(profile.get("capture_ready_delay_sec", 5)),
                name="MCDATA_CAPTURE_READY_DELAY",
            ),
        )


def _parse_capture_size(raw: str | None, *, default_width: int, default_height: int) -> tuple[int, int]:
    if raw is None or raw == "":
        return default_width, default_height
    try:
        width, height = raw.lower().split("x", 1)
        parsed = int(width), int(height)
    except ValueError as exc:
        raise RuntimeError("MCDATA_CAPTURE_SIZE must look like 1280x720") from exc
    if parsed[0] <= 0 or parsed[1] <= 0:
        raise RuntimeError("MCDATA_CAPTURE_SIZE dimensions must be positive")
    return parsed


def _parse_positive_int(raw: str | None, *, default: int, name: str) -> int:
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def _parse_float(raw: str | None, *, default: float, name: str) -> float:
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}
