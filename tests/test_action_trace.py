from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest
from jsonschema import validate

from mcdata.action_curriculum import ActionCurriculumError
from mcdata.action_trace import (
    ActionTraceError,
    CameraCalibration,
    build_native_trace,
    compile_native_trace,
    native_trace_sha256,
    validate_native_trace,
)
from mcdata.external_action_adapters import (
    MineStudioVPTEnvAdapter,
    OpenAIVPTRecorderV7Adapter,
)
from mcdata.render.pipeline import _trajectory_manifest

ROOT = Path(__file__).resolve().parents[1]


def _minestudio_records() -> list[dict]:
    return [
        {"forward": 1, "camera": [0.0, 1.25], "hotbar.1": 1},
        {
            "forward": 1,
            "jump": 1,
            "attack": 1,
            "camera": [-0.5, 2.0],
            "semantic_annotations": ["jump_candidate"],
        },
        {"forward": 1, "use": 1, "camera": [0.5, -1.0]},
        {"camera": [0.0, 0.0]},
    ]


def _minestudio_trace(records: list[dict] | None = None) -> dict:
    return build_native_trace(
        MineStudioVPTEnvAdapter(),
        records or _minestudio_records(),
        producer={
            "name": "MineStudio VPT",
            "version": "fixture-v1",
            "model_sha256": "a" * 64,
        },
        source_environment={
            "name": "MineStudio",
            "version": "1.1.4",
            "minecraft_version": "1.16.5",
            "action_format": "minestudio_env_action_v1",
            "action_tick_rate_hz": 20,
        },
        world={
            "seed": 42,
            "snapshot_id": "fixture-world",
            "snapshot_sha256": "b" * 64,
        },
        semantic_annotations={1: ["manual_jump_review"]},
    )


