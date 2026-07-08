from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from mcdata.qa.frames import extract_frames_at, uniform_timestamps
from mcdata.qa.metrics import black_border_metrics, brightness_percentiles, zero_mean_ncc
from mcdata.qa.probe import summarize_probe

_BILINEAR = getattr(getattr(Image, "Resampling", Image), "BILINEAR")


def resolve_video(input_path: Path) -> tuple[Path, Path]:
    if input_path.is_dir():
        return input_path / "capture.mp4", input_path
    return input_path, input_path.parent


def write_run_report(
    input_path: Path,
    *,
    frames: int = 12,
    out_dir: Path | None = None,
    border_mean_threshold: float = 6.0,
    border_var_threshold: float = 8.0,
) -> dict[str, Any]:
    video, default_out_dir = resolve_video(input_path)
    out = out_dir or default_out_dir
    out.mkdir(parents=True, exist_ok=True)
    probe = summarize_probe(video)
    timestamps = uniform_timestamps(float(probe.get("duration_sec") or 0), frames)
    images = extract_frames_at(video, timestamps)
    frame_metrics = []
    warnings: list[str] = []
    for timestamp, image in zip(timestamps, images):
        arr = np.asarray(image)
        brightness = brightness_percentiles(arr)
        border = black_border_metrics(
            arr,
            mean_threshold=border_mean_threshold,
            var_threshold=border_var_threshold,
        )
        if border["has_black_border"]:
            warnings.append(f"black border detected near t={timestamp:.3f}s")
        if brightness["p50"] < 12:
            warnings.append(f"low median brightness near t={timestamp:.3f}s")
        frame_metrics.append({"timestamp_sec": timestamp, "brightness": brightness, "border": border})

    expected = {"fps": 24.0, "width": probe.get("width"), "height": probe.get("height")}
    if abs(float(probe.get("fps") or 0) - 24.0) > 0.01:
        warnings.append(f"fps is {probe.get('fps')}, expected 24")

    report = {
        "input": str(input_path),
        "video": str(video),
        "probe": probe,
        "expected": expected,
        "frames": frame_metrics,
        "warnings": warnings,
        "outputs": {
            "json": str(out / "qa_report.json"),
            "markdown": str(out / "qa_report.md"),
            "contact_sheet": str(out / "contact_sheet.jpg"),
        },
    }
    _write_json(out / "qa_report.json", report)
    _write_run_markdown(out / "qa_report.md", report)
    _write_contact_sheet(out / "contact_sheet.jpg", images, timestamps)
    return report


def write_compare_report(
    inputs: list[Path],
    *,
    frames: int = 12,
    out_dir: Path,
) -> dict[str, Any]:
    if len(inputs) < 2:
        raise ValueError("qa-compare requires at least two inputs")
    videos = [resolve_video(path)[0] for path in inputs]
    probes = [summarize_probe(video) for video in videos]
    duration = min(float(probe.get("duration_sec") or 0) for probe in probes)
    timestamps = uniform_timestamps(duration, frames)
    extracted = [extract_frames_at(video, timestamps) for video in videos]

    rows = []
    for index, timestamp in enumerate(timestamps):
        thumbs = [
            image.convert("L").resize((64, 36), _BILINEAR)
            for image in [frames_at_time[index] for frames_at_time in extracted]
        ]
        pair_scores = []
        for left in range(len(thumbs)):
            for right in range(left + 1, len(thumbs)):
                pair_scores.append(
                    {
                        "left": str(inputs[left]),
                        "right": str(inputs[right]),
                        "ncc": zero_mean_ncc(np.asarray(thumbs[left]), np.asarray(thumbs[right])),
                    }
                )
        rows.append({"timestamp_sec": timestamp, "pairs": pair_scores})

    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / "qa_compare_contact_sheet.jpg"
    _write_compare_sheet(image_path, extracted, timestamps)
    report = {
        "inputs": [str(path) for path in inputs],
        "videos": [str(video) for video in videos],
        "probe": probes,
        "timestamps_sec": timestamps,
        "rows": rows,
        "outputs": {
            "json": str(out_dir / "qa_compare_report.json"),
            "markdown": str(out_dir / "qa_compare_report.md"),
            "contact_sheet": str(image_path),
        },
    }
    _write_json(out_dir / "qa_compare_report.json", report)
    _write_compare_markdown(out_dir / "qa_compare_report.md", report)
    return report


def copy_report_outputs(report: dict[str, Any], dest: Path, *, prefix: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for key, path in report["outputs"].items():
        source = Path(path)
        suffix = source.suffix
        target = dest / f"{prefix}_{key}{suffix}"
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_run_markdown(path: Path, report: dict[str, Any]) -> None:
    probe = report["probe"]
    lines = [
        "# QA Run Report",
        "",
        f"- video: `{report['video']}`",
        f"- codec: `{probe.get('codec')}`",
        f"- size: `{probe.get('width')}x{probe.get('height')}`",
        f"- fps: `{probe.get('fps')}`",
        f"- duration: `{probe.get('duration_sec')}`",
        f"- warnings: `{len(report['warnings'])}`",
        "",
        "| t_sec | p5 | p50 | p95 | black_border |",
        "|---:|---:|---:|---:|---|",
    ]
    for item in report["frames"]:
        b = item["brightness"]
        lines.append(
            f"| {item['timestamp_sec']:.3f} | {b['p5']:.1f} | {b['p50']:.1f} | "
            f"{b['p95']:.1f} | {item['border']['has_black_border']} |"
        )
    if report["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_compare_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# QA Compare Report",
        "",
        *[f"- input: `{item}`" for item in report["inputs"]],
        "",
        "| t_sec | pair | ncc |",
        "|---:|---|---:|",
    ]
    for row in report["rows"]:
        for pair in row["pairs"]:
            lines.append(
                f"| {row['timestamp_sec']:.3f} | `{Path(pair['left']).name}` vs "
                f"`{Path(pair['right']).name}` | {pair['ncc']:.4f} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_contact_sheet(path: Path, images: list[Image.Image], timestamps: list[float]) -> None:
    thumb_w, thumb_h = 320, 180
    cols = 3
    rows = max(1, (len(images) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + 20)), "white")
    draw = ImageDraw.Draw(sheet)
    for idx, image in enumerate(images):
        thumb = image.resize((thumb_w, thumb_h), _BILINEAR)
        x = (idx % cols) * thumb_w
        y = (idx // cols) * (thumb_h + 20)
        sheet.paste(thumb, (x, y))
        draw.text((x + 4, y + thumb_h + 3), f"{timestamps[idx]:.2f}s", fill=(0, 0, 0))
    sheet.save(path, quality=80, optimize=True)


def _write_compare_sheet(
    path: Path,
    extracted: list[list[Image.Image]],
    timestamps: list[float],
) -> None:
    thumb_w, thumb_h = 240, 135
    label_h = 20
    cols = len(extracted)
    rows = len(timestamps)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    for row, timestamp in enumerate(timestamps):
        for col, frames in enumerate(extracted):
            thumb = frames[row].resize((thumb_w, thumb_h), _BILINEAR)
            x = col * thumb_w
            y = row * (thumb_h + label_h)
            sheet.paste(thumb, (x, y))
            draw.text((x + 4, y + thumb_h + 3), f"{timestamp:.2f}s", fill=(0, 0, 0))
    sheet.save(path, quality=80, optimize=True)
