from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import validate

from mcdata.action_source import (
    ACTION_SOURCES,
    ActionSourceError,
    action_source_index,
    declared_action_source,
    resolve_manifest_action_source,
    validate_solaris_rollout_binding,
)
from mcdata.action_trace import (
    ActionTraceError,
    CameraCalibration,
    build_native_trace,
    compile_native_trace,
)
from mcdata.dataset import DatasetValidationError
from mcdata.dataset_support.episodes import _validate_manifest
from mcdata.external_action_adapters import SolarisNormalizedControllerAdapter
from mcdata.reference_replay import ReferenceReplayError
from mcdata.render.pipeline import _trajectory_manifest
from mcdata.solaris_rollout_import import (
    SOLARIS_COMMIT,
    SolarisRolloutImportError,
    import_solaris_rollout,
    solaris_rollout_manifest_sha256,
    validate_solaris_rollout_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/solaris_controller_rollout"
CALIBRATION = ROOT / "tests/fixtures/solaris_target_calibration.json"
GOLDEN_TRACE = ROOT / "tests/golden/action_traces/solaris_controller_trace.json"
GOLDEN_TRAJECTORY = ROOT / "tests/golden/action_traces/solaris_controller_trajectory.json"


def _manifest() -> dict[str, Any]:
    return json.loads((FIXTURE / "rollout_manifest.json").read_text(encoding="utf-8"))


def _records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURE / "normalized_controller_actions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_solaris_manifest_matches_schema_pin_and_semantic_hash() -> None:
    manifest = _manifest()
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/solaris_controller_rollout.schema.json").read_text(
            encoding="utf-8"
        )
    )

    validate(instance=manifest, schema=schema)
    assert validate_solaris_rollout_manifest(manifest) == manifest
    assert manifest["rollout_sha256"] == solaris_rollout_manifest_sha256(manifest)
    assert manifest["source_environment"]["repository_commit"] == SOLARIS_COMMIT
    assert manifest["source_environment"]["minecraft_version"] == "1.21"
    assert manifest["source_timing_status"] == "source_timing_not_yet_validated"
    assert manifest["target_replay_status"] == "target_replay_not_yet_validated"


def test_solaris_import_matches_quarantined_goldens(tmp_path: Path) -> None:
    trace_out = tmp_path / "trace.json"
    trajectory_out = tmp_path / "trajectory.json"

    result = import_solaris_rollout(
        FIXTURE,
        expected_rollout_sha256=_manifest()["rollout_sha256"],
        expected_ticks=5,
        camera_calibration_path=CALIBRATION,
        trace_out=trace_out,
        trajectory_out=trajectory_out,
    )

    assert trace_out.read_bytes() == GOLDEN_TRACE.read_bytes()
    assert trajectory_out.read_bytes() == GOLDEN_TRAJECTORY.read_bytes()
    assert result == {
        "rollout_sha256": _manifest()["rollout_sha256"],
        "trace_sha256": json.loads(trace_out.read_text())["trace_sha256"],
        "tick_count": 5,
        "trajectory_event_count": 19,
        "source_minecraft_version": "1.21",
        "target_minecraft_version": "26.2",
        "source_timing_status": "source_timing_not_yet_validated",
        "target_replay_status": "target_replay_not_yet_validated",
    }
    trace = json.loads(trace_out.read_text())
    trajectory = json.loads(trajectory_out.read_text())
    validate(
        instance=trace,
        schema=json.loads(
            (ROOT / "src/mcdata/schemas/canonical_action_trace.schema.json").read_text()
        ),
    )
    assert trace["action_source"] == declared_action_source("scripted_skill_agent")
    assert trace["adapter"]["parameters"]["solaris_provenance"] == {
        "action_artifact_sha256": _manifest()["artifacts"]["actions"]["sha256"],
        "episode_id": 7,
        "episode_role": "Alpha",
        "episode_type": "walkLook",
        "repository": "https://github.com/solaris-wm/solaris-engine",
        "repository_commit": SOLARIS_COMMIT,
        "shared_rng_sha256": trajectory["solaris_rollout_binding"]["shared_rng_sha256"],
        "source_timing_status": "source_timing_not_yet_validated",
        "target_replay_status": "target_replay_not_yet_validated",
        "world_snapshot_sha256": _manifest()["world"]["snapshot_sha256"],
    }
    assert validate_solaris_rollout_binding(
        trajectory["solaris_rollout_binding"]
    ) == trajectory["solaris_rollout_binding"]
    assert trajectory["curriculum_binding"] == {
        "status": "requires_semantic_effect_validation",
        "has_jump_input": True,
        "has_use_input": True,
        "has_attack_input": True,
    }


