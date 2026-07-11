from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mcdata import cli
from mcdata.reference_replay import (
    ReferenceReplayError,
    validate_reference_replay_trajectory,
)
from mcdata.render import pipeline

ROOT = Path(__file__).resolve().parents[1]
TRAJECTORY = ROOT / "tests/golden/action_traces/minestudio_neutral_phase2_trajectory.json"
TARGET_PROFILE = "fixture_neutral_reference"


def _trajectory() -> dict[str, Any]:
    return json.loads(TRAJECTORY.read_text(encoding="utf-8"))


def test_run_trajectory_file_is_strictly_mutually_exclusive_with_strategy() -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--strategy",
            "ground_astar_loop",
            "--trajectory-file",
            str(TRAJECTORY),
            "--replay-actions",
        ],
    )

    assert result.exit_code != 0
    assert "--strategy and --trajectory-file are mutually exclusive" in result.output


def test_run_trajectory_file_requires_replay_and_reaches_launch(monkeypatch) -> None:
    missing_replay = CliRunner().invoke(
        cli.app,
        ["run", "--trajectory-file", str(TRAJECTORY)],
    )
    assert missing_replay.exit_code != 0
    assert "--trajectory-file requires --replay-actions" in missing_replay.output

    calls: list[tuple[Path, str, dict[str, Any]]] = []

    def fake_launch(root: Path, profile: str, **kwargs: Any) -> None:
        calls.append((root, profile, kwargs))

    monkeypatch.setattr(cli, "launch_profile", fake_launch)
    result = CliRunner().invoke(
        cli.app,
        [
            "run",
            "--root",
            str(ROOT),
            "--profile",
            TARGET_PROFILE,
            "--trajectory-file",
            str(TRAJECTORY),
            "--replay-actions",
            "--game-version",
            "26.2",
        ],
    )

    assert result.exit_code == 0
    assert len(calls) == 1
    assert calls[0][1] == TARGET_PROFILE
    assert calls[0][2]["strategy"] is None
    assert calls[0][2]["trajectory_path"] == TRAJECTORY.resolve()
    assert calls[0][2]["replay_actions"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(type="astar_walk"), "native_action_trace_replay"),
        (
            lambda value: value.pop("external_rollout_binding"),
            "top-level field set",
        ),
        (
            lambda value: value["external_rollout_binding"].update(
                target_minecraft_version="1.16.5"
            ),
            "target Minecraft version",
        ),
        (
            lambda value: value["external_rollout_binding"].update(
                compatibility_status="accepted"
            ),
            "compatibility status",
        ),
        (
            lambda value: value["curriculum_binding"].update(has_jump_input=True),
            "curriculum status disagrees",
        ),
        (
            lambda value: value["native_trace"].update(sha256="F" * 64),
            "native_trace sha256",
        ),
        (
            lambda value: value["camera_calibration"].update(artifact_sha256="a" * 64),
            "calibration SHA disagrees",
        ),
        (
            lambda value: value["action_source"].update(id="scripted_astar"),
            "learned_visual_policy",
        ),
        (
            lambda value: value["events"][0].pop("native_trace_tick"),
            "canonical L1 input",
        ),
    ],
)
def test_reference_replay_contract_tampering_fails_closed(
    mutation: Any, message: str
) -> None:
    trajectory = copy.deepcopy(_trajectory())
    mutation(trajectory)

    with pytest.raises(ReferenceReplayError, match=message):
        validate_reference_replay_trajectory(
            trajectory,
            target_profile=TARGET_PROFILE,
            target_minecraft_version="26.2",
        )


def test_reference_replay_rejects_profile_and_runtime_version_mismatch() -> None:
    with pytest.raises(ReferenceReplayError, match="selected profile"):
        validate_reference_replay_trajectory(
            _trajectory(),
            target_profile="different_profile",
            target_minecraft_version="26.2",
        )
    with pytest.raises(ReferenceReplayError, match="requires Minecraft 26.2"):
        validate_reference_replay_trajectory(
            _trajectory(),
            target_profile=TARGET_PROFILE,
            target_minecraft_version="26.1",
        )


def test_reference_replay_plan_preserves_all_bindings_in_manifest_and_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "imported_trajectory.json"
    source.write_bytes(TRAJECTORY.read_bytes())
    instances = tmp_path / "instances"
    runs = tmp_path / "runs"
    (instances / TARGET_PROFILE).mkdir(parents=True)
    monkeypatch.setenv("MCDATA_WORK_DIR", str(instances))
    monkeypatch.setenv("MCDATA_OUTPUT_DIR", str(runs))
    monkeypatch.setattr(
        pipeline,
        "_profile_with_scene",
        lambda _configs, _name: {
            "game_version": "26.2",
            "loader": "fabric",
            "quality": "reference",
            "asset_set": "vanilla",
            "width": 320,
            "height": 180,
            "username": "mcdata_bot",
            "jvm_args": "-Xmx1G",
            "server_port": 25570,
            "world_seed": 1,
            "world_profile": "reference",
            "world_state": {},
        },
    )
    options = pipeline.RunOptions(
        dry_run=True,
        capture=False,
        strategy=None,
        duration=1,
        with_server=False,
        replay_actions=True,
        trajectory_path=source,
        game_version="26.2",
        server_port=None,
        lane="reference",
        probe_interval=5.0,
        debug_no_reapply=False,
        debug_no_replay_gate=False,
    )

    plan = pipeline._plan_run(tmp_path, TARGET_PROFILE, options=options)

    expected = _trajectory()
    expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    trajectory = plan.trajectory_info
    assert trajectory is not None
    assert trajectory["sha256"] == expected_sha
    assert trajectory["source_sha256"] == expected_sha
    assert trajectory["native_trace"] == expected["native_trace"]
    assert trajectory["camera_calibration"] == expected["camera_calibration"]
    assert trajectory["action_source"] == expected["action_source"]
    assert trajectory["external_rollout_binding"] == expected["external_rollout_binding"]
    assert trajectory["external_rollout_binding"]["compatibility_status"] == (
        "target_replay_not_yet_validated"
    )
    assert plan.metadata["trajectory"] == trajectory
    persisted = json.loads((plan.run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert persisted["trajectory"] == trajectory


def test_reference_replay_plan_rejects_profile_mismatch_before_creating_run(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        pipeline,
        "_profile_with_scene",
        lambda _configs, _name: {
            "game_version": "26.2",
            "width": 320,
            "height": 180,
        },
    )
    options = pipeline.RunOptions(
        dry_run=True,
        capture=False,
        strategy=None,
        duration=1,
        with_server=False,
        replay_actions=True,
        trajectory_path=TRAJECTORY,
        game_version="26.2",
        server_port=None,
        lane=None,
        probe_interval=5.0,
        debug_no_reapply=False,
        debug_no_replay_gate=False,
    )

    with pytest.raises(ReferenceReplayError, match="selected profile"):
        pipeline._plan_run(tmp_path, "wrong_profile", options=options)
    assert not (tmp_path / "runs").exists()