def test_minestudio_env_adapter_matches_schema_and_golden() -> None:
    trace = _minestudio_trace()
    golden = json.loads(
        (ROOT / "tests/golden/action_traces/minestudio_native_trace.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/canonical_action_trace.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert trace == golden
    assert trace["trace_sha256"] == native_trace_sha256(trace)
    assert trace["source_environment"]["minecraft_version"] == "1.16.5"
    validate(instance=trace, schema=schema)


def test_native_trace_hash_and_compilation_are_deterministic() -> None:
    first = _minestudio_trace()
    second = _minestudio_trace([dict(reversed(list(item.items()))) for item in _minestudio_records()])
    calibration = CameraCalibration(
        calibration_id="fixture-cal",
        artifact_sha256="c" * 64,
        yaw_pixels_per_degree=4.0,
        pitch_pixels_per_degree=4.0,
    )

    assert first == second
    assert compile_native_trace(first, camera_calibration=calibration) == compile_native_trace(
        second, camera_calibration=calibration
    )
    trajectory = compile_native_trace(first, camera_calibration=calibration)
    camera_events = [item for item in trajectory["events"] if "mouse_dx" in item]
    assert [(item["mouse_dx"], item["mouse_dy"]) for item in camera_events] == [
        (5, 0),
        (8, -2),
        (-4, 2),
    ]
    assert trajectory["native_trace"]["sha256"] == first["trace_sha256"]
    assert trajectory["curriculum_binding"] == {
        "status": "requires_semantic_effect_validation",
        "has_jump_input": True,
        "has_use_input": True,
        "has_attack_input": True,
    }


def test_compiled_trace_binding_propagates_into_run_manifest(tmp_path: Path) -> None:
    trace = _minestudio_trace(
        [{"forward": 1, "camera": [0.0, 1.0]}, {"camera": [0.0, 0.0]}]
    )
    trajectory = compile_native_trace(
        trace,
        camera_calibration=CameraCalibration("manifest-cal", "9" * 64, 4.0, 4.0),
    )
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps(trajectory, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = _trajectory_manifest(path, source_path=path, strategy="external_vpt")

    assert manifest is not None
    assert manifest["action_source"] == trace["action_source"]
    assert manifest["native_trace"] == {
        "schema_version": 1,
        "sha256": trace["trace_sha256"],
        "tick_rate_hz": 20,
    }
    assert manifest["curriculum_binding"]["status"] == "l1_candidate"


def test_unverified_advanced_native_trace_cannot_be_mislabeled_as_l1(
    tmp_path: Path,
) -> None:
    trajectory = compile_native_trace(
        _minestudio_trace(),
        camera_calibration=CameraCalibration("advanced-cal", "8" * 64, 4.0, 4.0),
    )
    path = tmp_path / "advanced.json"
    path.write_text(json.dumps(trajectory), encoding="utf-8")

    with pytest.raises(ActionCurriculumError, match="requires semantic effect validation"):
        _trajectory_manifest(path, source_path=path, strategy="external_vpt")


def test_compiler_requires_explicit_calibration_and_accumulates_subpixels() -> None:
    assert inspect.signature(compile_native_trace).parameters["camera_calibration"].default is (
        inspect.Parameter.empty
    )
    records = [{"forward": 1, "camera": [0.0, 0.1]} for _ in range(4)]
    records.append({"camera": [0.0, 0.0]})
    trace = _minestudio_trace(records)
    trajectory = compile_native_trace(
        trace,
        camera_calibration=CameraCalibration("subpixel", "d" * 64, 3.0, 3.0),
    )

    assert sum(item.get("mouse_dx", 0) for item in trajectory["events"]) == 1


def test_trace_tampering_and_hierarchical_policy_tokens_fail_closed() -> None:
    trace = _minestudio_trace()
    trace["ticks"][1]["camera_delta_degrees"]["yaw"] = 99.0
    with pytest.raises(ActionTraceError, match="semantic SHA"):
        validate_native_trace(trace)

    with pytest.raises(ActionTraceError, match="agent_action_to_env_action"):
        _minestudio_trace([{"buttons": [3], "camera": [60]}])


def test_learned_policy_requires_model_hash_and_source_20hz() -> None:
    kwargs = {
        "producer": {"name": "VPT", "version": "v1", "model_sha256": None},
        "source_environment": {
            "name": "MineStudio",
            "version": "1.1.4",
            "minecraft_version": "1.16.5",
            "action_format": "minestudio_env_action_v1",
            "action_tick_rate_hz": 20,
        },
        "world": {"seed": 1, "snapshot_id": "s", "snapshot_sha256": "e" * 64},
    }
    with pytest.raises(ActionTraceError, match="model_sha256"):
        build_native_trace(MineStudioVPTEnvAdapter(), [{"camera": [0, 0]}], **kwargs)

    kwargs["producer"]["model_sha256"] = "f" * 64
    kwargs["source_environment"]["action_tick_rate_hz"] = 30
    with pytest.raises(ActionTraceError, match="exactly 20 Hz"):
        build_native_trace(MineStudioVPTEnvAdapter(), [{"camera": [0, 0]}], **kwargs)


def test_openai_vpt_recorder_adapter_preserves_wrap_edges_and_human_provenance() -> None:
    records = [
        {
            "keyboard": {"keys": ["key.keyboard.w"], "newKeys": []},
            "mouse": {"buttons": []},
            "isGuiOpen": False,
            "hotbar": 0,
            "yaw": 179.0,
            "pitch": 5.0,
            "tick": 10,
        },
        {
            "keyboard": {
                "keys": ["key.keyboard.w", "key.keyboard.space"],
                "newKeys": ["key.keyboard.space"],
            },
            "mouse": {"buttons": [0]},
            "isGuiOpen": False,
            "hotbar": 0,
            "yaw": -179.0,
            "pitch": 4.0,
            "tick": 11,
        },
        {
            "keyboard": {
                "keys": ["key.keyboard.e"],
                "newKeys": ["key.keyboard.e"],
            },
            "mouse": {"buttons": [1]},
            "isGuiOpen": False,
            "hotbar": 1,
            "yaw": -178.0,
            "pitch": 4.5,
            "tick": 12,
        },
    ]
    trace = build_native_trace(
        OpenAIVPTRecorderV7Adapter(hotbar_index_base=0),
        records,
        producer={
            "name": "OpenAI contractor recorder",
            "version": "7.6",
            "model_sha256": None,
        },
        source_environment={
            "name": "OpenAI VPT recorder",
            "version": "7.6",
            "minecraft_version": "1.16.5",
            "action_format": "openai_vpt_recorder_7x_jsonl",
            "action_tick_rate_hz": 20,
        },
        world={
            "seed": "source-seed",
            "snapshot_id": "segment.zip",
            "snapshot_sha256": "d" * 64,
        },
    )

    assert trace["action_source"]["id"] == "human_demo"
    assert trace["producer"]["model_sha256"] is None
    assert trace["ticks"][1]["camera_delta_degrees"] == {"pitch": -1.0, "yaw": 2.0}
    assert trace["ticks"][2]["hotbar"] == 2
    assert {item["control"] for item in trace["ticks"][2]["edge_events"]} >= {
        "inventory",
        "hotbar",
        "use",
    }


def test_vpt_recorder_rejects_gui_unknown_key_and_implicit_hotbar_base() -> None:
    with pytest.raises(ActionTraceError, match="explicitly 0 or 1"):
        OpenAIVPTRecorderV7Adapter(hotbar_index_base=2)
    adapter = OpenAIVPTRecorderV7Adapter(hotbar_index_base=0)
    base = {
        "keyboard": {"keys": []},
        "mouse": {"buttons": []},
        "isGuiOpen": True,
        "hotbar": 0,
        "yaw": 0,
        "pitch": 0,
        "tick": 0,
    }
    with pytest.raises(ActionTraceError, match="opens a GUI"):
        adapter.adapt([base])
    unknown = copy.deepcopy(base)
    unknown["isGuiOpen"] = False
    unknown["keyboard"]["keys"] = ["key.keyboard.q"]
    with pytest.raises(ActionTraceError, match="unsupported held keys"):
        adapter.adapt([unknown])