def test_place_success_and_unrepresentable_actions_remain_annotation_only() -> None:
    ticks = SolarisNormalizedControllerAdapter().adapt(_records())

    placement = ticks[2]
    assert placement["use"] is False
    assert all(edge["control"] != "use" for edge in placement["edge_events"])
    assert placement["semantic_annotations"] == [
        "solaris_annotation_only:place_block_success:placed-0002"
    ]
    annotation_tick = ticks[3]
    assert {
        item.split(":")[1]
        for item in annotation_tick["semantic_annotations"]
        if item.startswith("solaris_annotation_only:")
    } == {"dismount", "mine", "mount", "place_entity"}

    source_record = _records()[2]
    annotation_only_record = {
        **source_record,
        "tick": 0,
        "held": {key: False for key in source_record["held"]},
        "camera_delta_degrees": {"pitch": 0.0, "yaw": 0.0},
    }
    trace = build_native_trace(
        SolarisNormalizedControllerAdapter(),
        [annotation_only_record],
        producer={
            "name": "Solaris fixture",
            "version": SOLARIS_COMMIT,
            "model_sha256": None,
        },
        source_environment={
            "name": "Solaris Engine",
            "version": SOLARIS_COMMIT,
            "minecraft_version": "1.21",
            "action_format": "solaris_normalized_controller_boundary_v1",
            "action_tick_rate_hz": 20,
        },
        world={"seed": 1, "snapshot_id": "fixture", "snapshot_sha256": "a" * 64},
    )
    trajectory = compile_native_trace(
        trace,
        camera_calibration=CameraCalibration("fixture", "b" * 64, 4.0, 4.0),
    )
    assert trajectory["curriculum_binding"] == {
        "status": "l1_candidate",
        "has_jump_input": False,
        "has_use_input": False,
        "has_attack_input": False,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda records: records[0].update(renderTime=50), "unstable normalized field"),
        (lambda records: records[1].update(tick=9), "contiguous and zero-based"),
        (lambda records: records[2].update(attack=True), "consecutive ticks"),
        (
            lambda records: records[2]["annotation_only_events"][0].update(mode="replay"),
            "must be annotation_only",
        ),
        (
            lambda records: records[2]["annotation_only_events"][0].update(
                action="place_block"
            ),
            "unsupported semantic action",
        ),
        (
            lambda records: records[0].update(
                semantic_annotations=["z:last", "a:first"]
            ),
            "semantic annotations are not canonical",
        ),
    ],
)
def test_solaris_adapter_fails_closed_on_ambiguous_input(
    mutation: Any, message: str
) -> None:
    records = _records()
    if message == "consecutive ticks":
        records[1]["attack"] = True
    mutation(records)
    with pytest.raises(ActionTraceError, match=message):
        SolarisNormalizedControllerAdapter().adapt(records)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["source_environment"].update(
                repository_commit="mutable-main"
            ),
            "audited pin",
        ),
        (
            lambda value: value["source_environment"].update(minecraft_version="1.21.1"),
            "audited pin",
        ),
        (
            lambda value: value["episode"]["shared_rng"].update(
                episode_seed="different"
            ),
            "base_seed_episode_id",
        ),
        (
            lambda value: value.update(source_timing_status="validated"),
            "not fail-closed",
        ),
    ],
)
def test_solaris_manifest_provenance_tampering_fails_closed(
    mutation: Any, message: str
) -> None:
    manifest = _manifest()
    mutation(manifest)
    manifest["rollout_sha256"] = solaris_rollout_manifest_sha256(manifest)
    with pytest.raises(SolarisRolloutImportError, match=message):
        validate_solaris_rollout_manifest(manifest)


