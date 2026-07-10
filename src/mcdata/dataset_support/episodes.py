from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcdata.dataset_support.core import (
    DatasetValidationError,
    artifact,
    load_json,
    relative_path,
    require_hash,
    require_mapping,
    require_nonempty_string,
    shader_runtime,
    validate_report_evidence,
)


def _require_close(actual: Any, expected: float, label: str, tolerance: float = 0.01) -> None:
    try:
        numeric = float(actual)
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError(f"Expected numeric {label}, got {actual!r}") from exc
    if not math.isfinite(numeric) or abs(numeric - expected) > tolerance:
        raise DatasetValidationError(f"Expected {label}={expected}, got {numeric}")


def _frame_count(manifest: dict[str, Any]) -> int:
    capture = require_mapping(manifest.get("capture"), "manifest.capture")
    ffprobe = require_mapping(capture.get("ffprobe"), "manifest.capture.ffprobe")
    streams = ffprobe.get("streams")
    if not isinstance(streams, list) or not streams:
        raise DatasetValidationError("manifest.capture.ffprobe.streams is empty")
    stream = require_mapping(streams[0], "manifest.capture.ffprobe.streams[0]")
    try:
        return int(stream.get("nb_frames"))
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError("Video stream has no integer nb_frames") from exc


def _validate_capture(
    manifest: dict[str, Any],
    qa: dict[str, Any],
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> dict[str, Any]:
    capture = require_mapping(manifest.get("capture"), "manifest.capture")
    settings = require_mapping(capture.get("settings"), "manifest.capture.settings")
    probe = require_mapping(qa.get("probe"), "qa_report.probe")
    ffprobe = require_mapping(capture.get("ffprobe"), "manifest.capture.ffprobe")
    streams = ffprobe.get("streams")
    if not isinstance(streams, list) or not streams:
        raise DatasetValidationError("Manifest ffprobe has no video stream")
    stream = require_mapping(streams[0], "manifest.capture.ffprobe.streams[0]")
    if capture.get("enabled") is not True:
        raise DatasetValidationError("Capture was not enabled")
    for source, label in ((settings, "capture settings"), (probe, "QA probe")):
        if source.get("width") != width or source.get("height") != height:
            raise DatasetValidationError(
                f"Expected {label} size {width}x{height}, got "
                f"{source.get('width')}x{source.get('height')}"
            )
        _require_close(source.get("fps"), fps, f"{label} fps")
    if stream.get("width") != width or stream.get("height") != height:
        raise DatasetValidationError(
            f"Expected manifest ffprobe size {width}x{height}, got "
            f"{stream.get('width')}x{stream.get('height')}"
        )
    rate = stream.get("avg_frame_rate")
    if rate != f"{int(fps)}/1":
        raise DatasetValidationError(f"Expected manifest ffprobe fps={fps}, got {rate!r}")
    _require_close(probe.get("duration_sec"), duration, "QA probe duration", tolerance=0.05)
    frames = _frame_count(manifest)
    expected_frames = round(duration * fps)
    if frames != expected_frames:
        raise DatasetValidationError(f"Expected {expected_frames} frames, got {frames}")
    return {
        "codec": probe.get("codec"),
        "width": width,
        "height": height,
        "fps": float(probe["fps"]),
        "duration_sec": float(probe["duration_sec"]),
        "frame_count": frames,
    }


def _validate_qa(qa: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    warnings = qa.get("warnings")
    if warnings != []:
        raise DatasetValidationError(f"QA warnings are not empty for {run_dir.name}: {warnings!r}")
    route = require_mapping(qa.get("route_reference"), "qa_report.route_reference")
    if route.get("passed") is not True:
        raise DatasetValidationError(f"Route QA did not pass for {run_dir.name}")
    count = route.get("count")
    threshold = route.get("threshold_blocks")
    maximum = route.get("max_deviation_blocks")
    if (
        not isinstance(count, int)
        or count <= 0
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or threshold <= 0
        or threshold > 3.0
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
        or maximum > threshold
        or route.get("y_out_of_range_count") != 0
    ):
        raise DatasetValidationError(f"Route QA evidence is incomplete for {run_dir.name}")
    if route.get("mode") == "online_position_yaw_feedback" and (
        route.get("failure_count") != 0
        or not isinstance(route.get("navigation_control_count"), int)
        or route.get("navigation_control_count") <= 0
        or not isinstance(route.get("movement_distance_blocks"), (int, float))
        or route.get("movement_distance_blocks") <= 0
        or not isinstance(route.get("navigation_duration_ratio"), (int, float))
        or route.get("navigation_duration_ratio") < 0.95
        or route.get("terminal_stop") is not True
        or route.get("route_progress_ordered") is not True
        or not isinstance(route.get("ordered_route_progress_blocks"), (int, float))
        or not isinstance(route.get("minimum_route_progress_blocks"), (int, float))
        or route.get("ordered_route_progress_blocks") < route.get("minimum_route_progress_blocks")
        or not isinstance(route.get("position_duration_ratio"), (int, float))
        or route.get("position_duration_ratio") < 0.95
    ):
        raise DatasetValidationError(f"Feedback navigation QA is incomplete for {run_dir.name}")
    return {
        "passed": True,
        "warnings": [],
        "route_max_deviation_blocks": route.get("max_deviation_blocks"),
        "route_mean_deviation_blocks": route.get("mean_deviation_blocks"),
        "route_max_yaw_error_degrees": route.get("max_yaw_error_degrees"),
    }


def _validate_manifest(manifest: dict[str, Any], run_dir: Path) -> None:
    profile = require_mapping(manifest.get("profile"), "manifest.profile")
    git = require_mapping(manifest.get("git"), "manifest.git")
    resources = require_mapping(manifest.get("resources"), "manifest.resources")
    world = require_mapping(manifest.get("world"), "manifest.world")
    trajectory = require_mapping(manifest.get("trajectory"), "manifest.trajectory")
    runtime = require_mapping(
        resources.get("resourcepack_runtime"), "manifest.resources.resourcepack_runtime"
    )
    require_nonempty_string(profile.get("name"), "profile name")
    require_nonempty_string(manifest.get("mc_version"), "Minecraft version")
    require_nonempty_string(world.get("profile"), "world profile")
    if world.get("seed") is None or not require_mapping(world.get("state"), "world state"):
        raise DatasetValidationError(f"World provenance is incomplete: {run_dir.name}")
    require_hash(trajectory.get("sha256"), 64, "trajectory sha256")
    if manifest.get("error") is not None or not manifest.get("ended_at"):
        raise DatasetValidationError(f"Run did not finish cleanly: {run_dir.name}")
    if (
        not git.get("commit")
        or git.get("dirty") is not False
        or git.get("source") != "sync_commit"
        or git.get("status_porcelain") != []
    ):
        raise DatasetValidationError(f"Run git provenance is dirty or missing: {run_dir.name}")
    if runtime.get("status") != "pass":
        raise DatasetValidationError(f"Resource-pack runtime gate did not pass: {run_dir.name}")
    if runtime.get("actual_file_packs") != runtime.get("expected_file_packs"):
        raise DatasetValidationError(f"Resource-pack runtime set/order mismatch: {run_dir.name}")
    for key in ("missing_file_packs", "unexpected_file_packs", "duplicate_file_packs"):
        if runtime.get(key) != []:
            raise DatasetValidationError(
                f"Resource-pack runtime {key} is not empty: {run_dir.name}"
            )


def _resource_summary(items: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise DatasetValidationError(f"Expected {label} to be a list")
    summaries = []
    for item in items:
        resource = require_mapping(item, f"{label} entry")
        filename = require_nonempty_string(resource.get("filename"), f"{label} filename")
        if Path(filename).name != filename or "," in filename:
            raise DatasetValidationError(f"Unsafe {label} filename: {filename!r}")
        digest = require_hash(resource.get("sha256"), 64, f"{label} sha256")
        size = resource.get("size_bytes")
        if not isinstance(size, int) or size <= 0:
            raise DatasetValidationError(f"Expected positive {label} size_bytes")
        summaries.append({"filename": filename, "sha256": digest, "size_bytes": size})
    return summaries


def _resolution_pack_summary(pack: dict[str, Any], resource: dict[str, Any]) -> dict[str, Any]:
    filename = require_nonempty_string(pack.get("filename"), "resolution filename")
    if filename != resource["filename"]:
        raise DatasetValidationError("Resourcepack resolution filename/order mismatch")
    effective = require_hash(pack.get("effective_sha256"), 64, "effective sha256")
    if effective != resource["sha256"]:
        raise DatasetValidationError(f"Effective resourcepack hash mismatch: {filename}")
    source_sha512 = require_hash(pack.get("source_sha512"), 128, "source sha512")
    upstream_sha512 = require_hash(pack.get("upstream_sha512"), 128, "upstream sha512")
    if source_sha512 != upstream_sha512:
        raise DatasetValidationError(f"Upstream/source resourcepack hash mismatch: {filename}")
    expected_size = pack.get("expected_size")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise DatasetValidationError(f"Invalid upstream size for resourcepack: {filename}")
    normalized = pack.get("normalized")
    if not isinstance(normalized, bool):
        raise DatasetValidationError(f"Missing normalization decision for resourcepack: {filename}")
    download_url = require_nonempty_string(pack.get("download_url"), "download URL")
    if not download_url.startswith("https://"):
        raise DatasetValidationError(f"Unsafe resourcepack download URL: {download_url}")
    return {
        "filename": filename,
        "project": require_nonempty_string(pack.get("project"), "resourcepack project"),
        "version": require_nonempty_string(pack.get("version"), "resourcepack version"),
        "download_url": download_url,
        "expected_size": expected_size,
        "normalized": normalized,
        "source_sha256": require_hash(pack.get("source_sha256"), 64, "source sha256"),
        "source_sha512": source_sha512,
        "upstream_sha512": upstream_sha512,
        "effective_sha256": effective,
        "before": require_mapping(pack.get("before"), "resolution before"),
        "after": require_mapping(pack.get("after"), "resolution after"),
    }


def _resourcepack_resolution(
    resolution_value: Any,
    resourcepacks: list[dict[str, Any]],
    mc_version: str,
) -> dict[str, Any]:
    resolution = require_mapping(resolution_value, "resourcepack_resolution")
    if resolution.get("schema_version") != 1 or resolution.get("game_version") != mc_version:
        raise DatasetValidationError("Resourcepack resolution schema/game version mismatch")
    target = require_mapping(resolution.get("target"), "resourcepack resolution target")
    major, minor = target.get("resource_major"), target.get("resource_minor")
    if not isinstance(major, int) or not isinstance(minor, int):
        raise DatasetValidationError("Resourcepack target format is missing")
    target_summary = {
        "resource_major": major,
        "resource_minor": minor,
        "source_jar_sha256": require_hash(
            target.get("source_jar_sha256"), 64, "target client jar sha256"
        ),
    }
    packs = resolution.get("packs")
    if not isinstance(packs, list) or len(packs) != len(resourcepacks):
        raise DatasetValidationError("Resourcepack resolution count mismatch")
    resources_by_name = {item["filename"]: item for item in resourcepacks}
    if len(resources_by_name) != len(resourcepacks):
        raise DatasetValidationError("Resourcepack filenames are not unique")
    summaries = []
    for pack_value in packs:
        pack = require_mapping(pack_value, "resolution pack")
        filename = require_nonempty_string(pack.get("filename"), "resolution filename")
        if filename not in resources_by_name:
            raise DatasetValidationError(f"Unknown resourcepack resolution entry: {filename}")
        summaries.append(_resolution_pack_summary(pack, resources_by_name[filename]))
    if {item["filename"] for item in summaries} != set(resources_by_name):
        raise DatasetValidationError("Resourcepack resolution filename set mismatch")
    return {
        "schema_version": 1,
        "game_version": mc_version,
        "normalizer_version": require_nonempty_string(
            resolution.get("normalizer_version"), "normalizer version"
        ),
        "target": target_summary,
        "packs": summaries,
    }


def _episode_evidence(
    root: Path,
    run_dir: Path,
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    manifest_path = run_dir / "manifest.json"
    qa_path = run_dir / "qa_report.json"
    manifest = load_json(manifest_path)
    qa = load_json(qa_path)
    _validate_manifest(manifest, run_dir)
    if manifest.get("schema_version") != 2:
        raise DatasetValidationError(f"Unsupported manifest schema in {run_dir.name}")
    evidence = {
        "manifest": artifact(root, manifest_path),
        "video": artifact(root, run_dir / "capture.mp4"),
        "trajectory": artifact(root, run_dir / "trajectory.json"),
        "positions": artifact(root, run_dir / "positions.jsonl"),
        "client_log": artifact(root, run_dir / "client_latest.log"),
    }
    execution_mode = manifest.get("trajectory", {}).get("execution_mode", "open_loop_event_replay")
    if execution_mode == "online_position_yaw_feedback":
        evidence["navigation"] = artifact(root, run_dir / "navigation_log.jsonl")
    video_facts = _validate_capture(
        manifest, qa, width=width, height=height, fps=fps, duration=duration
    )
    qa_facts = _validate_qa(qa, run_dir)
    validate_report_evidence(
        qa.get("evidence"),
        {
            key: evidence[key]
            for key in ("manifest", "video", "trajectory", "positions", "navigation")
            if key in evidence
        },
        f"QA report for {run_dir.name}",
    )
    return manifest, evidence, video_facts, qa_facts


def _trajectory_facts(
    manifest: dict[str, Any], trajectory_artifact: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    trajectory = require_mapping(manifest.get("trajectory"), "manifest.trajectory")
    if trajectory_artifact["sha256"] != trajectory.get("sha256"):
        raise DatasetValidationError(f"Trajectory hash mismatch for {run_dir.name}")
    event_count = trajectory.get("event_count")
    duration = trajectory.get("duration_sec")
    execution_mode = trajectory.get("execution_mode", "open_loop_event_replay")
    route_point_count = trajectory.get("route_point_count", 0)
    if execution_mode not in {"open_loop_event_replay", "online_position_yaw_feedback"}:
        raise DatasetValidationError(f"Invalid trajectory execution mode for {run_dir.name}")
    if not isinstance(event_count, int) or event_count < 0:
        raise DatasetValidationError(f"Invalid trajectory event count for {run_dir.name}")
    if execution_mode == "open_loop_event_replay" and event_count <= 0:
        raise DatasetValidationError(f"Open-loop trajectory has no events for {run_dir.name}")
    if execution_mode == "online_position_yaw_feedback" and (
        event_count != 0 or not isinstance(route_point_count, int) or route_point_count < 2
    ):
        raise DatasetValidationError(f"Feedback trajectory contract is invalid for {run_dir.name}")
    if (
        not isinstance(duration, (int, float))
        or not math.isfinite(float(duration))
        or duration <= 0
    ):
        raise DatasetValidationError(f"Invalid trajectory duration for {run_dir.name}")
    return {
        **trajectory_artifact,
        "event_count": event_count,
        "duration_sec": duration,
        "execution_mode": execution_mode,
        "route_point_count": route_point_count,
    }


def _episode_resources(manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    resources = require_mapping(manifest["resources"], "manifest.resources")
    runtime = require_mapping(resources["resourcepack_runtime"], "resourcepack_runtime")
    mods = _resource_summary(resources.get("mods"), "mods")
    resourcepacks = _resource_summary(resources.get("resourcepacks"), "resourcepacks")
    shaderpacks = _resource_summary(resources.get("shaderpacks"), "shaderpacks")
    resolution = _resourcepack_resolution(
        resources.get("resourcepack_resolution"), resourcepacks, manifest["mc_version"]
    )
    expected_file_packs = [f"file/{item['filename']}" for item in resolution["packs"]]
    if runtime.get("expected_file_packs") != expected_file_packs:
        raise DatasetValidationError(f"Runtime/resource resolution mismatch for {run_dir.name}")
    return {
        "runtime": runtime,
        "mods": mods,
        "resourcepacks": resourcepacks,
        "shaderpacks": shaderpacks,
        "resolution": resolution,
        "shader_runtime": shader_runtime(run_dir / "client_latest.log", shaderpacks),
    }


def episode_from_run(
    root: Path,
    run_dir: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: float,
    expected_duration: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, evidence, video_facts, qa_facts = _episode_evidence(
        root,
        run_dir,
        width=expected_width,
        height=expected_height,
        fps=expected_fps,
        duration=expected_duration,
    )
    trajectory_facts = _trajectory_facts(manifest, evidence["trajectory"], run_dir)
    profile = require_mapping(manifest["profile"], "manifest.profile")
    resources = _episode_resources(manifest, run_dir)
    episode = {
        "episode_id": manifest.get("run_id"),
        "run_id": manifest.get("run_id"),
        "profile_name": profile.get("name"),
        "asset_set": require_nonempty_string(profile.get("asset_set"), "asset set"),
        "run_dir": relative_path(root, run_dir),
        "manifest": {**evidence["manifest"], "schema_version": manifest.get("schema_version")},
        "video": {**evidence["video"], **video_facts},
        "trajectory": trajectory_facts,
        "positions": evidence["positions"],
        "qa": {**artifact(root, run_dir / "qa_report.json"), **qa_facts},
        "client_log": evidence["client_log"],
        "shader_runtime": resources["shader_runtime"],
        "resourcepack_runtime_status": resources["runtime"].get("status"),
        "mods": resources["mods"],
        "resourcepacks": resources["resourcepacks"],
        "shaderpacks": resources["shaderpacks"],
        "resourcepack_resolution": resources["resolution"],
        "accepted": True,
    }
    if "navigation" in evidence:
        episode["navigation"] = evidence["navigation"]
    return episode, manifest


def _single_value(manifests: Sequence[dict[str, Any]], getter: Any, label: str) -> Any:
    values = [getter(manifest) for manifest in manifests]
    first = values[0]
    if any(value != first for value in values[1:]):
        raise DatasetValidationError(f"Dataset invariant differs across runs: {label}")
    return first


def global_invariants(
    manifests: Sequence[dict[str, Any]],
    *,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> dict[str, Any]:
    target = _single_value(
        manifests,
        lambda item: {
            key: item.get("resources", {})
            .get("resourcepack_resolution", {})
            .get("target", {})
            .get(key)
            for key in ("resource_major", "resource_minor", "source_jar_sha256")
        },
        "resourcepack target",
    )
    return {
        "mc_version": _single_value(manifests, lambda item: item.get("mc_version"), "mc_version"),
        "git_commit": _single_value(
            manifests, lambda item: item.get("git", {}).get("commit"), "git commit"
        ),
        "trajectory_sha256": _single_value(
            manifests, lambda item: item.get("trajectory", {}).get("sha256"), "trajectory sha256"
        ),
        "world_seed": _single_value(
            manifests, lambda item: item.get("world", {}).get("seed"), "world seed"
        ),
        "world_profile": _single_value(
            manifests, lambda item: item.get("world", {}).get("profile"), "world profile"
        ),
        "trajectory_event_count": _single_value(
            manifests, lambda item: item.get("trajectory", {}).get("event_count"), "event count"
        ),
        "trajectory_execution_mode": _single_value(
            manifests,
            lambda item: item.get("trajectory", {}).get("execution_mode", "open_loop_event_replay"),
            "trajectory execution mode",
        ),
        "trajectory_duration_sec": _single_value(
            manifests,
            lambda item: item.get("trajectory", {}).get("duration_sec"),
            "trajectory duration",
        ),
        "resourcepack_target": {
            "resource_major": target["resource_major"],
            "resource_minor": target["resource_minor"],
            "source_jar_sha256": require_hash(
                target["source_jar_sha256"], 64, "target client jar sha256"
            ),
        },
        "capture": {
            "width": width,
            "height": height,
            "fps": fps,
            "duration_sec": duration,
            "frame_count": round(fps * duration),
        },
    }


def load_episodes(
    root: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: float,
    expected_duration: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run_dirs = sorted(path.parent for path in root.glob("*/manifest.json"))
    if not run_dirs:
        raise DatasetValidationError(f"No direct-child run directories found in {root}")
    pairs = [
        episode_from_run(
            root,
            run_dir,
            expected_width=expected_width,
            expected_height=expected_height,
            expected_fps=expected_fps,
            expected_duration=expected_duration,
        )
        for run_dir in run_dirs
    ]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]
