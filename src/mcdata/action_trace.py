from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Protocol

from mcdata.action_source import (
    declared_action_source,
    validate_action_source_record,
)

SCHEMA_VERSION = 1
TICK_RATE_HZ = 20

HELD_BUTTONS = (
    "forward",
    "back",
    "left",
    "right",
    "jump",
    "sneak",
    "sprint",
)
STATEFUL_CONTROLS = (*HELD_BUTTONS, "attack", "use")

_REPLAY_KEYS = {
    "forward": "w",
    "back": "s",
    "left": "a",
    "right": "d",
    "jump": "space",
    "sneak": "left_shift",
    "sprint": "left_control",
}


class ActionTraceError(ValueError):
    """Raised when an external action stream would lose provenance or input semantics."""


class NativeActionAdapter(Protocol):
    action_source: str

    def descriptor(self) -> dict[str, Any]: ...

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class CameraCalibration:
    calibration_id: str
    artifact_sha256: str
    yaw_pixels_per_degree: float
    pitch_pixels_per_degree: float

    def as_dict(self) -> dict[str, Any]:
        if not self.calibration_id:
            raise ActionTraceError("camera calibration_id must be non-empty")
        _require_sha256(self.artifact_sha256, "camera calibration artifact_sha256")
        yaw = _finite_number(self.yaw_pixels_per_degree, "yaw_pixels_per_degree")
        pitch = _finite_number(self.pitch_pixels_per_degree, "pitch_pixels_per_degree")
        if yaw == 0 or pitch == 0:
            raise ActionTraceError("camera pixels-per-degree calibration must be nonzero")
        return {
            "calibration_id": self.calibration_id,
            "artifact_sha256": self.artifact_sha256,
            "yaw_pixels_per_degree": yaw,
            "pitch_pixels_per_degree": pitch,
        }


