from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from mcdata.action_source import validate_solaris_rollout_binding
from mcdata.action_trace import (
    ActionTraceError,
    CameraCalibration,
    build_native_trace,
    compile_native_trace,
    write_compiled_trajectory,
    write_native_trace,
)
from mcdata.external_action_adapters import SolarisNormalizedControllerAdapter

SOLARIS_REPOSITORY = "https://github.com/solaris-wm/solaris-engine"
SOLARIS_COMMIT = "430f56f787405d6a7818e79e95e4ddee026dd6b7"
SOURCE_MINECRAFT_VERSION = "1.21"
TARGET_MINECRAFT_VERSION = "26.2"
SOURCE_FORMAT = "solaris_normalized_controller_boundary_v1"
TICK_RATE_HZ = 20
SOURCE_TIMING_STATUS = "source_timing_not_yet_validated"
TARGET_REPLAY_STATUS = "target_replay_not_yet_validated"

SOLARIS_EPISODE_TYPES = {
    "straightLineWalk",
    "chase",
    "orbit",
    "walkLook",
    "walkLookAway",
    "pvp",
    "pve",
    "buildStructure",
    "buildTower",
    "mine",
    "towerBridge",
    "buildHouse",
    "collector",
    "placeAndMine",
    "structureEval",
    "translationEval",
    "bothLookAwayEval",
    "oneLooksAwayEval",
    "rotationEval",
    "turnToLookEval",
    "turnToLookOppositeEval",
}


class SolarisRolloutImportError(ValueError):
    """Raised when Solaris provenance or controller-boundary actions are ambiguous."""


class _BoundSolarisAdapter:
    action_source = "scripted_skill_agent"

    def __init__(self, provenance: Mapping[str, Any]) -> None:
        self._provenance = dict(provenance)
        self._delegate = SolarisNormalizedControllerAdapter()

    def descriptor(self) -> dict[str, Any]:
        descriptor = self._delegate.descriptor()
        return {
            **descriptor,
            "parameters": {
                **descriptor["parameters"],
                "solaris_provenance": self._provenance,
            },
        }

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        return self._delegate.adapt(records, semantic_annotations=semantic_annotations)


def import_solaris_rollout(
    rollout_dir: Path,
    *,
    expected_rollout_sha256: str,
    expected_ticks: int,
    camera_calibration_path: Path,
    trace_out: Path,
    trajectory_out: Path,
) -> dict[str, Any]:
    """Import a pinned Solaris artifact into quarantine; this does not admit it for replay."""
    _require_sha256(expected_rollout_sha256, "expected_rollout_sha256")
    if not isinstance(expected_ticks, int) or isinstance(expected_ticks, bool) or expected_ticks <= 0:
        raise SolarisRolloutImportError("expected_ticks must be a positive integer")
    if trace_out.resolve() == trajectory_out.resolve():
        raise SolarisRolloutImportError("trace_out and trajectory_out must be different paths")

    manifest = _read_json(rollout_dir / "rollout_manifest.json", "rollout manifest")
    normalized = validate_solaris_rollout_manifest(manifest)
    if normalized["rollout_sha256"] != expected_rollout_sha256:
        raise SolarisRolloutImportError(
            "Solaris rollout manifest does not match --expected-rollout-sha256"
        )
    action_ref = normalized["artifacts"]["actions"]
    if action_ref["tick_count"] != expected_ticks:
        raise SolarisRolloutImportError(
            f"Solaris rollout has {action_ref['tick_count']} ticks, expected {expected_ticks}"
        )
    _verify_artifact(rollout_dir, normalized["artifacts"]["world_snapshot"], "world snapshot")
    action_bytes = _verify_artifact(rollout_dir, action_ref, "normalized actions")
    actions = _parse_jsonl(action_bytes, expected_ticks)
    calibration, target = _load_camera_calibration(camera_calibration_path)

    binding = _build_rollout_binding(normalized, calibration, target)
    trace, trajectory = _convert_actions(actions, normalized, calibration, binding)
    trajectory["solaris_rollout_binding"] = binding
    write_native_trace(trace_out, trace)
    write_compiled_trajectory(trajectory_out, trajectory)
    return {
        "rollout_sha256": normalized["rollout_sha256"],
        "trace_sha256": trace["trace_sha256"],
        "tick_count": len(trace["ticks"]),
        "trajectory_event_count": len(trajectory["events"]),
        "source_minecraft_version": SOURCE_MINECRAFT_VERSION,
        "target_minecraft_version": target["target_minecraft_version"],
        "source_timing_status": SOURCE_TIMING_STATUS,
        "target_replay_status": TARGET_REPLAY_STATUS,
    }


