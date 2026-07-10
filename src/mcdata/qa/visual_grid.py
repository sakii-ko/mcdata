from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from mcdata.qa.probe import summarize_probe

_STANDARD_MOMENT_IDS = (
    "material_closeup",
    "scene_wide",
    "water_reflection",
    "motion",
)
_ROW_LABEL_WIDTH = 220
_TITLE_HEIGHT = 56
_COLUMN_HEADER_HEIGHT = 56
_HEADER_HEIGHT = _TITLE_HEIGHT + _COLUMN_HEADER_HEIGHT
_BACKGROUND = (18, 20, 24)
_PANEL = (29, 32, 38)
_TEXT = (238, 240, 244)
_MUTED_TEXT = (174, 181, 190)
_BORDER = (78, 84, 94)


class VisualGridError(RuntimeError):
    pass


@dataclass(frozen=True)
class VisualProfile:
    id: str
    label: str
    input: Path
    video: Path


@dataclass(frozen=True)
class VisualMoment:
    id: str
    label: str
    timestamp_sec: float


@dataclass(frozen=True)
class VisualGridSpec:
    title: str
    profiles: tuple[VisualProfile, ...]
    moments: tuple[VisualMoment, ...]
    cell_width: int
    cell_height: int


def write_visual_grid(spec_path: Path, out_dir: Path) -> dict[str, Any]:
    """Build a lossless, hash-bound profile-by-moment visual comparison grid."""
    spec_path = spec_path.resolve()
    out_dir = out_dir.resolve()
    spec = load_visual_grid_spec(spec_path)
    probes = _validate_videos(spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_root = out_dir / "frames"
    records: list[dict[str, Any]] = []
    images: dict[tuple[str, str], Image.Image] = {}

    for profile in spec.profiles:
        for moment in spec.moments:
            frame_path = frames_root / moment.id / f"{profile.id}.png"
            _extract_lossless_frame(profile.video, moment.timestamp_sec, frame_path)
            image = Image.open(frame_path).convert("RGB")
            images[(profile.id, moment.id)] = image.copy()
            records.append(
                {
                    "profile_id": profile.id,
                    "moment_id": moment.id,
                    "timestamp_sec": moment.timestamp_sec,
                    **_artifact(frame_path),
                    "width": image.width,
                    "height": image.height,
                }
            )

    grid_path = out_dir / "visual_comparison_grid.png"
    _compose_grid(grid_path, spec, images)
    manifest = {
        "schema_version": 1,
        "title": spec.title,
        "spec": _artifact(spec_path),
        "profiles": [
            {
                "id": profile.id,
                "label": profile.label,
                "input": str(profile.input),
                "video": _artifact(profile.video),
                "probe": probes[profile.id],
            }
            for profile in spec.profiles
        ],
        "moments": [
            {
                "id": moment.id,
                "label": moment.label,
                "timestamp_sec": moment.timestamp_sec,
            }
            for moment in spec.moments
        ],
        "frames": records,
        "outputs": {
            "grid": _artifact(grid_path),
            "frames_root": str(frames_root),
            "manifest": str(out_dir / "visual_grid_manifest.json"),
        },
    }
    manifest_path = out_dir / "visual_grid_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def load_visual_grid_spec(path: Path) -> VisualGridSpec:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualGridError(f"Cannot load visual-grid spec {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise VisualGridError("Visual-grid spec must be a JSON object")
    profiles = _parse_profiles(raw.get("profiles"), path.parent)
    moments = _parse_moments(raw.get("moments"))
    title = _required_text(raw.get("title", "Rendering comparison"), "title")
    cell_width = _bounded_int(raw.get("cell_width", 640), "cell_width", 160, 1920)
    cell_height = _bounded_int(raw.get("cell_height", 360), "cell_height", 90, 1080)
    return VisualGridSpec(title, profiles, moments, cell_width, cell_height)


def _parse_profiles(raw: object, base: Path) -> tuple[VisualProfile, ...]:
    if not isinstance(raw, list) or not raw:
        raise VisualGridError("profiles must be a non-empty list")
    profiles: list[VisualProfile] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise VisualGridError(f"profiles[{index}] must be an object")
        profile_id = _safe_id(item.get("id"), f"profiles[{index}].id")
        label = _required_text(item.get("label"), f"profiles[{index}].label")
        input_text = _required_text(item.get("input"), f"profiles[{index}].input")
        input_path = Path(input_text)
        if not input_path.is_absolute():
            input_path = base / input_path
        input_path = input_path.resolve()
        video = input_path / "capture.mp4" if input_path.is_dir() else input_path
        profiles.append(VisualProfile(profile_id, label, input_path, video))
    _reject_duplicate([profile.id for profile in profiles], "profile id")
    return tuple(profiles)


def _parse_moments(raw: object) -> tuple[VisualMoment, ...]:
    if not isinstance(raw, list):
        raise VisualGridError("moments must be a list")
    moments: list[VisualMoment] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise VisualGridError(f"moments[{index}] must be an object")
        moment_id = _safe_id(item.get("id"), f"moments[{index}].id")
        label = _required_text(item.get("label"), f"moments[{index}].label")
        try:
            timestamp = float(item.get("timestamp_sec"))
        except (TypeError, ValueError) as exc:
            raise VisualGridError(f"moments[{index}].timestamp_sec must be numeric") from exc
        if timestamp < 0:
            raise VisualGridError(f"moments[{index}].timestamp_sec must be non-negative")
        moments.append(VisualMoment(moment_id, label, round(timestamp, 3)))
    if tuple(moment.id for moment in moments) != _STANDARD_MOMENT_IDS:
        raise VisualGridError(
            "moments must use this exact order: " + ", ".join(_STANDARD_MOMENT_IDS)
        )
    _reject_duplicate([moment.timestamp_sec for moment in moments], "moment timestamp")
    return tuple(moments)


def _validate_videos(spec: VisualGridSpec) -> dict[str, dict[str, Any]]:
    probes: dict[str, dict[str, Any]] = {}
    max_timestamp = max(moment.timestamp_sec for moment in spec.moments)
    expected_size: tuple[object, object] | None = None
    for profile in spec.profiles:
        if not profile.video.is_file():
            raise VisualGridError(f"Missing capture video: {profile.video}")
        probe = summarize_probe(profile.video)
        compact = {key: probe.get(key) for key in ("codec", "width", "height", "fps", "duration_sec")}
        duration = float(compact.get("duration_sec") or 0.0)
        if max_timestamp >= duration:
            raise VisualGridError(
                f"Moment at {max_timestamp:.3f}s is outside {profile.id} duration {duration:.3f}s"
            )
        size = (compact.get("width"), compact.get("height"))
        if expected_size is None:
            expected_size = size
        elif size != expected_size:
            raise VisualGridError(
                f"Capture resolution mismatch: {profile.id} is {size}, expected {expected_size}"
            )
        probes[profile.id] = compact
    return probes


def _extract_lossless_frame(video: Path, timestamp: float, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
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
            "-pix_fmt",
            "rgb24",
            "-compression_level",
            "3",
            str(out),
        ],
        check=True,
    )


def _compose_grid(
    path: Path,
    spec: VisualGridSpec,
    images: dict[tuple[str, str], Image.Image],
) -> None:
    width = _ROW_LABEL_WIDTH + len(spec.profiles) * spec.cell_width
    height = _HEADER_HEIGHT + len(spec.moments) * spec.cell_height
    sheet = Image.new("RGB", (width, height), _BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    font = _font(18)
    small_font = _font(15)
    draw.text((14, 8), spec.title, font=font, fill=_TEXT)
    draw.text((14, 32), "same timestamp across every profile", font=small_font, fill=_MUTED_TEXT)
    for column, profile in enumerate(spec.profiles):
        x = _ROW_LABEL_WIDTH + column * spec.cell_width
        draw.rectangle(
            (x, _TITLE_HEIGHT, x + spec.cell_width - 1, _HEADER_HEIGHT - 1), fill=_PANEL
        )
        draw.text((x + 12, _TITLE_HEIGHT + 17), profile.label, font=font, fill=_TEXT)
    for row, moment in enumerate(spec.moments):
        y = _HEADER_HEIGHT + row * spec.cell_height
        draw.rectangle((0, y, _ROW_LABEL_WIDTH - 1, y + spec.cell_height - 1), fill=_PANEL)
        draw.text((14, y + 18), moment.label, font=font, fill=_TEXT)
        draw.text(
            (14, y + 48),
            f"{moment.id}\nt = {moment.timestamp_sec:.3f}s",
            font=small_font,
            fill=_MUTED_TEXT,
            spacing=6,
        )
        for column, profile in enumerate(spec.profiles):
            x = _ROW_LABEL_WIDTH + column * spec.cell_width
            cell = _letterbox(images[(profile.id, moment.id)], spec.cell_width, spec.cell_height)
            sheet.paste(cell, (x, y))
            draw.rectangle(
                (x, y, x + spec.cell_width - 1, y + spec.cell_height - 1),
                outline=_BORDER,
                width=1,
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format="PNG", compress_level=6)


def _letterbox(image: Image.Image, width: int, height: int) -> Image.Image:
    fitted = ImageOps.contain(image.convert("RGB"), (width, height))
    result = Image.new("RGB", (width, height), "black")
    result.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return result


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _artifact(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": path.stat().st_size}


def _safe_id(value: object, field: str) -> str:
    text = _required_text(value, field)
    if any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in text):
        raise VisualGridError(f"{field} may contain only lowercase a-z, 0-9, '_' and '-'")
    if not text[0].isalnum():
        raise VisualGridError(f"{field} must start with a letter or digit")
    return text


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VisualGridError(f"{field} must be a non-empty string")
    return value.strip()


def _bounded_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise VisualGridError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise VisualGridError(f"{field} must be an integer") from exc
    if result < minimum or result > maximum:
        raise VisualGridError(f"{field} must be between {minimum} and {maximum}")
    return result


def _reject_duplicate(values: list[object], label: str) -> None:
    if len(values) != len(set(values)):
        raise VisualGridError(f"Duplicate {label} in visual-grid spec")
