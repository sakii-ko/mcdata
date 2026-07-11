from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from mcdata.action_source import (
    ActionSourceError,
    validate_action_source_record,
    validate_curriculum_binding,
    validate_external_rollout_binding,
    validate_native_trace_ref,
)
from mcdata.action_trace import ActionTraceError, CameraCalibration

TARGET_MINECRAFT_VERSION = "26.2"
_TRAJECTORY_FIELDS = {
    "type",
    "duration_sec",
    "events",
    "native_trace",
    "action_source",
    "camera_calibration",
    "curriculum_binding",
    "external_rollout_binding",
}
_L1_KEYS = {"w", "a", "s", "d", "left_shift", "left_control"}


class ReferenceReplayError(ValueError):
    """Raised when an imported rollout is not a safe MC26.2 reference replay."""


def validate_reference_replay_trajectory(
    value: Any,
    *,
    target_profile: str | None = None,
    target_minecraft_version: str | None = None,
) -> dict[str, Any]:
    """Validate the exact fail-closed Phase 2 reference-replay contract."""
    if not isinstance(value, Mapping) or set(value) != _TRAJECTORY_FIELDS:
        raise ReferenceReplayError(
            "reference replay trajectory has an unstable top-level field set"
        )
    if value.get("type") != "native_action_trace_replay":
        raise ReferenceReplayError(
            "reference replay type must be native_action_trace_replay"
        )
    try:
        source = validate_action_source_record(value.get("action_source"))
        native_trace = validate_native_trace_ref(value.get("native_trace"))
        curriculum = validate_curriculum_binding(value.get("curriculum_binding"))
        rollout = validate_external_rollout_binding(
            value.get("external_rollout_binding")
        )
    except ActionSourceError as exc:
        raise ReferenceReplayError(str(exc)) from exc
    if source != {
        "taxonomy_version": 1,
        "id": "learned_visual_policy",
        "provenance": "declared",
    }:
        raise ReferenceReplayError(
            "reference replay action_source must be a declared learned_visual_policy"
        )
    if native_trace["tick_rate_hz"] != 20:
        raise ReferenceReplayError("reference replay native trace must be exactly 20 Hz")
    if curriculum != {
        "status": "l1_candidate",
        "has_jump_input": False,
        "has_use_input": False,
        "has_attack_input": False,
    }:
        raise ReferenceReplayError(
            "reference replay curriculum must be a neutral L1 candidate"
        )
    calibration = validate_compiled_camera_calibration(value.get("camera_calibration"))
    if calibration["artifact_sha256"] != rollout["camera_calibration_sha256"]:
        raise ReferenceReplayError(
            "camera calibration SHA disagrees with external rollout binding"
        )
    if target_profile is not None and rollout["target_client_profile"] != target_profile:
        raise ReferenceReplayError(
            "external rollout target_client_profile does not match the selected profile"
        )
    if (
        target_minecraft_version is not None
        and target_minecraft_version != TARGET_MINECRAFT_VERSION
    ):
        raise ReferenceReplayError(
            f"reference replay requires Minecraft {TARGET_MINECRAFT_VERSION}"
        )
    _validate_l1_events(value.get("events"), value.get("duration_sec"))
    return dict(value)


