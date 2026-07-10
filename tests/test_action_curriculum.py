import json
from pathlib import Path

import pytest

from mcdata.action_curriculum import (
    ActionCurriculumError,
    action_buckets,
    planned_action_contract,
    summarize_action_run,
    validate_action_summary,
)
from mcdata.actions.strategies import build_trajectory
from mcdata.config import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_replay(path: Path, events: list[dict], statuses: list[str] | None = None) -> None:
    records = [{"event": "start", "mono": 1.0}]
    statuses = statuses or ["executed"] * len(events)
    records.extend(
        {
            "scheduled_t": event["t"],
            "actual_t": event["t"],
            "event": event,
            "execution_status": status,
        }
        for event, status in zip(events, statuses)
    )
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def _l2_trajectory() -> dict:
    return {
        "type": "scripted",
        "duration_sec": 2,
        "action_curriculum": {
            "taxonomy_version": 1,
            "planned_level": 2,
            "capabilities": ["navigation", "deliberate_jump"],
        },
        "events": [
            {"t": 0.0, "key": "w", "action": "tap"},
            {
                "t": 1.0,
                "key": "space",
                "action": "tap",
                "semantic_action": "deliberate_jump",
            },
        ],
    }


def test_legacy_open_loop_defaults_to_l1_and_uses_replay_evidence(tmp_path: Path) -> None:
    trajectory = {
        "type": "astar_walk",
        "duration_sec": 1,
        "events": [
            {"t": 0.0, "key": "w", "action": "down"},
            {"t": 0.5, "mouse_dx": 120, "mouse_dy": 0},
            {"t": 1.0, "key": "w", "action": "up"},
        ],
    }
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])

    result = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )

    assert result["planned_level"] == 1
    assert result["planned_capabilities"] == ["navigation"]
    assert result["observed_level"] == 1
    assert result["observed_semantic_action_counts"]["navigation_move"] == 1
    assert result["observed_semantic_action_counts"]["navigation_camera"] == 1
    assert result["bucket"] == "l1"
    assert result["evidence"]["kind"] == "replay_log"


@pytest.mark.parametrize("strategy_type", ["astar_walk", "roam", "feedback_roam"])
def test_existing_navigation_families_default_to_l1(strategy_type: str) -> None:
    result = planned_action_contract({"type": strategy_type, "events": []})

    assert result == {
        "taxonomy_version": 1,
        "planned_level": 1,
        "planned_capabilities": ["navigation"],
        "bucket": "l1",
    }


def test_fixed_camera_only_evidence_is_not_navigation_data(tmp_path: Path) -> None:
    trajectory = {
        "type": "look_scan",
        "duration_sec": 1,
        "events": [{"t": 0.0, "mouse_dx": 120, "mouse_dy": 0}],
    }
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])

    with pytest.raises(ActionCurriculumError, match="no observed L1 movement"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_deliberate_jump_is_l2_only_when_explicit_and_dispatched(tmp_path: Path) -> None:
    trajectory = _l2_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])

    result = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )

    assert result["bucket"] == "l1_l2"
    assert result["observed_level"] == 2
    assert result["observed_semantic_action_counts"]["deliberate_jump"] == 1


def test_configured_l2_jump_showcase_counts_all_dispatched_jumps(tmp_path: Path) -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"][
        "curriculum_l2_jump_showcase_60s"
    ]
    trajectory = build_trajectory("curriculum_l2_jump_showcase_60s", spec)
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    statuses = [
        "executed"
        if "key" in event or "mouse_dx" in event or "mouse_dy" in event
        else "non_input"
        for event in trajectory["events"]
    ]
    _write_replay(replay_path, trajectory["events"], statuses)

    result = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )

    assert result["bucket"] == "l1_l2"
    assert result["observed_level"] == 2
    assert result["observed_semantic_action_counts"]["deliberate_jump"] == 4
    assert result["controller_recovery_counts"]["jump_taps"] == 0


def test_scripted_strategy_preserves_action_curriculum_contract() -> None:
    contract = {
        "taxonomy_version": 1,
        "planned_level": 2,
        "capabilities": ["navigation", "deliberate_jump"],
    }

    trajectory = build_trajectory(
        "jump_probe",
        {
            "type": "scripted",
            "duration_sec": 2,
            "action_curriculum": contract,
            "steps": _l2_trajectory()["events"],
        },
    )

    assert trajectory["action_curriculum"] == contract
    assert planned_action_contract(trajectory)["planned_level"] == 2


def test_capabilities_must_be_exactly_cumulative() -> None:
    trajectory = _l2_trajectory()
    trajectory["action_curriculum"]["capabilities"] = ["deliberate_jump"]

    with pytest.raises(ActionCurriculumError, match="cumulative and ordered"):
        planned_action_contract(trajectory)


def test_summary_field_set_is_strict(tmp_path: Path) -> None:
    trajectory = _l2_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])
    summary = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )
    summary["unversioned_extra"] = True

    with pytest.raises(ActionCurriculumError, match="unstable field set"):
        validate_action_summary(summary)


