#!/usr/bin/env python3
"""Pinned MineStudio/VPT neutral rollout recorder.

This program runs in a separate MineStudio environment. It deliberately has no import-time
dependency on MineStudio so the repository can lint and unit-test its contracts without installing
the simulator, PyTorch, the engine, or the checkpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MINESTUDIO_VERSION = "1.1.5"
MINESTUDIO_REPOSITORY = "https://github.com/CraftJarvis/MineStudio.git"
MINESTUDIO_COMMIT = "278aa8553668d591339dbf30d281594ed06ee882"
SOURCE_MINECRAFT_VERSION = "1.16.5"
TICK_RATE_HZ = 20

MODEL_REPOSITORY = "CraftJarvis/MineStudio_VPT.foundation_model_1x"
MODEL_REVISION = "17a5f43b30c4f734489902fdc6a55bf47781be3a"
MODEL_FILENAME = "model.safetensors"
MODEL_SHA256 = "475fbd0df655ad77c3e3f602d157f4273032bff8e6e82c3863a992f5b03753f9"
MODEL_CONFIG_SHA256 = "d088a1f68ca44cac73d0efe1af7b7df4ade5994b39360da7fe74cfb6b282cbd2"
MODEL_LICENSE_STATUS = "license_unknown"
MODEL_USAGE_SCOPE = "research_only"

ENGINE_REPOSITORY = "CraftJarvis/SimulatorEngine"
ENGINE_REVISION = "48d4809cfddc7e2b85295e8c39b3c5e8c6d46ae7"
ENGINE_ARCHIVE_FILENAME = "engine.zip"
ENGINE_ARCHIVE_SHA256 = "293fac6ac72245b3365dce0e8bfbb6396fb94df29b23b6538f3bd7e2eec13ec6"
ENGINE_RUNTIME_JAR = Path("engine/build/libs/mcprec-6.13.jar")

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


class RolloutError(ValueError):
    """Raised when a rollout would not match the pinned neutral contract."""


@dataclass(frozen=True)
class RunnerConfig:
    minestudio_repo: Path
    minestudio_home: Path
    model_dir: Path
    reset_contract: Path
    output_dir: Path
    ticks: int
    device: str
    save_frames: bool
    save_video: bool


@dataclass(frozen=True)
class RuntimeBindings:
    minecraft_sim: Any
    callback_base: type
    mask_callback: type
    record_callback: type
    vpt_policy: Any
    numpy: Any
    torch: Any
    minestudio_module_file: str


def run_rollout(config: RunnerConfig, *, runtime: RuntimeBindings | None = None) -> dict[str, Any]:
    """Execute one fixed-length neutral rollout and atomically publish its artifact directory."""
    validated = _validate_inputs(config)
    bindings = runtime or _load_runtime(config.minestudio_home)
    _validate_runtime(bindings, config.minestudio_repo)
    _set_determinism(bindings, validated["reset_contract"]["world_seed"])

    staging = config.output_dir.with_name(f".{config.output_dir.name}.tmp")
    if config.output_dir.exists() or staging.exists():
        raise RolloutError(
            f"output and staging paths must not exist: {config.output_dir}, {staging}"
        )
    staging.mkdir(parents=True)
    sim: Any = None
    try:
        (staging / "reset_contract.json").write_bytes(validated["reset_contract_bytes"])
        recorder = _make_action_recorder(bindings.callback_base)
        mask = bindings.mask_callback(**{control: 0 for control in MASKED_CONTROLS})
        callbacks: list[Any] = [mask, recorder]
        if config.save_video:
            callbacks.append(
                bindings.record_callback(
                    record_path=str(staging / "video"),
                    fps=TICK_RATE_HZ,
                    frame_type="obs",
                    recording=True,
                    record_actions=False,
                )
            )
        reset = validated["reset_contract"]
        sim = bindings.minecraft_sim(
            action_type="agent",
            obs_size=tuple(reset["obs_size"]),
            render_size=tuple(reset["render_size"]),
            seed=reset["world_seed"],
            inventory=dict(reset["inventory"]),
            preferred_spawn_biome=reset["preferred_spawn_biome"],
            num_empty_frames=reset["num_empty_frames"],
            callbacks=callbacks,
        )
        if list(sim.callbacks[:2]) != [mask, recorder]:
            raise RolloutError("MineStudio changed the required mask-before-recorder callback order")
        policy = bindings.vpt_policy.from_pretrained(str(config.model_dir))
        policy = policy.to(config.device)
        policy.eval()
        observations, _ = sim.reset()
        memory = None
        frames_dir = staging / "frames"
        if config.save_frames:
            frames_dir.mkdir()
        for tick in range(config.ticks):
            agent_action, memory = policy.get_action(
                observations,
                memory,
                deterministic=True,
                input_shape="*",
            )
            observations, _, terminated, truncated, _ = sim.step(agent_action)
            if terminated or truncated:
                raise RolloutError(f"MineStudio terminated before fixed tick {tick + 1}")
            if config.save_frames:
                _write_frame(bindings.numpy, frames_dir / f"{tick:06d}.npy", observations)
        _close_sim(sim)
        sim = None

        actions = list(recorder.actions)
        if len(actions) != config.ticks:
            raise RolloutError(
                f"post-mapper recorder produced {len(actions)} actions for {config.ticks} ticks"
            )
        actions_path = staging / "env_actions.jsonl"
        _write_jsonl(actions_path, actions)
        video_artifact = _finalize_video(staging, config.ticks) if config.save_video else None
        frame_artifact = _directory_artifact(staging, frames_dir) if config.save_frames else None
        manifest = _build_manifest(
            config,
            validated,
            bindings,
            actions_path=actions_path,
            frame_artifact=frame_artifact,
            video_artifact=video_artifact,
        )
        _write_json(staging / "rollout_manifest.json", manifest)
        os.replace(staging, config.output_dir)
        return manifest
    except BaseException as exc:
        if sim is not None:
            try:
                _close_sim(sim)
            except BaseException as close_exc:
                exc.add_note(f"MineStudio close also failed: {close_exc}")
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _validate_inputs(config: RunnerConfig) -> dict[str, Any]:
    if not isinstance(config.ticks, int) or isinstance(config.ticks, bool) or config.ticks <= 0:
        raise RolloutError("ticks must be a positive integer")
    if config.device not in {"cpu", "cuda"}:
        raise RolloutError("device must be exactly 'cpu' or 'cuda'")
    _verify_minestudio_checkout(config.minestudio_repo)
    engine_archive = config.minestudio_home / ENGINE_ARCHIVE_FILENAME
    engine_jar = config.minestudio_home / ENGINE_RUNTIME_JAR
    _require_file_sha(engine_archive, ENGINE_ARCHIVE_SHA256, "SimulatorEngine archive")
    engine_jar_sha256 = _file_sha256(engine_jar)
    model_path = config.model_dir / MODEL_FILENAME
    _require_file_sha(model_path, MODEL_SHA256, "VPT model")
    model_config = config.model_dir / "config.json"
    _require_file_sha(model_config, MODEL_CONFIG_SHA256, "VPT model config")
    reset_bytes = _read_bytes(config.reset_contract, "reset contract")
    try:
        reset_contract = json.loads(
            reset_bytes,
            object_pairs_hook=_unique_json_object,
            parse_constant=_invalid_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise RolloutError(f"reset contract is not JSON: {config.reset_contract}: {exc}") from exc
    _validate_reset_contract(reset_contract)
    return {
        "engine_archive_sha256": ENGINE_ARCHIVE_SHA256,
        "engine_jar_sha256": engine_jar_sha256,
        "model_sha256": MODEL_SHA256,
        "model_config_sha256": MODEL_CONFIG_SHA256,
        "reset_contract": reset_contract,
        "reset_contract_bytes": reset_bytes,
        "reset_contract_sha256": hashlib.sha256(reset_bytes).hexdigest(),
        "java_version": _java8_version(),
    }


def _verify_minestudio_checkout(repository: Path) -> None:
    if not repository.is_dir():
        raise RolloutError(f"MineStudio checkout is missing: {repository}")
    commit = _git(repository, "rev-parse", "HEAD")
    if commit != MINESTUDIO_COMMIT:
        raise RolloutError(
            f"MineStudio checkout is {commit}, expected pinned commit {MINESTUDIO_COMMIT}"
        )
    if _git(repository, "status", "--porcelain", "--untracked-files=no"):
        raise RolloutError(f"MineStudio checkout has tracked modifications: {repository}")


def _load_runtime(minestudio_home: Path) -> RuntimeBindings:
    os.environ["MINESTUDIO_DIR"] = str(minestudio_home)
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    simulator = importlib.import_module("minestudio.simulator")
    callbacks = importlib.import_module("minestudio.simulator.callbacks")
    models = importlib.import_module("minestudio.models")
    numpy = importlib.import_module("numpy")
    torch = importlib.import_module("torch")
    return RuntimeBindings(
        minecraft_sim=simulator.MinecraftSim,
        callback_base=callbacks.MinecraftCallback,
        mask_callback=callbacks.MaskActionsCallback,
        record_callback=callbacks.RecordCallback,
        vpt_policy=models.VPTPolicy,
        numpy=numpy,
        torch=torch,
        minestudio_module_file=str(simulator.__file__),
    )


def _validate_runtime(runtime: RuntimeBindings, minestudio_repo: Path) -> None:
    try:
        version = importlib.metadata.version("minestudio")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RolloutError("the isolated runtime has no installed minestudio distribution") from exc
    if version != MINESTUDIO_VERSION:
        raise RolloutError(f"minestudio version is {version}, expected {MINESTUDIO_VERSION}")
    if not all(
        hasattr(runtime, field)
        for field in (
            "minecraft_sim",
            "callback_base",
            "mask_callback",
            "record_callback",
            "vpt_policy",
            "numpy",
            "torch",
            "minestudio_module_file",
        )
    ):
        raise RolloutError("MineStudio runtime bindings are incomplete")
    module_path = Path(runtime.minestudio_module_file).resolve()
    try:
        module_path.relative_to(minestudio_repo.resolve())
    except ValueError as exc:
        raise RolloutError(
            f"imported MineStudio module is not from pinned checkout: {module_path}"
        ) from exc


def _set_determinism(runtime: RuntimeBindings, seed: int) -> None:
    random.seed(seed)
    runtime.numpy.random.seed(seed)
    runtime.torch.manual_seed(seed)
    if runtime.torch.cuda.is_available():
        runtime.torch.cuda.manual_seed_all(seed)
    runtime.torch.use_deterministic_algorithms(True)
    runtime.torch.backends.cudnn.benchmark = False
    runtime.torch.backends.cudnn.deterministic = True


def _make_action_recorder(callback_base: type) -> Any:
    class PostMapperEnvActionRecorder(callback_base):
        def __init__(self) -> None:
            super().__init__()
            self.actions: list[dict[str, Any]] = []

        def before_step(self, sim: Any, action: Mapping[str, Any]) -> Mapping[str, Any]:
            del sim
            self.actions.append(_canonical_env_action(action, len(self.actions)))
            return action

    return PostMapperEnvActionRecorder()


def _canonical_env_action(action: Mapping[str, Any], source_tick: int) -> dict[str, Any]:
    if not isinstance(action, Mapping) or set(action) != {*ENV_CONTROLS, "camera"}:
        extra = sorted(set(action) - {*ENV_CONTROLS, "camera"}) if isinstance(action, Mapping) else []
        missing = sorted({*ENV_CONTROLS, "camera"} - set(action)) if isinstance(action, Mapping) else []
        raise RolloutError(f"post-mapper env action fields changed; missing={missing}, extra={extra}")
    result: dict[str, Any] = {"source_tick": source_tick}
    for control in ENV_CONTROLS:
        result[control] = _binary_scalar(action[control], f"action {source_tick}.{control}")
    for control in MASKED_CONTROLS:
        if result[control] != 0:
            raise RolloutError(f"neutral mask did not clear action {source_tick}.{control}")
    camera = _plain_sequence(action["camera"], f"action {source_tick}.camera")
    if len(camera) != 2:
        raise RolloutError(f"action {source_tick}.camera must contain [pitch,yaw]")
    result["camera"] = [
        _finite_scalar(camera[0], f"action {source_tick}.camera[0]"),
        _finite_scalar(camera[1], f"action {source_tick}.camera[1]"),
    ]
    return result


def _validate_reset_contract(value: Any) -> None:
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
    if not isinstance(value, Mapping) or set(value) != expected:
        raise RolloutError("reset contract has an unstable field set")
    if value["schema_version"] != 1:
        raise RolloutError("reset contract schema_version must be 1")
    if not isinstance(value["contract_id"], str) or not value["contract_id"]:
        raise RolloutError("reset contract_id must be non-empty")
    if value["source_minecraft_version"] != SOURCE_MINECRAFT_VERSION:
        raise RolloutError("reset contract must identify the MineStudio 1.16.5 source engine")
    if value["snapshot_kind"] != "procedural_seeded_reset_only":
        raise RolloutError("Phase 2 runner only supports procedural_seeded_reset_only")
    if not isinstance(value["world_seed"], int) or isinstance(value["world_seed"], bool):
        raise RolloutError("reset world_seed must be an integer")
    if value["obs_size"] != [128, 128] or value["render_size"] != [640, 360]:
        raise RolloutError("neutral VPT reset requires obs_size 128x128 and render_size 640x360")
    empty_frames = value["num_empty_frames"]
    if not isinstance(empty_frames, int) or isinstance(empty_frames, bool) or empty_frames < 0:
        raise RolloutError("reset num_empty_frames must be a nonnegative integer")
    biome = value["preferred_spawn_biome"]
    if biome is not None and (not isinstance(biome, str) or not biome):
        raise RolloutError("preferred_spawn_biome must be null or a non-empty string")
    inventory = value["inventory"]
    if not isinstance(inventory, Mapping) or any(not isinstance(key, str) for key in inventory):
        raise RolloutError("reset inventory must be a string-keyed mapping")


def _build_manifest(
    config: RunnerConfig,
    validated: Mapping[str, Any],
    runtime: RuntimeBindings,
    *,
    actions_path: Path,
    frame_artifact: Mapping[str, Any] | None,
    video_artifact: Mapping[str, Any] | None,
) -> dict[str, Any]:
    reset = validated["reset_contract"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "complete",
        "source_environment": {
            "name": "MineStudio",
            "version": MINESTUDIO_VERSION,
            "repository": MINESTUDIO_REPOSITORY,
            "repository_commit": MINESTUDIO_COMMIT,
            "minecraft_version": SOURCE_MINECRAFT_VERSION,
            "action_type": "agent",
            "action_format": "minestudio_post_mapper_env_action_v1",
            "action_tick_rate_hz": TICK_RATE_HZ,
        },
        "producer": {
            "name": "MineStudio VPT foundation_model_1x",
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "filename": MODEL_FILENAME,
            "model_sha256": validated["model_sha256"],
            "config_sha256": validated["model_config_sha256"],
            "license_status": MODEL_LICENSE_STATUS,
            "usage_scope": MODEL_USAGE_SCOPE,
        },
        "engine": {
            "repository": ENGINE_REPOSITORY,
            "revision": ENGINE_REVISION,
            "archive_filename": ENGINE_ARCHIVE_FILENAME,
            "archive_sha256": validated["engine_archive_sha256"],
            "runtime_jar": ENGINE_RUNTIME_JAR.as_posix(),
            "runtime_jar_sha256": validated["engine_jar_sha256"],
        },
        "reset_contract": {
            "path": "reset_contract.json",
            "sha256": validated["reset_contract_sha256"],
            "contract_id": reset["contract_id"],
            "snapshot_kind": reset["snapshot_kind"],
            "world_seed": reset["world_seed"],
        },
        "rollout": {
            "tick_rate_hz": TICK_RATE_HZ,
            "tick_count": config.ticks,
            "duration_sec": config.ticks / TICK_RATE_HZ,
            "seed": reset["world_seed"],
            "deterministic_policy": True,
            "action_type": "agent",
            "callback_order": [
                "neutral_mask",
                "post_mapper_env_action_recorder",
                *(["record_video"] if config.save_video else []),
            ],
            "masked_controls": list(MASKED_CONTROLS),
            "learned_controls": list(LEARNED_CONTROLS),
        },
        "runtime": {
            "python": platform.python_version(),
            "device": config.device,
            "numpy": str(runtime.numpy.__version__),
            "torch": str(runtime.torch.__version__),
            "cuda": str(runtime.torch.version.cuda) if runtime.torch.version.cuda else None,
            "java": validated["java_version"],
        },
        "artifacts": {
            "actions": _file_artifact(actions_path, actions_path.parent),
            "frames": frame_artifact,
            "video": video_artifact,
        },
    }
    payload["artifacts"]["actions"]["tick_count"] = config.ticks
    payload["rollout_sha256"] = _semantic_sha256(payload, exclude="rollout_sha256")
    return payload


def _write_frame(numpy: Any, path: Path, observations: Mapping[str, Any]) -> None:
    if not isinstance(observations, Mapping) or set(observations) != {"image"}:
        raise RolloutError("VPT observation must contain exactly the image field")
    with path.open("wb") as handle:
        numpy.save(handle, observations["image"], allow_pickle=False)


def _finalize_video(staging: Path, ticks: int) -> dict[str, Any]:
    candidates = sorted((staging / "video").glob("*.mp4"))
    if len(candidates) != 1:
        raise RolloutError(f"RecordCallback produced {len(candidates)} videos, expected exactly one")
    final = staging / "capture.mp4"
    os.replace(candidates[0], final)
    (staging / "video").rmdir()
    artifact = _file_artifact(final, staging)
    artifact.update({"fps": TICK_RATE_HZ, "frame_contract": "reset_plus_post_step", "ticks": ticks})
    return artifact


def _directory_artifact(staging: Path, directory: Path) -> dict[str, Any]:
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    records = [_file_artifact(path, staging) for path in files]
    return {
        "path": directory.relative_to(staging).as_posix(),
        "file_count": len(records),
        "tree_sha256": _semantic_sha256(records),
    }


def _file_artifact(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    rendered = "".join(
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for record in records
    )
    path.write_text(rendered, encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _close_sim(sim: Any) -> None:
    sim.close()


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _java8_version() -> str:
    completed = subprocess.run(
        ["java", "-version"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rendered = " ".join((*completed.stdout.splitlines(), *completed.stderr.splitlines())).strip()
    if 'version "1.8' not in rendered:
        raise RolloutError(f"MineStudio source engine requires Java 8; observed: {rendered!r}")
    return rendered


def _require_file_sha(path: Path, expected: str, label: str) -> None:
    actual = _file_sha256(path)
    if actual != expected:
        raise RolloutError(f"{label} SHA-256 is {actual}, expected pinned {expected}: {path}")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise RolloutError(f"required file is missing: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RolloutError(f"could not read {label} {path}: {exc}") from exc


def _binary_scalar(value: Any, label: str) -> int:
    value = _scalar(value)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in {0, 1}:
        return value
    raise RolloutError(f"{label} must be a binary scalar")


def _finite_scalar(value: Any, label: str) -> float:
    value = _scalar(value)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise RolloutError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise RolloutError(f"{label} must be finite")
    return 0.0 if result == 0 else result


def _scalar(value: Any) -> Any:
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (TypeError, ValueError):
            return value
    return value


def _plain_sequence(value: Any, label: str) -> list[Any]:
    if hasattr(value, "tolist") and callable(value.tolist):
        value = value.tolist()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RolloutError(f"{label} must be a sequence")
    return list(value)


def _semantic_sha256(value: Any, *, exclude: str | None = None) -> str:
    if exclude is not None and isinstance(value, Mapping):
        value = {key: item for key, item in value.items() if key != exclude}
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RolloutError(f"reset contract repeats JSON key {key!r}")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> Any:
    raise RolloutError(f"reset contract contains non-finite JSON constant {value}")


def _parse_args(argv: Sequence[str] | None = None) -> RunnerConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minestudio-repo", type=Path, required=True)
    parser.add_argument("--minestudio-home", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reset-contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ticks", type=int, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--save-frames", action="store_true")
    parser.add_argument("--save-video", action="store_true")
    args = parser.parse_args(argv)
    return RunnerConfig(
        minestudio_repo=args.minestudio_repo.resolve(),
        minestudio_home=args.minestudio_home.resolve(),
        model_dir=args.model_dir.resolve(),
        reset_contract=args.reset_contract.resolve(),
        output_dir=args.output_dir.resolve(),
        ticks=args.ticks,
        device=args.device,
        save_frames=args.save_frames,
        save_video=args.save_video,
    )


def main(argv: Sequence[str] | None = None) -> int:
    try:
        manifest = run_rollout(_parse_args(argv))
    except (RolloutError, OSError, subprocess.SubprocessError) as exc:
        print(f"neutral rollout failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"rollout_sha256": manifest["rollout_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