def validate_compiled_camera_calibration(value: Any) -> dict[str, Any]:
    fields = {
        "calibration_id",
        "artifact_sha256",
        "yaw_pixels_per_degree",
        "pitch_pixels_per_degree",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ReferenceReplayError(
            "camera_calibration must contain the exact compiled calibration fields"
        )
    if not isinstance(value["calibration_id"], str) or not value["calibration_id"].strip():
        raise ReferenceReplayError("camera_calibration calibration_id must be non-empty")
    try:
        return CameraCalibration(
            calibration_id=value["calibration_id"],
            artifact_sha256=value["artifact_sha256"],
            yaw_pixels_per_degree=value["yaw_pixels_per_degree"],
            pitch_pixels_per_degree=value["pitch_pixels_per_degree"],
        ).as_dict()
    except (ActionTraceError, TypeError, ValueError) as exc:
        raise ReferenceReplayError(f"invalid camera_calibration: {exc}") from exc


def _validate_l1_events(events: Any, duration: Any) -> None:
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or float(duration) <= 0
    ):
        raise ReferenceReplayError("reference replay duration_sec must be finite and positive")
    tick_count_float = float(duration) * 20
    tick_count = round(tick_count_float)
    if tick_count < 1 or not math.isclose(
        tick_count_float, tick_count, rel_tol=0, abs_tol=1e-9
    ):
        raise ReferenceReplayError("reference replay duration must align to 20 Hz ticks")
    if not isinstance(events, list) or not events:
        raise ReferenceReplayError("reference replay must contain at least one L1 event")

    held: set[str] = set()
    camera_ticks: set[int] = set()
    previous_tick = -1
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ReferenceReplayError(f"reference replay event {index} is not an object")
        common = {"t", "native_trace_tick"}
        is_key = "key" in event or "action" in event
        is_camera = "mouse_dx" in event or "mouse_dy" in event or "duration" in event
        expected = (
            common | {"key", "action"}
            if is_key and not is_camera
            else common | {"mouse_dx", "mouse_dy", "duration"}
        )
        if is_key == is_camera or set(event) != expected:
            raise ReferenceReplayError(
                f"reference replay event {index} is not a canonical L1 input"
            )
        tick = event.get("native_trace_tick")
        if (
            not isinstance(tick, int)
            or isinstance(tick, bool)
            or tick < 0
            or tick > tick_count
            or tick < previous_tick
        ):
            raise ReferenceReplayError(
                f"reference replay event {index} has an invalid native_trace_tick"
            )
        previous_tick = tick
        timestamp = event.get("t")
        if (
            not isinstance(timestamp, (int, float))
            or isinstance(timestamp, bool)
            or not math.isfinite(float(timestamp))
            or not math.isclose(float(timestamp), tick / 20, rel_tol=0, abs_tol=1e-9)
        ):
            raise ReferenceReplayError(
                f"reference replay event {index} is not aligned to its native tick"
            )
        if is_key:
            _validate_key_event(event, index=index, held=held)
        else:
            _validate_camera_event(event, index=index, camera_ticks=camera_ticks, tick=tick)
    if held:
        raise ReferenceReplayError("reference replay does not release all held L1 keys")


def _validate_key_event(
    event: Mapping[str, Any], *, index: int, held: set[str]
) -> None:
    key = event.get("key")
    action = event.get("action")
    if key not in _L1_KEYS or action not in {"down", "up"}:
        raise ReferenceReplayError(
            f"reference replay event {index} contains a non-neutral key input"
        )
    if action == "down":
        if key in held:
            raise ReferenceReplayError(
                f"reference replay event {index} repeats a held key"
            )
        held.add(key)
    else:
        if key not in held:
            raise ReferenceReplayError(
                f"reference replay event {index} releases an unheld key"
            )
        held.remove(key)


def _validate_camera_event(
    event: Mapping[str, Any], *, index: int, camera_ticks: set[int], tick: int
) -> None:
    if tick in camera_ticks:
        raise ReferenceReplayError(
            f"reference replay event {index} repeats a camera tick"
        )
    camera_ticks.add(tick)
    for field in ("mouse_dx", "mouse_dy"):
        value = event.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ReferenceReplayError(
                f"reference replay event {index}.{field} must be an integer"
            )
    if not event["mouse_dx"] and not event["mouse_dy"]:
        raise ReferenceReplayError(
            f"reference replay event {index} has an empty camera delta"
        )
    duration = event.get("duration")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isfinite(float(duration))
        or not math.isclose(float(duration), 0.05, rel_tol=0, abs_tol=1e-9)
    ):
        raise ReferenceReplayError(
            f"reference replay event {index} camera duration must be one 20 Hz tick"
        )
