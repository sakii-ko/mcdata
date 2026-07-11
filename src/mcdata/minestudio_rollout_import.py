from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcdata.action_trace import (
    ActionTraceError,
    CameraCalibration,
    build_native_trace,
    compile_native_trace,
    write_compiled_trajectory,
    write_native_trace,
)
from mcdata.external_action_adapters import MineStudioVPTEnvAdapter
from mcdata.minestudio_rollout_support import (
    MineStudioRolloutImportError,
    artifact_path as _artifact_path,
    canonical_bytes as _canonical_bytes,
    exact_mapping as _exact_mapping,
    finite as _finite,
    finite_nonzero as _finite_nonzero,
    nonempty as _nonempty,
    parse_json_bytes as _parse_json_bytes,
    read_bytes as _read_bytes,
    read_json as _read_json,
    require_sha256 as _require_sha256,
)

MINESTUDIO_VERSION = "1.1.5"
MINESTUDIO_COMMIT = "278aa8553668d591339dbf30d281594ed06ee882"
SOURCE_MINECRAFT_VERSION = "1.16.5"
TARGET_MINECRAFT_VERSION = "26.2"
TICK_RATE_HZ = 20
MODEL_REPOSITORY = "CraftJarvis/MineStudio_VPT.foundation_model_1x"
MODEL_REVISION = "17a5f43b30c4f734489902fdc6a55bf47781be3a"
MODEL_SHA256 = "475fbd0df655ad77c3e3f602d157f4273032bff8e6e82c3863a992f5b03753f9"
MODEL_CONFIG_SHA256 = "d088a1f68ca44cac73d0efe1af7b7df4ade5994b39360da7fe74cfb6b282cbd2"
ENGINE_REVISION = "48d4809cfddc7e2b85295e8c39b3c5e8c6d46ae7"
ENGINE_ARCHIVE_SHA256 = "293fac6ac72245b3365dce0e8bfbb6396fb94df29b23b6538f3bd7e2eec13ec6"

ENV_CONTROLS = (
    "attack",
    "back",
    "forward",
    "jump",
    "left",
    "right",
    "sneak",
    "sprint",
    "use",
    "drop",
    "inventory",
    *(f"hotbar.{slot}" for slot in range(1, 10)),
)
MASKED_CONTROLS = (
    "jump",
    "use",
    "attack",
    "inventory",
    "drop",
    *(f"hotbar.{slot}" for slot in range(1, 10)),
)
LEARNED_CONTROLS = (
    "back",
    "forward",
    "left",
    "right",
    "sneak",
    "sprint",
    "camera",
)


class _BoundMineStudioAdapter:
    action_source = "learned_visual_policy"

    def __init__(self, rollout_binding: Mapping[str, Any]) -> None:
        self._binding = dict(rollout_binding)
        self._delegate = MineStudioVPTEnvAdapter()

    def descriptor(self) -> dict[str, Any]:
        descriptor = self._delegate.descriptor()
        return {
            **descriptor,
            "source_format": "minestudio_post_mapper_env_action_v1",
            "parameters": {
                **descriptor["parameters"],
                "raw_source_format": descriptor["source_format"],
                "rollout_binding": self._binding,
            },
        }

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        return self._delegate.adapt(records, semantic_annotations=semantic_annotations)