def test_empty_highest_level_and_undeclared_observation_fail_closed(tmp_path: Path) -> None:
    trajectory = _l2_trajectory()
    trajectory["events"] = trajectory["events"][:1]
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])

    with pytest.raises(ActionCurriculumError, match="no observed highest-level action"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )

    summary = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
        require_evidence=False,
    )
    summary["planned_level"] = 1
    summary["planned_capabilities"] = ["navigation"]
    summary["bucket"] = "l1"
    summary["observed_semantic_action_counts"]["deliberate_jump"] = 1
    summary["observed_level"] = 2
    with pytest.raises(ActionCurriculumError, match="undeclared semantic"):
        validate_action_summary(summary)


@pytest.mark.parametrize(
    "semantic_action",
    ["deterministic_block_placement", "controlled_combat"],
)
def test_l3_l4_contract_events_cannot_claim_execution(
    tmp_path: Path, semantic_action: str
) -> None:
    level = 3 if semantic_action == "deterministic_block_placement" else 4
    capabilities = [
        "navigation",
        "deliberate_jump",
        "deterministic_block_placement",
        "controlled_combat",
    ][:level]
    events = [
        {"t": 0.0, "key": "w", "action": "tap"},
        {"t": 1.0, "semantic_action": semantic_action},
    ]
    trajectory = {
        "type": "scripted",
        "duration_sec": 2,
        "action_curriculum": {
            "taxonomy_version": 1,
            "planned_level": level,
            "capabilities": capabilities,
        },
        "events": events,
    }
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, events, ["executed", "unsupported_contract_only"])

    with pytest.raises(ActionCurriculumError, match="no observed highest-level action"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )

    _write_replay(replay_path, events, ["executed", "executed"])
    with pytest.raises(ActionCurriculumError, match="execution status"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_feedback_recovery_is_not_a_deliberate_jump(tmp_path: Path) -> None:
    trajectory = {
        "type": "feedback_roam",
        "duration_sec": 60,
        "route": [{"x": 0, "z": 0}, {"x": 1, "z": 0}, {"x": 0, "z": 0}],
        "events": [],
    }
    records = [
        {"event": "start", "mono": 1.0},
        {"event": "control", "moving": True, "mouse_dx": 20},
        {"event": "recovery", "attempt": 1, "reason": "position_stuck"},
        {"event": "stop", "reason": "stop_requested"},
    ]
    trajectory_path = tmp_path / "trajectory.json"
    navigation_path = tmp_path / "navigation_log.jsonl"
    _write_json(trajectory_path, trajectory)
    navigation_path.write_text(
        "".join(json.dumps(item) + "\n" for item in records), encoding="utf-8"
    )

    result = summarize_action_run(
        trajectory_path,
        navigation_path,
        execution_mode="online_position_yaw_feedback",
    )

    assert result["observed_level"] == 1
    assert result["observed_semantic_action_counts"]["deliberate_jump"] == 0
    assert result["controller_recovery_counts"] == {
        "attempts": 1,
        "jump_taps": 1,
        "reverse_moves": 1,
    }


def test_missing_or_tampered_replay_evidence_fails_closed(tmp_path: Path) -> None:
    trajectory = _l2_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    _write_json(trajectory_path, trajectory)

    with pytest.raises(ActionCurriculumError, match="evidence is missing"):
        summarize_action_run(
            trajectory_path,
            tmp_path / "missing.jsonl",
            execution_mode="open_loop_event_replay",
        )

    replay_path = tmp_path / "replay_log.jsonl"
    _write_replay(replay_path, list(reversed(trajectory["events"])))
    with pytest.raises(ActionCurriculumError, match="exactly match"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_missing_execution_status_is_allowed_only_for_explicit_legacy_migration(
    tmp_path: Path,
) -> None:
    trajectory = {
        "type": "astar_walk",
        "duration_sec": 1,
        "events": [{"t": 0.0, "key": "w", "action": "tap"}],
    }
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"])
    records = [json.loads(line) for line in replay_path.read_text().splitlines()]
    records[1].pop("execution_status")
    replay_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    with pytest.raises(ActionCurriculumError, match="execution status"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )

    migrated = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
        allow_legacy_execution_status_missing=True,
    )
    assert migrated["observed_semantic_action_counts"]["navigation_move"] == 1


def test_bucket_index_is_disjoint_exact_and_sorted() -> None:
    episodes = [
        {"episode_id": "z", "action_curriculum": {"bucket": "l1"}},
        {"episode_id": "a", "action_curriculum": {"bucket": "l1"}},
        {"episode_id": "jump", "action_curriculum": {"bucket": "l1_l2"}},
    ]

    result = action_buckets(episodes)

    assert result == {
        "taxonomy_version": 1,
        "l1": {"episode_count": 2, "episode_ids": ["a", "z"]},
        "l1_l2": {"episode_count": 1, "episode_ids": ["jump"]},
        "l1_l2_l3": {"episode_count": 0, "episode_ids": []},
        "l1_l2_l3_l4": {"episode_count": 0, "episode_ids": []},
    }