def test_solaris_import_rejects_wrong_trust_root_and_artifact_bytes(tmp_path: Path) -> None:
    common = {
        "rollout_dir": FIXTURE,
        "expected_rollout_sha256": _manifest()["rollout_sha256"],
        "expected_ticks": 5,
        "camera_calibration_path": CALIBRATION,
        "trace_out": tmp_path / "trace.json",
        "trajectory_out": tmp_path / "trajectory.json",
    }
    with pytest.raises(SolarisRolloutImportError, match="expected-rollout-sha256"):
        import_solaris_rollout(**{**common, "expected_rollout_sha256": "f" * 64})
    with pytest.raises(SolarisRolloutImportError, match="has 5 ticks"):
        import_solaris_rollout(**{**common, "expected_ticks": 6})

    copied = tmp_path / "tampered"
    copied.mkdir()
    for source in FIXTURE.iterdir():
        (copied / source.name).write_bytes(source.read_bytes())
    actions = copied / "normalized_controller_actions.jsonl"
    actions.write_bytes(actions.read_bytes().replace(b'"yaw":1.25', b'"yaw":1.26', 1))
    with pytest.raises(SolarisRolloutImportError, match="size/SHA-256 mismatch"):
        import_solaris_rollout(**{**common, "rollout_dir": copied})


def test_solaris_trajectory_is_rejected_by_replay_and_dataset(tmp_path: Path) -> None:
    with pytest.raises(ReferenceReplayError, match="source_timing_status"):
        _trajectory_manifest(GOLDEN_TRAJECTORY, source_path=GOLDEN_TRAJECTORY, strategy=None)

    binding = json.loads(GOLDEN_TRAJECTORY.read_text())["solaris_rollout_binding"]
    manifest = {
        "profile": {"name": "fixture"},
        "git": {},
        "resources": {"resourcepack_runtime": {}},
        "world": {"seed": 1, "profile": "fixture-world", "state": {"time": "noon"}},
        "trajectory": {"sha256": "a" * 64, "solaris_rollout_binding": binding},
        "mc_version": "26.2",
    }
    with pytest.raises(DatasetValidationError, match="source timing and target replay"):
        _validate_manifest(manifest, tmp_path / "unvalidated-solaris-run")


def test_scripted_skill_agent_is_native_trace_bound_and_indexable() -> None:
    assert "scripted_skill_agent" in ACTION_SOURCES
    index = action_source_index(
        [
            {
                "episode_id": "solaris-fixture",
                "action_source": declared_action_source("scripted_skill_agent"),
            }
        ]
    )
    assert index["scripted_skill_agent"]["episode_ids"] == ["solaris-fixture"]
    trajectory = {
        "type": "native_action_trace_replay",
        "action_source": declared_action_source("scripted_skill_agent"),
        "native_trace": {"schema_version": 1, "sha256": "a" * 64, "tick_rate_hz": 20},
        "curriculum_binding": {
            "status": "l1_candidate",
            "has_jump_input": False,
            "has_use_input": False,
            "has_attack_input": False,
        },
    }
    assert resolve_manifest_action_source({"trajectory": trajectory})["id"] == (
        "scripted_skill_agent"
    )
    del trajectory["native_trace"]
    with pytest.raises(ActionSourceError, match="requires a canonical native_trace"):
        resolve_manifest_action_source({"trajectory": trajectory})


def test_solaris_binding_is_representable_in_run_manifest_schema() -> None:
    manifest = json.loads(
        (ROOT / "docs/examples/run_manifest_example.json").read_text(encoding="utf-8")
    )
    trajectory = json.loads(GOLDEN_TRAJECTORY.read_text())
    manifest["trajectory"].update(
        {
            "action_source": trajectory["action_source"],
            "native_trace": trajectory["native_trace"],
            "curriculum_binding": trajectory["curriculum_binding"],
            "camera_calibration": trajectory["camera_calibration"],
            "solaris_rollout_binding": trajectory["solaris_rollout_binding"],
            "source_path": "quarantine/solaris_controller_trajectory.json",
            "source_sha256": "b" * 64,
        }
    )
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/manifest.schema.json").read_text(encoding="utf-8")
    )
    validate(instance=manifest, schema=schema)