def import_minestudio_rollout(
    rollout_dir: Path,
    *,
    expected_rollout_sha256: str,
    expected_ticks: int,
    camera_calibration_path: Path,
    trace_out: Path,
    trajectory_out: Path,
) -> dict[str, Any]:
    """Verify a pinned raw rollout, then write a canonical trace and target trajectory."""
    _require_sha256(expected_rollout_sha256, "expected_rollout_sha256")
    if not isinstance(expected_ticks, int) or isinstance(expected_ticks, bool) or expected_ticks <= 0:
        raise MineStudioRolloutImportError("expected_ticks must be a positive integer")
    if trace_out.resolve() == trajectory_out.resolve():
        raise MineStudioRolloutImportError("trace_out and trajectory_out must be different paths")
    manifest = _read_json(rollout_dir / "rollout_manifest.json", "rollout manifest")
    normalized = validate_rollout_manifest(manifest)
    if normalized["rollout_sha256"] != expected_rollout_sha256:
        raise MineStudioRolloutImportError(
            "rollout manifest is valid but does not match --expected-rollout-sha256"
        )
    if normalized["rollout"]["tick_count"] != expected_ticks:
        raise MineStudioRolloutImportError(
            f"rollout has {normalized['rollout']['tick_count']} ticks, expected {expected_ticks}"
        )
    reset_contract = _verify_reset_contract(rollout_dir, normalized)
    actions = _load_actions(rollout_dir, normalized, expected_ticks=expected_ticks)
    _verify_optional_artifacts(rollout_dir, normalized)
    calibration, target = load_camera_calibration(camera_calibration_path)
    binding = _rollout_binding(normalized)
    adapter = _BoundMineStudioAdapter(binding)
    try:
        trace = build_native_trace(
            adapter,
            actions,
            producer={
                "name": normalized["producer"]["name"],
                "version": normalized["producer"]["revision"],
                "model_sha256": normalized["producer"]["model_sha256"],
            },
            source_environment={
                "name": normalized["source_environment"]["name"],
                "version": normalized["source_environment"]["version"],
                "minecraft_version": normalized["source_environment"]["minecraft_version"],
                "action_format": "minestudio_post_mapper_env_action_v1",
                "action_tick_rate_hz": TICK_RATE_HZ,
            },
            world={
                "seed": reset_contract["world_seed"],
                "snapshot_id": reset_contract["contract_id"],
                "snapshot_sha256": normalized["reset_contract"]["sha256"],
            },
        )
        trajectory = compile_native_trace(trace, camera_calibration=calibration)
    except ActionTraceError as exc:
        raise MineStudioRolloutImportError(f"canonical trace conversion failed: {exc}") from exc
    if trajectory["curriculum_binding"]["status"] != "l1_candidate":
        raise MineStudioRolloutImportError("neutral rollout unexpectedly contains advanced controls")
    trajectory["external_rollout_binding"] = {
        "rollout_schema_version": 1,
        "rollout_sha256": normalized["rollout_sha256"],
        "source_minecraft_version": SOURCE_MINECRAFT_VERSION,
        "target_minecraft_version": target["target_minecraft_version"],
        "target_client_profile": target["target_client_profile"],
        "camera_calibration_sha256": calibration.artifact_sha256,
        "compatibility_status": "target_replay_not_yet_validated",
    }
    write_native_trace(trace_out, trace)
    write_compiled_trajectory(trajectory_out, trajectory)
    return {
        "rollout_sha256": normalized["rollout_sha256"],
        "trace_sha256": trace["trace_sha256"],
        "tick_count": len(trace["ticks"]),
        "trajectory_event_count": len(trajectory["events"]),
        "source_minecraft_version": SOURCE_MINECRAFT_VERSION,
        "target_minecraft_version": target["target_minecraft_version"],
        "compatibility_status": "target_replay_not_yet_validated",
    }


def validate_rollout_manifest(value: Any) -> dict[str, Any]:
    expected = {
        "schema_version",
        "status",
        "source_environment",
        "producer",
        "engine",
        "reset_contract",
        "rollout",
        "runtime",
        "artifacts",
        "rollout_sha256",
    }
    manifest = _exact_mapping(value, expected, "rollout manifest")
    schema_version = manifest["schema_version"]
    if schema_version not in {1, 2} or manifest["status"] != "complete":
        raise MineStudioRolloutImportError("rollout manifest must be complete schema v1 or v2")
    _require_sha256(manifest["rollout_sha256"], "rollout_sha256")
    if manifest["rollout_sha256"] != rollout_manifest_sha256(manifest):
        raise MineStudioRolloutImportError("rollout_sha256 disagrees with canonical manifest payload")
    _validate_source_environment(manifest["source_environment"])
    _validate_producer(manifest["producer"])
    _validate_engine(manifest["engine"])
    _validate_reset_ref(manifest["reset_contract"])
    _validate_rollout_spec(manifest["rollout"], schema_version=schema_version)
    _validate_runtime_record(manifest["runtime"])
    _validate_artifacts(manifest["artifacts"])
    if manifest["artifacts"]["actions"]["tick_count"] != manifest["rollout"]["tick_count"]:
        raise MineStudioRolloutImportError("actions artifact tick count disagrees with rollout")
    if manifest["rollout"]["seed"] != manifest["reset_contract"]["world_seed"]:
        raise MineStudioRolloutImportError("rollout seed disagrees with reset contract")
    has_video_callback = "record_video" in manifest["rollout"]["callback_order"]
    if has_video_callback != (manifest["artifacts"]["video"] is not None):
        raise MineStudioRolloutImportError("record_video callback disagrees with video artifact")
    return dict(manifest)


