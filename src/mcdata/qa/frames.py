from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image


def extract_frames(video: Path, *, count: int, duration_sec: float | None = None) -> list[Image.Image]:
    timestamps = uniform_timestamps(duration_sec or _probe_duration(video), count)
    return extract_frames_at(video, timestamps)


def extract_frames_at(video: Path, timestamps: list[float]) -> list[Image.Image]:
    frames: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="mcdata-qa-frames-") as tmp:
        tmpdir = Path(tmp)
        for index, timestamp in enumerate(timestamps):
            out = tmpdir / f"frame_{index:04d}.jpg"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(out),
                ],
                check=True,
            )
            frames.append(Image.open(out).convert("RGB").copy())
    return frames


def uniform_timestamps(duration_sec: float, count: int) -> list[float]:
    if count <= 0:
        raise ValueError("frame count must be positive")
    if duration_sec <= 0:
        return [0.0 for _ in range(count)]
    step = duration_sec / (count + 1)
    return [round(step * (idx + 1), 3) for idx in range(count)]


def _probe_duration(video: Path) -> float:
    from mcdata.qa.probe import summarize_probe

    return float(summarize_probe(video).get("duration_sec") or 0)