def build_native_trace(
    adapter: NativeActionAdapter,
    records: Sequence[Mapping[str, Any]],
    *,
    producer: Mapping[str, Any],
    source_environment: Mapping[str, Any],
    world: Mapping[str, Any],
    semantic_annotations: Mapping[int, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Purely convert external records into a validated, content-addressed 20 Hz trace."""
    descriptor = _validate_adapter_descriptor(adapter.descriptor())
    producer_record = _validate_producer(producer, adapter.action_source)
    environment_record = _validate_source_environment(source_environment, descriptor)
    world_record = _validate_world(world)
    ticks = adapter.adapt(records, semantic_annotations=semantic_annotations)
    if not ticks:
        raise ActionTraceError("native action trace must contain at least one tick")
    trace = {
        "schema_version": SCHEMA_VERSION,
        "tick_rate_hz": TICK_RATE_HZ,
        "duration_sec": _clean_float(len(ticks) / TICK_RATE_HZ),
        "action_source": declared_action_source(adapter.action_source),
        "producer": producer_record,
        "source_environment": environment_record,
        "world": world_record,
        "adapter": descriptor,
        "ticks": ticks,
    }
    trace["trace_sha256"] = native_trace_sha256(trace)
    validate_native_trace(trace)
    return trace


def validate_native_trace(trace: Any) -> None:
    if not isinstance(trace, Mapping) or set(trace) != {
        "schema_version",
        "tick_rate_hz",
        "duration_sec",
        "action_source",
        "producer",
        "source_environment",
        "world",
        "adapter",
        "ticks",
        "trace_sha256",
    }:
        raise ActionTraceError("native trace has an unstable top-level field set")
    if trace.get("schema_version") != SCHEMA_VERSION or trace.get("tick_rate_hz") != TICK_RATE_HZ:
        raise ActionTraceError("native trace must use schema v1 at exactly 20 Hz")
    source = validate_action_source_record(trace.get("action_source"))
    if source["provenance"] != "declared":
        raise ActionTraceError("canonical trace action_source must be explicitly declared")
    descriptor = _validate_adapter_descriptor(trace.get("adapter"))
    _validate_producer(trace.get("producer"), source["id"])
    _validate_source_environment(trace.get("source_environment"), descriptor)
    _validate_world(trace.get("world"))
    ticks = trace.get("ticks")
    if not isinstance(ticks, list) or not ticks:
        raise ActionTraceError("native trace ticks must be a non-empty list")
    _validate_ticks(ticks)
    expected_duration = _clean_float(len(ticks) / TICK_RATE_HZ)
    if trace.get("duration_sec") != expected_duration:
        raise ActionTraceError("native trace duration does not match its 20 Hz tick count")
    _require_sha256(trace.get("trace_sha256"), "trace_sha256")
    if trace["trace_sha256"] != native_trace_sha256(trace):
        raise ActionTraceError("native trace semantic SHA does not match its canonical payload")


def native_trace_sha256(trace: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in trace.items() if key != "trace_sha256"}
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def native_trace_ref(trace: Mapping[str, Any]) -> dict[str, Any]:
    validate_native_trace(trace)
    return {
        "schema_version": SCHEMA_VERSION,
        "sha256": trace["trace_sha256"],
        "tick_rate_hz": TICK_RATE_HZ,
    }


def compile_native_trace(
    trace: Mapping[str, Any], *, camera_calibration: CameraCalibration
) -> dict[str, Any]:
    """Compile one canonical trace into deterministic existing replay events."""
    validate_native_trace(trace)
    calibration = camera_calibration.as_dict()
    events: list[dict[str, Any]] = []
    cumulative_x = Decimal(0)
    cumulative_y = Decimal(0)
    emitted_x = 0
    emitted_y = 0
    for tick in trace["ticks"]:
        tick_index = tick["tick"]
        timestamp = _tick_time(tick_index)
        for edge in tick["edge_events"]:
            events.append(_edge_to_replay_event(edge, timestamp, tick_index))
        camera = tick["camera_delta_degrees"]
        cumulative_x += Decimal(str(camera["yaw"])) * Decimal(
            str(calibration["yaw_pixels_per_degree"])
        )
        cumulative_y += Decimal(str(camera["pitch"])) * Decimal(
            str(calibration["pitch_pixels_per_degree"])
        )
        target_x = _decimal_pixel(cumulative_x)
        target_y = _decimal_pixel(cumulative_y)
        dx, dy = target_x - emitted_x, target_y - emitted_y
        emitted_x, emitted_y = target_x, target_y
        if dx or dy:
            events.append(
                {
                    "t": timestamp,
                    "mouse_dx": dx,
                    "mouse_dy": dy,
                    "duration": 1 / TICK_RATE_HZ,
                    "native_trace_tick": tick_index,
                }
            )
    events.extend(_terminal_release_events(trace["ticks"], trace["duration_sec"]))
    advanced = _advanced_control_summary(trace["ticks"])
    return {
        "type": "native_action_trace_replay",
        "duration_sec": trace["duration_sec"],
        "events": events,
        "native_trace": native_trace_ref(trace),
        "action_source": dict(trace["action_source"]),
        "camera_calibration": calibration,
        "curriculum_binding": {
            "status": (
                "requires_semantic_effect_validation"
                if any(advanced.values())
                else "l1_candidate"
            ),
            **advanced,
        },
    }


def write_native_trace(path: Path, trace: Mapping[str, Any]) -> None:
    validate_native_trace(trace)
    _atomic_write_json(path, trace)


def load_native_trace(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionTraceError(f"could not read native trace {path}: {exc}") from exc
    validate_native_trace(value)
    return value


def write_compiled_trajectory(path: Path, trajectory: Mapping[str, Any]) -> None:
    if trajectory.get("type") != "native_action_trace_replay":
        raise ActionTraceError("compiled trajectory has the wrong type")
    _atomic_write_json(path, trajectory)


def _raw_tick(
    *,
    index: int,
    source_tick: int,
    held: Sequence[str],
    attack: bool,
    use: bool,
    inventory: bool,
    hotbar: int | None,
    camera: Mapping[str, float],
    annotations: list[str],
) -> dict[str, Any]:
    held_set = set(held)
    unknown = sorted(held_set - set(HELD_BUTTONS))
    if unknown:
        raise ActionTraceError(f"tick {index} has unknown held buttons {unknown!r}")
    tick = {
        "tick": index,
        "source_tick": source_tick,
        "held_buttons": [control for control in HELD_BUTTONS if control in held_set],
        "camera_delta_degrees": {
            "pitch": _clean_float(camera["pitch"]),
            "yaw": _clean_float(camera["yaw"]),
        },
        "hotbar": hotbar,
        "use": bool(use),
        "attack": bool(attack),
        "inventory": bool(inventory),
    }
    if annotations:
        tick["semantic_annotations"] = annotations
    return tick


def _derive_edge_events(raw_ticks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    previous: set[str] = set()
    result = []
    for raw in raw_ticks:
        current = set(raw["held_buttons"])
        if raw["attack"]:
            current.add("attack")
        if raw["use"]:
            current.add("use")
        edges = []
        for control in STATEFUL_CONTROLS:
            if (control in current) == (control in previous):
                continue
            edges.append(
                {"control": control, "edge": "press" if control in current else "release"}
            )
        if raw["inventory"]:
            edges.append({"control": "inventory", "edge": "press"})
        if raw["hotbar"] is not None:
            edges.append({"control": "hotbar", "edge": "select", "slot": raw["hotbar"]})
        result.append({**raw, "edge_events": edges})
        previous = current
    return result


def _validate_ticks(ticks: Sequence[Any]) -> None:
    raw_ticks = []
    for index, value in enumerate(ticks):
        if not isinstance(value, Mapping):
            raise ActionTraceError(f"native tick {index} is not a mapping")
        required = {
            "tick",
            "source_tick",
            "held_buttons",
            "edge_events",
            "camera_delta_degrees",
            "hotbar",
            "use",
            "attack",
            "inventory",
        }
        optional = {"semantic_annotations"}
        if not required <= set(value) or set(value) - required - optional:
            raise ActionTraceError(f"native tick {index} has an unstable field set")
        if value.get("tick") != index:
            raise ActionTraceError("native trace ticks must be contiguous and zero-based")
        source_tick = value.get("source_tick")
        if not isinstance(source_tick, int) or isinstance(source_tick, bool) or source_tick < 0:
            raise ActionTraceError(f"native tick {index} has an invalid source_tick")
        held = value.get("held_buttons")
        if not isinstance(held, list) or held != [item for item in HELD_BUTTONS if item in held]:
            raise ActionTraceError(f"native tick {index} held_buttons are not canonical")
        if len(set(held)) != len(held):
            raise ActionTraceError(f"native tick {index} repeats a held button")
        for field in ("use", "attack", "inventory"):
            if not isinstance(value.get(field), bool):
                raise ActionTraceError(f"native tick {index}.{field} must be boolean")
        hotbar = value.get("hotbar")
        if hotbar is not None and (
            not isinstance(hotbar, int) or isinstance(hotbar, bool) or not 1 <= hotbar <= 9
        ):
            raise ActionTraceError(f"native tick {index}.hotbar must be null or 1..9")
        camera = _mapping(
            value.get("camera_delta_degrees"), f"native tick {index}.camera_delta_degrees"
        )
        if set(camera) != {"pitch", "yaw"}:
            raise ActionTraceError(f"native tick {index} camera must contain pitch and yaw")
        _finite_number(camera["pitch"], f"native tick {index}.camera.pitch")
        _finite_number(camera["yaw"], f"native tick {index}.camera.yaw")
        annotations = value.get("semantic_annotations", [])
        if (
            not isinstance(annotations, list)
            or annotations != sorted(set(annotations))
            or any(not isinstance(item, str) or not item for item in annotations)
        ):
            raise ActionTraceError(f"native tick {index} semantic annotations are not canonical")
        raw_ticks.append({key: item for key, item in value.items() if key != "edge_events"})
    expected = _derive_edge_events(raw_ticks)
    for index, (actual, derived) in enumerate(zip(ticks, expected, strict=True)):
        if actual.get("edge_events") != derived["edge_events"]:
            raise ActionTraceError(f"native tick {index} edge_events disagree with held state")


def _validate_adapter_descriptor(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "name",
        "version",
        "source_format",
        "parameters",
    }:
        raise ActionTraceError(
            "adapter descriptor must contain name, version, source_format, and parameters"
        )
    name = _nonempty_string(value.get("name"), "adapter.name")
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ActionTraceError("adapter.version must be a positive integer")
    source_format = _nonempty_string(value.get("source_format"), "adapter.source_format")
    parameters = value.get("parameters")
    if not isinstance(parameters, Mapping):
        raise ActionTraceError("adapter.parameters must be a mapping")
    return {
        "name": name,
        "version": version,
        "source_format": source_format,
        "parameters": dict(parameters),
    }


def _validate_producer(value: Any, action_source: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"name", "version", "model_sha256"}:
        raise ActionTraceError("producer must contain exactly name, version, and model_sha256")
    name = _nonempty_string(value.get("name"), "producer.name")
    version = _nonempty_string(value.get("version"), "producer.version")
    model_sha256 = value.get("model_sha256")
    if action_source in {"learned_visual_policy", "llm_skill_agent"}:
        _require_sha256(model_sha256, f"{action_source} producer.model_sha256")
    elif model_sha256 is not None:
        _require_sha256(model_sha256, "producer.model_sha256")
    return {"name": name, "version": version, "model_sha256": model_sha256}


def _validate_source_environment(
    value: Any, descriptor: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {
        "name",
        "version",
        "minecraft_version",
        "action_format",
        "action_tick_rate_hz",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ActionTraceError(
            "source_environment must contain name, version, minecraft_version, "
            "action_format, and action_tick_rate_hz"
        )
    result = {
        key: _nonempty_string(value.get(key), f"source_environment.{key}")
        for key in ("name", "version", "minecraft_version", "action_format")
    }
    if result["action_format"] != descriptor["source_format"]:
        raise ActionTraceError("source_environment action_format disagrees with adapter")
    if value.get("action_tick_rate_hz") != TICK_RATE_HZ:
        raise ActionTraceError("source actions must be supplied or resampled at exactly 20 Hz")
    return {**result, "action_tick_rate_hz": TICK_RATE_HZ}


def _validate_world(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "seed",
        "snapshot_id",
        "snapshot_sha256",
    }:
        raise ActionTraceError("world must contain exactly seed, snapshot_id, snapshot_sha256")
    seed = value.get("seed")
    if not isinstance(seed, (int, str)) or isinstance(seed, bool):
        raise ActionTraceError("world.seed must be an integer or string")
    snapshot_id = _nonempty_string(value.get("snapshot_id"), "world.snapshot_id")
    snapshot_sha256 = value.get("snapshot_sha256")
    _require_sha256(snapshot_sha256, "world.snapshot_sha256")
    return {
        "seed": seed,
        "snapshot_id": snapshot_id,
        "snapshot_sha256": snapshot_sha256,
    }


def _edge_to_replay_event(edge: Mapping[str, Any], timestamp: float, tick: int) -> dict[str, Any]:
    control = edge["control"]
    phase = edge["edge"]
    common = {"t": timestamp, "native_trace_tick": tick}
    if control in _REPLAY_KEYS:
        return {**common, "key": _REPLAY_KEYS[control], "action": "down" if phase == "press" else "up"}
    if control in {"attack", "use"}:
        return {
            **common,
            "mouse_button": 1 if control == "attack" else 3,
            "action": "down" if phase == "press" else "up",
        }
    if control == "inventory":
        return {**common, "key": "e", "action": "tap"}
    if control == "hotbar":
        return {**common, "key": str(edge["slot"]), "action": "tap"}
    raise ActionTraceError(f"cannot compile edge control {control!r}")


def _terminal_release_events(
    ticks: Sequence[Mapping[str, Any]], duration: float
) -> list[dict[str, Any]]:
    last = ticks[-1]
    active = set(last["held_buttons"])
    if last["attack"]:
        active.add("attack")
    if last["use"]:
        active.add("use")
    return [
        _edge_to_replay_event(
            {"control": control, "edge": "release"},
            _clean_float(duration),
            len(ticks),
        )
        for control in STATEFUL_CONTROLS
        if control in active
    ]


def _advanced_control_summary(ticks: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    return {
        "has_jump_input": any("jump" in tick["held_buttons"] for tick in ticks),
        "has_use_input": any(tick["use"] for tick in ticks),
        "has_attack_input": any(tick["attack"] for tick in ticks),
    }


def _scalar(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ActionTraceError(f"{label} must be a mapping")
    return value


def _finite_number(value: Any, label: str) -> float:
    value = _scalar(value)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ActionTraceError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ActionTraceError(f"{label} must be finite")
    return number


def _clean_float(value: float) -> float:
    cleaned = round(float(value), 9)
    return 0.0 if cleaned == 0 else cleaned


def _tick_time(index: int) -> float:
    return _clean_float(index / TICK_RATE_HZ)


def _decimal_pixel(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionTraceError(f"{label} must be a non-empty string")
    return value


def _require_sha256(value: Any, label: str) -> None:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise ActionTraceError(f"{label} must be 64 lowercase hex characters")


def _canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ActionTraceError(f"native trace is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