def rollout_manifest_sha256(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "rollout_sha256"}
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def load_camera_calibration(path: Path) -> tuple[CameraCalibration, dict[str, str]]:
    raw = _read_bytes(path, "camera calibration")
    value = _parse_json_bytes(raw, path, "camera calibration")
    expected = {
        "schema_version",
        "calibration_id",
        "target_minecraft_version",
        "target_client_profile",
        "yaw_pixels_per_degree",
        "pitch_pixels_per_degree",
    }
    record = _exact_mapping(value, expected, "camera calibration")
    if record["schema_version"] != 1:
        raise MineStudioRolloutImportError("camera calibration schema_version must be 1")
    if record["target_minecraft_version"] != TARGET_MINECRAFT_VERSION:
        raise MineStudioRolloutImportError(
            f"camera calibration target must be Minecraft {TARGET_MINECRAFT_VERSION}"
        )
    calibration_id = _nonempty(record["calibration_id"], "calibration_id")
    target_profile = _nonempty(record["target_client_profile"], "target_client_profile")
    calibration = CameraCalibration(
        calibration_id=calibration_id,
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
        "target_client_profile": target_profile,
    }


def _verify_reset_contract(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    ref = manifest["reset_contract"]
    path = _artifact_path(root, ref["path"], "reset contract")
    raw = _read_bytes(path, "reset contract")
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise MineStudioRolloutImportError("reset contract SHA-256 does not match manifest")
    value = _parse_json_bytes(raw, path, "reset contract")
    expected = {
        "schema_version",
        "contract_id",
        "source_minecraft_version",
        "snapshot_kind",
        "world_seed",
        "obs_size",
        "render_size",
        "num_empty_frames",
        "preferred_spawn_biome",
        "inventory",
    }
    record = _exact_mapping(value, expected, "reset contract")
    if record["schema_version"] != 1:
        raise MineStudioRolloutImportError("reset contract schema_version must be 1")
    if record["contract_id"] != ref["contract_id"]:
        raise MineStudioRolloutImportError("reset contract_id disagrees with manifest")
    if record["source_minecraft_version"] != SOURCE_MINECRAFT_VERSION:
        raise MineStudioRolloutImportError("reset source Minecraft version is not 1.16.5")
    if record["snapshot_kind"] != "procedural_seeded_reset_only":
        raise MineStudioRolloutImportError("reset contract falsely claims a restorable snapshot")
    if record["world_seed"] != ref["world_seed"]:
        raise MineStudioRolloutImportError("reset world seed disagrees with manifest")
    if record["obs_size"] != [128, 128] or record["render_size"] != [640, 360]:
        raise MineStudioRolloutImportError("reset dimensions do not match neutral VPT contract")
    _nonempty(record["contract_id"], "reset contract_id")
    if not isinstance(record["world_seed"], int) or isinstance(record["world_seed"], bool):
        raise MineStudioRolloutImportError("reset world_seed must be an integer")
    empty_frames = record["num_empty_frames"]
    if not isinstance(empty_frames, int) or isinstance(empty_frames, bool) or empty_frames < 0:
        raise MineStudioRolloutImportError("reset num_empty_frames must be nonnegative")
    biome = record["preferred_spawn_biome"]
    if biome is not None and (not isinstance(biome, str) or not biome):
        raise MineStudioRolloutImportError("reset preferred_spawn_biome is invalid")
    inventory = record["inventory"]
    if not isinstance(inventory, Mapping) or any(not isinstance(key, str) for key in inventory):
        raise MineStudioRolloutImportError("reset inventory must be a string-keyed mapping")
    return dict(record)


def _load_actions(
    root: Path, manifest: Mapping[str, Any], *, expected_ticks: int
) -> list[dict[str, Any]]:
    ref = manifest["artifacts"]["actions"]
    path = _artifact_path(root, ref["path"], "actions")
    raw = _read_bytes(path, "env actions")
    if len(raw) != ref["size_bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise MineStudioRolloutImportError("env actions size/SHA-256 disagrees with manifest")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MineStudioRolloutImportError("env actions JSONL is not UTF-8") from exc
    lines = text.splitlines()
    if not text.endswith("\n") or any(not line for line in lines):
        raise MineStudioRolloutImportError("env actions JSONL must have one nonblank line per tick")
    if len(lines) != expected_ticks or ref["tick_count"] != expected_ticks:
        raise MineStudioRolloutImportError("env actions line count disagrees with fixed tick count")
    records = []
    for tick, line in enumerate(lines):
        value = _parse_json_bytes(line.encode("utf-8"), path, f"env action tick {tick}")
        records.append(_validate_env_action(value, tick))
    return records


def _validate_env_action(value: Any, tick: int) -> dict[str, Any]:
    record = _exact_mapping(value, {"source_tick", "camera", *ENV_CONTROLS}, f"action {tick}")
    if record["source_tick"] != tick:
        raise MineStudioRolloutImportError("env action source ticks must be contiguous and zero-based")
    result: dict[str, Any] = {"source_tick": tick}
    for control in ENV_CONTROLS:
        item = record[control]
        if not isinstance(item, int) or isinstance(item, bool) or item not in {0, 1}:
            raise MineStudioRolloutImportError(f"action {tick}.{control} must be binary")
        result[control] = item
    for control in MASKED_CONTROLS:
        if result[control] != 0:
            raise MineStudioRolloutImportError(f"action {tick}.{control} escaped neutral mask")
    camera = record["camera"]
    if not isinstance(camera, list) or len(camera) != 2:
        raise MineStudioRolloutImportError(f"action {tick}.camera must be [pitch,yaw]")
    result["camera"] = [
        _finite(camera[0], f"action {tick}.camera[0]"),
        _finite(camera[1], f"action {tick}.camera[1]"),
    ]
    return result


def _verify_optional_artifacts(root: Path, manifest: Mapping[str, Any]) -> None:
    frames = manifest["artifacts"]["frames"]
    if frames is not None:
        directory = _artifact_path(root, frames["path"], "frames")
        if not directory.is_dir() or directory.is_symlink():
            raise MineStudioRolloutImportError("frames artifact must be a real directory")
        paths = sorted(path for path in directory.rglob("*") if path.is_file())
        if len(paths) != frames["file_count"] or any(path.is_symlink() for path in paths):
            raise MineStudioRolloutImportError("frames artifact count or file type disagrees")
        records = [_file_artifact(path, root) for path in paths]
        if hashlib.sha256(_canonical_bytes(records)).hexdigest() != frames["tree_sha256"]:
            raise MineStudioRolloutImportError("frames tree SHA-256 disagrees with manifest")
    video = manifest["artifacts"]["video"]
    if video is not None:
        path = _artifact_path(root, video["path"], "video")
        raw = _read_bytes(path, "video")
        if len(raw) != video["size_bytes"] or hashlib.sha256(raw).hexdigest() != video["sha256"]:
            raise MineStudioRolloutImportError("video size/SHA-256 disagrees with manifest")


def _file_artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(_read_bytes(path, "frame")).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def _rollout_binding(manifest: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "rollout_schema_version": manifest["schema_version"],
        "rollout_sha256": manifest["rollout_sha256"],
        "minestudio_repository_commit": manifest["source_environment"]["repository_commit"],
        "engine_archive_sha256": manifest["engine"]["archive_sha256"],
        "engine_runtime_jar_sha256": manifest["engine"]["runtime_jar_sha256"],
        "reset_contract_sha256": manifest["reset_contract"]["sha256"],
        "deterministic_policy": manifest["rollout"]["deterministic_policy"],
        "masked_controls": manifest["rollout"]["masked_controls"],
    }
    if manifest["schema_version"] == 2:
        result.update(
            sampling_mode=manifest["rollout"]["sampling_mode"],
            sampling_seed=manifest["rollout"]["sampling_seed"],
            policy_sampling_reproducibility=manifest["rollout"][
                "policy_sampling_reproducibility"
            ],
        )
    return result


def _validate_source_environment(value: Any) -> None:
    expected = {
        "name",
        "version",
        "repository",
        "repository_commit",
        "minecraft_version",
        "action_type",
        "action_format",
        "action_tick_rate_hz",
    }
    record = _exact_mapping(value, expected, "source_environment")
    required = {
        "name": "MineStudio",
        "version": MINESTUDIO_VERSION,
        "repository": "https://github.com/CraftJarvis/MineStudio.git",
        "repository_commit": MINESTUDIO_COMMIT,
        "minecraft_version": SOURCE_MINECRAFT_VERSION,
        "action_type": "agent",
        "action_format": "minestudio_post_mapper_env_action_v1",
        "action_tick_rate_hz": TICK_RATE_HZ,
    }
    if dict(record) != required:
        raise MineStudioRolloutImportError("source_environment is not the pinned MineStudio contract")


def _validate_producer(value: Any) -> None:
    expected = {
        "name",
        "repository",
        "revision",
        "filename",
        "model_sha256",
        "config_sha256",
        "license_status",
        "usage_scope",
    }
    record = _exact_mapping(value, expected, "producer")
    if (
        record["name"] != "MineStudio VPT foundation_model_1x"
        or record["repository"] != MODEL_REPOSITORY
        or record["revision"] != MODEL_REVISION
        or record["filename"] != "model.safetensors"
        or record["model_sha256"] != MODEL_SHA256
        or record["config_sha256"] != MODEL_CONFIG_SHA256
        or record["license_status"] != "license_unknown"
        or record["usage_scope"] != "research_only"
    ):
        raise MineStudioRolloutImportError("producer is not the pinned research-only VPT model")


def _validate_engine(value: Any) -> None:
    expected = {
        "repository",
        "revision",
        "archive_filename",
        "archive_sha256",
        "runtime_jar",
        "runtime_jar_sha256",
    }
    record = _exact_mapping(value, expected, "engine")
    if (
        record["repository"] != "CraftJarvis/SimulatorEngine"
        or record["revision"] != ENGINE_REVISION
        or record["archive_filename"] != "engine.zip"
        or record["archive_sha256"] != ENGINE_ARCHIVE_SHA256
        or record["runtime_jar"] != "engine/build/libs/mcprec-6.13.jar"
    ):
        raise MineStudioRolloutImportError("engine is not the pinned SimulatorEngine artifact")
    _require_sha256(record["runtime_jar_sha256"], "engine.runtime_jar_sha256")


def _validate_reset_ref(value: Any) -> None:
    record = _exact_mapping(
        value,
        {"path", "sha256", "contract_id", "snapshot_kind", "world_seed"},
        "reset_contract",
    )
    if record["path"] != "reset_contract.json":
        raise MineStudioRolloutImportError("reset contract must use the canonical relative path")
    _require_sha256(record["sha256"], "reset_contract.sha256")
    _nonempty(record["contract_id"], "reset_contract.contract_id")
    if record["snapshot_kind"] != "procedural_seeded_reset_only":
        raise MineStudioRolloutImportError("reset contract snapshot_kind is invalid")
    if not isinstance(record["world_seed"], int) or isinstance(record["world_seed"], bool):
        raise MineStudioRolloutImportError("reset contract world_seed must be an integer")


def _validate_rollout_spec(value: Any, *, schema_version: int) -> None:
    expected = {
        "tick_rate_hz",
        "tick_count",
        "duration_sec",
        "seed",
        "deterministic_policy",
        "action_type",
        "callback_order",
        "masked_controls",
        "learned_controls",
    }
    if schema_version == 2:
        expected.update(
            {
                "sampling_mode",
                "sampling_seed",
                "policy_sampling_reproducibility",
            }
        )
    record = _exact_mapping(value, expected, "rollout")
    ticks = record["tick_count"]
    if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks <= 0:
        raise MineStudioRolloutImportError("rollout tick_count must be positive")
    expected_order = ["neutral_mask", "post_mapper_env_action_recorder"]
    if record["callback_order"] not in [expected_order, [*expected_order, "record_video"]]:
        raise MineStudioRolloutImportError("rollout callback order is not mask-before-recorder")
    if (
        record["tick_rate_hz"] != TICK_RATE_HZ
        or record["duration_sec"] != ticks / TICK_RATE_HZ
        or record["action_type"] != "agent"
        or record["masked_controls"] != list(MASKED_CONTROLS)
        or record["learned_controls"] != list(LEARNED_CONTROLS)
    ):
        raise MineStudioRolloutImportError("rollout is not a fixed 20 Hz neutral policy")
    if not isinstance(record["seed"], int) or isinstance(record["seed"], bool):
        raise MineStudioRolloutImportError("rollout seed must be an integer")
    if schema_version == 1:
        if record["deterministic_policy"] is not True:
            raise MineStudioRolloutImportError("schema v1 rollout must use deterministic policy")
        return
    sampling_mode = record["sampling_mode"]
    expected_sampling = {
        "deterministic_argmax": (True, "argmax_no_sampling_rng"),
        "seeded_stochastic": (False, "seeded_rng_not_cross_run_validated"),
    }
    if sampling_mode not in expected_sampling:
        raise MineStudioRolloutImportError("rollout sampling_mode is unknown")
    deterministic, reproducibility = expected_sampling[sampling_mode]
    if (
        record["deterministic_policy"] is not deterministic
        or record["sampling_seed"] != record["seed"]
        or record["policy_sampling_reproducibility"] != reproducibility
    ):
        raise MineStudioRolloutImportError("rollout sampling provenance is inconsistent")


def _validate_runtime_record(value: Any) -> None:
    record = _exact_mapping(
        value, {"python", "device", "numpy", "torch", "cuda", "java"}, "runtime"
    )
    for field in ("python", "numpy", "torch", "java"):
        _nonempty(record[field], f"runtime.{field}")
    if record["device"] not in {"cpu", "cuda"}:
        raise MineStudioRolloutImportError("runtime.device must be cpu or cuda")
    if record["cuda"] is not None and not isinstance(record["cuda"], str):
        raise MineStudioRolloutImportError("runtime.cuda must be null or a string")
    if 'version "1.8' not in record["java"]:
        raise MineStudioRolloutImportError("runtime.java must identify Java 8")


def _validate_artifacts(value: Any) -> None:
    record = _exact_mapping(value, {"actions", "frames", "video"}, "artifacts")
    actions = _exact_mapping(
        record["actions"], {"path", "sha256", "size_bytes", "tick_count"}, "actions artifact"
    )
    if actions["path"] != "env_actions.jsonl":
        raise MineStudioRolloutImportError("actions artifact path must be env_actions.jsonl")
    _require_sha256(actions["sha256"], "actions.sha256")
    for field in ("size_bytes", "tick_count"):
        if not isinstance(actions[field], int) or isinstance(actions[field], bool) or actions[field] <= 0:
            raise MineStudioRolloutImportError(f"actions.{field} must be a positive integer")
    if record["frames"] is not None:
        frames = _exact_mapping(
            record["frames"], {"path", "file_count", "tree_sha256"}, "frames artifact"
        )
        if (
            frames["path"] != "frames"
            or not isinstance(frames["file_count"], int)
            or isinstance(frames["file_count"], bool)
            or frames["file_count"] != actions["tick_count"]
        ):
            raise MineStudioRolloutImportError("frames artifact is not one frame per action tick")
        _require_sha256(frames["tree_sha256"], "frames.tree_sha256")
    if record["video"] is not None:
        video = _exact_mapping(
            record["video"],
            {"path", "sha256", "size_bytes", "fps", "frame_contract", "ticks"},
            "video artifact",
        )
        if (
            video["path"] != "capture.mp4"
            or video["fps"] != TICK_RATE_HZ
            or video["frame_contract"] != "reset_plus_post_step"
            or video["ticks"] != actions["tick_count"]
            or not isinstance(video["size_bytes"], int)
            or isinstance(video["size_bytes"], bool)
            or video["size_bytes"] <= 0
        ):
            raise MineStudioRolloutImportError("video artifact does not match rollout timing")
        _require_sha256(video["sha256"], "video.sha256")
