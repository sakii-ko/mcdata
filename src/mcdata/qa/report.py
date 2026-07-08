from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from mcdata.qa.frames import extract_frames_at, uniform_timestamps
from mcdata.qa.metrics import black_border_metrics, brightness_percentiles, zero_mean_ncc
from mcdata.qa.probe import summarize_probe
from mcdata.qa.route import distance_xz, interpolate_track, simulate_track, yaw_error_deg

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
    route_reference = route_reference_report(default_out_dir)
    if route_reference and not route_reference.get("passed"):
        warnings.append("route reference check failed")

    report = {
        "input": str(input_path),
        "video": str(video),
        "probe": probe,
        "expected": expected,
        "route_reference": route_reference,
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


def route_reference_report(run_dir: Path) -> dict[str, Any] | None:
    positions_path = run_dir / "positions.jsonl"
    trajectory_path = run_dir / "trajectory.json"
    if not positions_path.exists() or not trajectory_path.exists():
        return None
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if not trajectory.get("route"):
        return None
    positions = _read_positions(run_dir)
    if positions is None:
        return None
    try:
        ideal_track = simulate_track(trajectory)
    except ValueError as exc:
        return {"passed": False, "error": str(exc)}
    return check_route_reference(positions, ideal_track)


def check_route_reference(
    positions: list[dict[str, float]],
    ideal_track: list[dict[str, float]],
    *,
    max_dev: float = 3.0,
    max_yaw_dev_deg: float = 10.0,
    min_y: float = 63.0,
    max_y: float = 66.0,
) -> dict[str, Any]:
    samples = []
    yaw_errors: list[float] = []
    missing_yaw_count = 0
    for position in positions:
        t_rel = position.get("t_rel")
        if t_rel is None or t_rel < 0:
            continue
        ideal = interpolate_track(ideal_track, t_rel)
        deviation = distance_xz(position, ideal)
        y = float(position["y"])
        sample = {
            "idx": position.get("idx"),
            "t_rel": t_rel,
            "x": position["x"],
            "y": y,
            "z": position["z"],
            "ideal_x": ideal["x"],
            "ideal_z": ideal["z"],
            "deviation_blocks": deviation,
            "y_in_range": min_y <= y <= max_y,
        }
        if "yaw" in ideal:
            if "yaw" in position:
                yaw_error = yaw_error_deg(position["yaw"], ideal["yaw"])
                yaw_errors.append(yaw_error)
                sample.update(
                    {
                        "yaw": position["yaw"],
                        "ideal_yaw": ideal["yaw"],
                        "yaw_error_deg": yaw_error,
                    }
                )
            else:
                missing_yaw_count += 1
                sample["ideal_yaw"] = ideal["yaw"]
        samples.append(sample)
    deviations = [sample["deviation_blocks"] for sample in samples]
    y_values = [sample["y"] for sample in samples]
    y_out_of_range = sum(1 for sample in samples if not sample["y_in_range"])
    max_deviation = max(deviations) if deviations else None
    mean_deviation = sum(deviations) / len(deviations) if deviations else None
    max_yaw_error = max(yaw_errors) if yaw_errors else None
    mean_yaw_error = sum(yaw_errors) / len(yaw_errors) if yaw_errors else None
    return {
        "threshold_blocks": max_dev,
        "yaw_threshold_degrees": max_yaw_dev_deg,
        "y_min": min_y,
        "y_max": max_y,
        "count": len(samples),
        "max_deviation_blocks": max_deviation,
        "mean_deviation_blocks": mean_deviation,
        "max_yaw_error_degrees": max_yaw_error,
        "mean_yaw_error_degrees": mean_yaw_error,
        "yaw_sample_count": len(yaw_errors),
        "missing_yaw_count": missing_yaw_count,
        "observed_y_min": min(y_values) if y_values else None,
        "observed_y_max": max(y_values) if y_values else None,
        "y_out_of_range_count": y_out_of_range,
        "passed": (
            max_deviation is not None
            and max_deviation <= max_dev
            and missing_yaw_count == 0
            and (max_yaw_error is None or max_yaw_error <= max_yaw_dev_deg)
            and y_out_of_range == 0
        ),
        "samples": samples,
    }


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
    position_alignment = compare_position_alignment(inputs)
    report = {
        "inputs": [str(path) for path in inputs],
        "videos": [str(video) for video in videos],
        "probe": probes,
        "timestamps_sec": timestamps,
        "rows": rows,
        "position_alignment": position_alignment,
        "outputs": {
            "json": str(out_dir / "qa_compare_report.json"),
            "markdown": str(out_dir / "qa_compare_report.md"),
            "contact_sheet": str(image_path),
        },
    }
    _write_json(out_dir / "qa_compare_report.json", report)
    _write_compare_markdown(out_dir / "qa_compare_report.md", report)
    return report


def compare_position_alignment(
    inputs: list[Path],
    *,
    max_threshold_blocks: float = 2.0,
) -> dict[str, Any] | None:
    series = [_read_positions(path) for path in inputs]
    if any(items is None for items in series):
        return None
    assert all(items is not None for items in series)
    pair_results = []
    for left in range(len(series)):
        for right in range(left + 1, len(series)):
            left_items = series[left] or []
            right_items = series[right] or []
            count = min(len(left_items), len(right_items))
            distances = [
                _position_distance(left_items[idx], right_items[idx])
                for idx in range(count)
            ]
            max_distance = max(distances) if distances else None
            mean_distance = sum(distances) / len(distances) if distances else None
            pair_results.append(
                {
                    "left": str(inputs[left]),
                    "right": str(inputs[right]),
                    "count": count,
                    "max_distance_blocks": max_distance,
                    "mean_distance_blocks": mean_distance,
                    "passed": max_distance is not None and max_distance <= max_threshold_blocks,
                }
            )
    overall_max = max(
        (item["max_distance_blocks"] for item in pair_results if item["max_distance_blocks"] is not None),
        default=None,
    )
    total_count = sum(int(item["count"]) for item in pair_results)
    total_distance = sum(
        float(item["mean_distance_blocks"]) * int(item["count"])
        for item in pair_results
        if item["mean_distance_blocks"] is not None
    )
    overall_mean = total_distance / total_count if total_count else None
    return {
        "threshold_blocks": max_threshold_blocks,
        "passed": overall_max is not None and overall_max <= max_threshold_blocks,
        "max_distance_blocks": overall_max,
        "mean_distance_blocks": overall_mean,
        "pairs": pair_results,
    }


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
    ]
    route_reference = report.get("route_reference")
    if route_reference:
        status = "PASS" if route_reference.get("passed") else "FAIL"
        lines.extend(
            [
                f"- route_reference: `{status}`",
                "- route_max_deviation_blocks: "
                f"`{_format_optional_float(route_reference.get('max_deviation_blocks'))}`",
                "- route_mean_deviation_blocks: "
                f"`{_format_optional_float(route_reference.get('mean_deviation_blocks'))}`",
                f"- route_threshold_blocks: `{route_reference.get('threshold_blocks')}`",
                "- route_max_yaw_error_degrees: "
                f"`{_format_optional_float(route_reference.get('max_yaw_error_degrees'))}`",
                "- route_mean_yaw_error_degrees: "
                f"`{_format_optional_float(route_reference.get('mean_yaw_error_degrees'))}`",
                f"- route_yaw_threshold_degrees: `{route_reference.get('yaw_threshold_degrees')}`",
                f"- route_yaw_sample_count: `{route_reference.get('yaw_sample_count')}`",
                f"- route_missing_yaw_count: `{route_reference.get('missing_yaw_count')}`",
                f"- route_y_range: `{route_reference.get('y_min')}..{route_reference.get('y_max')}`",
                f"- route_y_out_of_range_count: `{route_reference.get('y_out_of_range_count')}`",
                "",
            ]
        )
    lines.extend(
        [
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
    )
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
    ]
    alignment = report.get("position_alignment")
    if alignment:
        status = "PASS" if alignment.get("passed") else "FAIL"
        max_distance = alignment.get("max_distance_blocks")
        max_text = "n/a" if max_distance is None else f"{float(max_distance):.3f}"
        lines.extend(
            [
                f"- position_alignment: `{status}`",
                f"- position_max_distance_blocks: `{max_text}`",
                f"- position_mean_distance_blocks: `{_format_optional_float(alignment.get('mean_distance_blocks'))}`",
                f"- position_threshold_blocks: `{alignment.get('threshold_blocks')}`",
                "",
            ]
        )
    lines.extend(
        [
            *[f"- input: `{item}`" for item in report["inputs"]],
            "",
            "| t_sec | pair | ncc |",
            "|---:|---|---:|",
        ]
    )
    for row in report["rows"]:
        for pair in row["pairs"]:
            lines.append(
                f"| {row['timestamp_sec']:.3f} | `{Path(pair['left']).name}` vs "
                f"`{Path(pair['right']).name}` | {pair['ncc']:.4f} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_positions(input_path: Path) -> list[dict[str, float]] | None:
    path = input_path / "positions.jsonl" if input_path.is_dir() else input_path.parent / "positions.jsonl"
    if not path.exists():
        return None
    items: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item = {"x": float(row["x"]), "y": float(row["y"]), "z": float(row["z"])}
        if "idx" in row:
            item["idx"] = int(row["idx"])
        if "t_rel" in row:
            item["t_rel"] = float(row["t_rel"])
        if "yaw" in row:
            item["yaw"] = float(row["yaw"])
        items.append(item)
    return items


def _format_optional_float(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f}"


def _position_distance(left: dict[str, float], right: dict[str, float]) -> float:
    return math.sqrt(
        (left["x"] - right["x"]) ** 2
        + (left["y"] - right["y"]) ** 2
        + (left["z"] - right["z"]) ** 2
    )


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
