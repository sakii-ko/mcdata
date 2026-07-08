from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any


def probe_video(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def summarize_probe(path: Path) -> dict[str, Any]:
    raw = probe_video(path)
    video_stream = next(
        (stream for stream in raw.get("streams", []) if stream.get("codec_type") == "video"),
        raw.get("streams", [{}])[0] if raw.get("streams") else {},
    )
    duration = video_stream.get("duration") or raw.get("format", {}).get("duration")
    fps = _fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
    return {
        "codec": video_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": fps,
        "duration_sec": float(duration) if duration is not None else None,
        "raw": raw,
    }


def _fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return None