def _build_rollout_binding(
    manifest: Mapping[str, Any],
    calibration: CameraCalibration,
    target: Mapping[str, str],
) -> dict[str, Any]:
    binding = {
        "rollout_schema_version": 1,
        "rollout_sha256": manifest["rollout_sha256"],
        "solaris_repository_commit": SOLARIS_COMMIT,
        "source_minecraft_version": SOURCE_MINECRAFT_VERSION,
        "episode_id": manifest["episode"]["episode_id"],
        "episode_type": manifest["episode"]["episode_type"],
        "episode_role": manifest["episode"]["role"],
        "shared_rng_sha256": _semantic_sha256(manifest["episode"]["shared_rng"]),
        "world_snapshot_sha256": manifest["world"]["snapshot_sha256"],
        "action_artifact_sha256": manifest["artifacts"]["actions"]["sha256"],
        "source_timing_status": SOURCE_TIMING_STATUS,
        "target_minecraft_version": target["target_minecraft_version"],
        "target_client_profile": target["target_client_profile"],
        "camera_calibration_sha256": calibration.artifact_sha256,
        "target_replay_status": TARGET_REPLAY_STATUS,
    }
    validate_solaris_rollout_binding(binding)
    return binding


def _convert_actions(
    actions: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    calibration: CameraCalibration,
    binding: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    adapter = _BoundSolarisAdapter(
        {
            "repository": SOLARIS_REPOSITORY,
            "repository_commit": SOLARIS_COMMIT,
            "episode_id": binding["episode_id"],
            "episode_type": binding["episode_type"],
            "episode_role": binding["episode_role"],
            "shared_rng_sha256": binding["shared_rng_sha256"],
            "world_snapshot_sha256": binding["world_snapshot_sha256"],
            "action_artifact_sha256": binding["action_artifact_sha256"],
            "source_timing_status": SOURCE_TIMING_STATUS,
            "target_replay_status": TARGET_REPLAY_STATUS,
        }
    )
    try:
        trace = build_native_trace(
            adapter,
            actions,
            producer={
                "name": "Solaris Engine scripted skill agent",
                "version": SOLARIS_COMMIT,
                "model_sha256": None,
            },
            source_environment={
                "name": "Solaris Engine",
                "version": SOLARIS_COMMIT,
                "minecraft_version": SOURCE_MINECRAFT_VERSION,
                "action_format": SOURCE_FORMAT,
                "action_tick_rate_hz": TICK_RATE_HZ,
            },
            world={
                "seed": manifest["world"]["seed"],
                "snapshot_id": manifest["world"]["snapshot_id"],
                "snapshot_sha256": manifest["world"]["snapshot_sha256"],
            },
        )
        trajectory = compile_native_trace(trace, camera_calibration=calibration)
    except ActionTraceError as exc:
        raise SolarisRolloutImportError(f"Solaris canonical trace conversion failed: {exc}") from exc
    return trace, trajectory


def validate_solaris_rollout_manifest(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "source_timing_status",
        "target_replay_status",
        "source_environment",
        "episode",
        "world",
        "artifacts",
        "rollout_sha256",
    }
    manifest = _exact_mapping(value, expected, "Solaris rollout manifest")
    if manifest["schema_version"] != 1:
        raise SolarisRolloutImportError("Solaris rollout schema_version must be 1")
    if manifest["source_timing_status"] != SOURCE_TIMING_STATUS:
        raise SolarisRolloutImportError("Solaris source timing status is not fail-closed")
    if manifest["target_replay_status"] != TARGET_REPLAY_STATUS:
        raise SolarisRolloutImportError("Solaris target replay status is not fail-closed")
    _validate_source_environment(manifest["source_environment"])
    _validate_episode(manifest["episode"])
    _validate_world(manifest["world"])
    artifacts = _exact_mapping(
        manifest["artifacts"], {"actions", "world_snapshot"}, "Solaris artifacts"
    )
    _validate_artifact_ref(artifacts["actions"], "actions", with_ticks=True)
    _validate_artifact_ref(artifacts["world_snapshot"], "world snapshot", with_ticks=False)
    if artifacts["world_snapshot"]["sha256"] != manifest["world"]["snapshot_sha256"]:
        raise SolarisRolloutImportError("world snapshot artifact SHA disagrees with world binding")
    _require_sha256(manifest["rollout_sha256"], "rollout_sha256")
    if manifest["rollout_sha256"] != solaris_rollout_manifest_sha256(manifest):
        raise SolarisRolloutImportError("rollout_sha256 disagrees with canonical Solaris manifest")
    return dict(manifest)


def solaris_rollout_manifest_sha256(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "rollout_sha256"}
    return _semantic_sha256(payload)


def _validate_source_environment(value: Any) -> None:
    record = _exact_mapping(
        value,
        {
            "name",
            "repository",
            "repository_commit",
            "minecraft_version",
            "action_format",
            "action_tick_rate_hz",
            "camera_convention",
        },
        "Solaris source_environment",
    )
    expected = {
        "name": "Solaris Engine",
        "repository": SOLARIS_REPOSITORY,
        "repository_commit": SOLARIS_COMMIT,
        "minecraft_version": SOURCE_MINECRAFT_VERSION,
        "action_format": SOURCE_FORMAT,
        "action_tick_rate_hz": TICK_RATE_HZ,
        "camera_convention": "minecraft_input_degrees_yaw_right_pitch_down",
    }
    if dict(record) != expected:
        raise SolarisRolloutImportError("Solaris source_environment does not match the audited pin")


def _validate_episode(value: Any) -> None:
    record = _exact_mapping(
        value, {"episode_id", "episode_type", "role", "shared_rng"}, "Solaris episode"
    )
    episode_id = record["episode_id"]
    if not isinstance(episode_id, int) or isinstance(episode_id, bool) or episode_id < 0:
        raise SolarisRolloutImportError("Solaris episode_id must be a nonnegative integer")
    if record["episode_type"] not in SOLARIS_EPISODE_TYPES:
        raise SolarisRolloutImportError("Solaris episode_type is not present at the audited commit")
    if record["role"] not in {"Alpha", "Bravo"}:
        raise SolarisRolloutImportError("Solaris role must be Alpha or Bravo")
    rng = _exact_mapping(
        record["shared_rng"],
        {"library", "version", "base_seed", "episode_seed"},
        "Solaris shared_rng",
    )
    if rng["library"] != "seedrandom" or rng["version"] != "3.0.5":
        raise SolarisRolloutImportError("Solaris shared_rng must pin seedrandom 3.0.5")
    base_seed = _nonempty(rng["base_seed"], "shared_rng.base_seed")
    if rng["episode_seed"] != f"{base_seed}_{episode_id}":
        raise SolarisRolloutImportError("Solaris episode RNG seed does not match base_seed_episode_id")


def _validate_world(value: Any) -> None:
    record = _exact_mapping(value, {"seed", "snapshot_id", "snapshot_sha256"}, "Solaris world")
    if not isinstance(record["seed"], (int, str)) or isinstance(record["seed"], bool):
        raise SolarisRolloutImportError("Solaris world.seed must be an integer or string")
    _nonempty(record["snapshot_id"], "world.snapshot_id")
    _require_sha256(record["snapshot_sha256"], "world.snapshot_sha256")


def _validate_artifact_ref(value: Any, label: str, *, with_ticks: bool) -> None:
    fields = {"path", "sha256", "size_bytes"}
    if with_ticks:
        fields.add("tick_count")
    record = _exact_mapping(value, fields, f"Solaris {label} artifact")
    _normalized_relative_path(record["path"], f"{label} path")
    _require_sha256(record["sha256"], f"{label} sha256")
    if not isinstance(record["size_bytes"], int) or record["size_bytes"] <= 0:
        raise SolarisRolloutImportError(f"Solaris {label} size_bytes must be positive")
    if with_ticks and (
        not isinstance(record["tick_count"], int)
        or isinstance(record["tick_count"], bool)
        or record["tick_count"] <= 0
    ):
        raise SolarisRolloutImportError("Solaris actions tick_count must be positive")


def _verify_artifact(root: Path, ref: Mapping[str, Any], label: str) -> bytes:
    relative = _normalized_relative_path(ref["path"], f"{label} path")
    path = root / Path(*relative.parts)
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        raw = path.read_bytes()
    except (OSError, ValueError) as exc:
        raise SolarisRolloutImportError(f"could not read Solaris {label} artifact: {exc}") from exc
    if len(raw) != ref["size_bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise SolarisRolloutImportError(f"Solaris {label} artifact size/SHA-256 mismatch")
    return raw


def _parse_jsonl(raw: bytes, expected_ticks: int) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SolarisRolloutImportError("Solaris actions are not UTF-8") from exc
    if not text.endswith("\n") or "\r" in text:
        raise SolarisRolloutImportError("Solaris actions must use LF-terminated JSONL")
    lines = text.splitlines()
    if len(lines) != expected_ticks or any(not line for line in lines):
        raise SolarisRolloutImportError("Solaris JSONL line count disagrees with tick_count")
    result = []
    for index, line in enumerate(lines):
        try:
            value = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        except (json.JSONDecodeError, SolarisRolloutImportError) as exc:
            raise SolarisRolloutImportError(
                f"could not parse Solaris action JSONL line {index + 1}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise SolarisRolloutImportError(f"Solaris action {index} must be an object")
        result.append(value)
    return result


def _load_camera_calibration(path: Path) -> tuple[CameraCalibration, dict[str, str]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SolarisRolloutImportError(f"could not read camera calibration: {exc}") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SolarisRolloutImportError(f"could not parse camera calibration: {exc}") from exc
    record = _exact_mapping(
        value,
        {
            "schema_version",
            "calibration_id",
            "target_minecraft_version",
            "target_client_profile",
            "yaw_pixels_per_degree",
            "pitch_pixels_per_degree",
        },
        "camera calibration",
    )
    if record["schema_version"] != 1:
        raise SolarisRolloutImportError("camera calibration schema_version must be 1")
    if record["target_minecraft_version"] != TARGET_MINECRAFT_VERSION:
        raise SolarisRolloutImportError("Solaris camera calibration target must be Minecraft 26.2")
    calibration = CameraCalibration(
        calibration_id=_nonempty(record["calibration_id"], "calibration_id"),
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
        yaw_pixels_per_degree=_finite_nonzero(
            record["yaw_pixels_per_degree"], "yaw_pixels_per_degree"
        ),
        pitch_pixels_per_degree=_finite_nonzero(
            record["pitch_pixels_per_degree"], "pitch_pixels_per_degree"
        ),
    )
    calibration.as_dict()
    return calibration, {
        "target_minecraft_version": TARGET_MINECRAFT_VERSION,
        "target_client_profile": _nonempty(
            record["target_client_profile"], "target_client_profile"
        ),
    }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SolarisRolloutImportError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SolarisRolloutImportError(f"{label} must be a JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SolarisRolloutImportError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise SolarisRolloutImportError(f"JSON contains non-finite constant {value}")


def _exact_mapping(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SolarisRolloutImportError(f"{label} has an unstable field set")
    return value


def _normalized_relative_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise SolarisRolloutImportError(f"{label} must be non-empty")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise SolarisRolloutImportError(f"{label} must be normalized and relative")
    return path


def _semantic_sha256(value: Any) -> str:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SolarisRolloutImportError(f"Solaris provenance is not canonical JSON: {exc}") from exc
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SolarisRolloutImportError(f"{label} must be a non-empty string")
    return value


def _finite_nonzero(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SolarisRolloutImportError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result == 0:
        raise SolarisRolloutImportError(f"{label} must be finite and nonzero")
    return result


def _require_sha256(value: Any, label: str) -> None:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise SolarisRolloutImportError(f"{label} must be 64 lowercase hex characters")
