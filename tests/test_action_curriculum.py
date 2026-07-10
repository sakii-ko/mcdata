import hashlib
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
from mcdata.action_placement import (
    EPISODE_RESET_BASE_PHASES,
    expected_input_events,
    placement_specs,
    receipt_marker,
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


def _placement_event(
    *, t: float = 2.0, action_id: str = "place_gold", route_index: int = 0
) -> dict:
    return {
        "t": t,
        "duration": 0.25,
        "semantic_action": "deterministic_block_placement",
        "route_index": route_index,
        "placement": {
            "action_id": action_id,
            "block": "minecraft:gold_block",
            "support_block": "minecraft:glass",
            "hotbar_slot": 1,
            "item_count": 2,
            "target": [-12, 65, -13],
            "support": [-13, 65, -13],
            "face": "east",
            "aim_dx_px": 610,
            "aim_dy_px": -150,
            "input_settle_sec": 0.1,
            "input_duration_sec": 0.25,
            "receipt_timeout_sec": 3.0,
        },
    }


def _placement_triplet(
    *, t: float = 2.0, action_id: str = "place_gold", route_index: int = 0
) -> list[dict]:
    placement = _placement_event(t=t, action_id=action_id, route_index=route_index)
    spec = placement["placement"]
    return [
        {
            "t": round(t - 0.55, 3),
            "mouse_dx": spec["aim_dx_px"],
            "mouse_dy": spec["aim_dy_px"],
            "duration": 0.35,
            "placement_aim": True,
            "route_index": route_index,
        },
        placement,
        {
            "t": round(t + spec["input_duration_sec"], 3),
            "mouse_dx": -spec["aim_dx_px"],
            "mouse_dy": -spec["aim_dy_px"],
            "duration": 0.35,
            "placement_aim_restore": True,
            "route_index": route_index,
        },
    ]


def _first_placement(trajectory: dict) -> dict:
    return next(
        event
        for event in trajectory["events"]
        if event.get("semantic_action") == "deterministic_block_placement"
    )


def _l3_trajectory() -> dict:
    return {
        "type": "scripted",
        "duration_sec": 3,
        "action_curriculum": {
            "taxonomy_version": 1,
            "planned_level": 3,
            "capabilities": [
                "navigation",
                "deliberate_jump",
                "deterministic_block_placement",
            ],
        },
        "events": [*_l2_trajectory()["events"], *_placement_triplet()],
    }


def _receipt(action_id: str, phase: str) -> tuple[dict, str]:
    marker = receipt_marker(action_id, phase)
    line = f"[Server thread/INFO]: [Server] {marker}"
    return {"phase": phase, "marker": marker, "line": line}, line


def _prefix_binding(text: str) -> dict:
    payload = text.encode()
    return {
        "path": "server.log",
        "prefix_size_bytes": len(payload),
        "prefix_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_verified_l3_replay(tmp_path: Path, trajectory: dict) -> Path:
    placement_events = [
        event
        for event in trajectory["events"]
        if event.get("semantic_action") == "deterministic_block_placement"
    ]
    specs = placement_specs(placement_events)
    reset_phases = [
        *EPISODE_RESET_BASE_PHASES,
        *(f"arena_{spec['action_id']}" for spec in specs),
        *(f"inventory_{spec['action_id']}" for spec in specs),
    ]
    reset_receipts_and_lines = [_receipt("episode_reset", phase) for phase in reset_phases]
    reset_receipts = [item[0] for item in reset_receipts_and_lines]
    reset_text = "".join(item[1] + "\n" for item in reset_receipts_and_lines)
    final_placements = []
    final_lines = []
    for spec in specs:
        receipts_and_lines = [
            _receipt(str(spec["action_id"]), phase)
            for phase in ("block_placed", "cleanup_complete")
        ]
        final_lines.extend(item[1] for item in receipts_and_lines)
        final_placements.append(
            {
                "action_id": spec["action_id"],
                "block": spec["block"],
                "target": spec["target"],
                "support": spec["support"],
                "face": spec["face"],
                "receipts": [item[0] for item in receipts_and_lines],
            }
        )
    server_text = reset_text + "".join(line + "\n" for line in final_lines)
    (tmp_path / "server.log").write_text(server_text, encoding="utf-8")
    records = [
        {
            "event": "start",
            "mono": 1.0,
            "episode_reset_evidence": {
                "kind": "l3_episode_reset",
                "action_ids": [spec["action_id"] for spec in specs],
                "reset_command_count": 3 + 3 * len(specs),
                "probe_command_count": len(reset_receipts),
                "receipts": reset_receipts,
                "server_log": _prefix_binding(reset_text),
            },
        }
    ]
    for event in trajectory["events"]:
        status = (
            "input_dispatched_pending_probe"
            if event.get("semantic_action") == "deterministic_block_placement"
            else (
                "executed"
                if "key" in event or "mouse_dx" in event or "mouse_dy" in event
                else "non_input"
            )
        )
        record = {
            "scheduled_t": event["t"],
            "actual_t": event["t"],
            "event": event,
            "execution_status": status,
        }
        if status == "input_dispatched_pending_probe":
            spec = event["placement"]
            record["semantic_evidence"] = {
                "kind": "deterministic_block_placement_input",
                "action_id": spec["action_id"],
                "block": spec["block"],
                "hotbar_slot": spec["hotbar_slot"],
                "target": spec["target"],
                "support": spec["support"],
                "face": spec["face"],
                "input_events": expected_input_events(spec),
            }
        records.append(record)
    records.append(
        {
            "event": {"replay_control": "l3_post_capture_verification"},
            "semantic_evidence": {
                "kind": "l3_post_capture_verification",
                "action_ids": [spec["action_id"] for spec in specs],
                "probe_command_count": 2 * len(specs),
                "cleanup_command_count": 3 * len(specs),
                "placements": final_placements,
                "server_log": _prefix_binding(server_text),
            },
        }
    )
    replay_path = tmp_path / "replay_log.jsonl"
    replay_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    return replay_path


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
        "executed" if "key" in event or "mouse_dx" in event or "mouse_dy" in event else "non_input"
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

    with pytest.raises(ActionCurriculumError, match="no observed required action"):
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


def test_l3_contract_event_without_executor_cannot_claim_execution(tmp_path: Path) -> None:
    trajectory = _l3_trajectory()
    events = trajectory["events"]
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    statuses = [
        "unsupported_contract_only"
        if event.get("semantic_action") == "deterministic_block_placement"
        else "executed"
        for event in events
    ]
    _write_replay(replay_path, events, statuses)

    with pytest.raises(ActionCurriculumError, match="no observed required action"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )

    statuses[3] = "executed"
    _write_replay(replay_path, events, statuses)
    with pytest.raises(ActionCurriculumError, match="execution status"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_l4_contract_event_stays_unsupported(tmp_path: Path) -> None:
    trajectory = {
        "type": "scripted",
        "duration_sec": 2,
        "action_curriculum": {
            "taxonomy_version": 1,
            "planned_level": 4,
            "capabilities": [
                "navigation",
                "deliberate_jump",
                "deterministic_block_placement",
                "controlled_combat",
            ],
        },
        "events": [
            {"t": 0.0, "key": "w", "action": "tap"},
            {
                "t": 1.0,
                "semantic_action": "controlled_combat",
            },
        ],
    }
    trajectory_path = tmp_path / "trajectory.json"
    replay_path = tmp_path / "replay_log.jsonl"
    _write_json(trajectory_path, trajectory)
    _write_replay(replay_path, trajectory["events"], ["executed", "executed"])

    with pytest.raises(ActionCurriculumError, match="execution status"):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_verified_l3_requires_input_reset_world_probe_and_cleanup(tmp_path: Path) -> None:
    trajectory = _l3_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    _write_json(trajectory_path, trajectory)
    replay_path = _write_verified_l3_replay(tmp_path, trajectory)

    result = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )

    assert result["bucket"] == "l1_l2_l3"
    assert result["observed_level"] == 3
    assert result["observed_semantic_action_counts"] == {
        "navigation_move": 1,
        "navigation_camera": 2,
        "deliberate_jump": 1,
        "deterministic_block_placement": 1,
        "controlled_combat": 0,
    }


def test_configured_l3_showcase_preserves_route_and_all_lower_actions(tmp_path: Path) -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    l2 = build_trajectory(
        "curriculum_l2_jump_showcase_60s",
        strategies["curriculum_l2_jump_showcase_60s"],
    )
    l3 = build_trajectory(
        "curriculum_l3_place_showcase_60s",
        strategies["curriculum_l3_place_showcase_60s"],
    )
    trajectory_path = tmp_path / "trajectory.json"
    _write_json(trajectory_path, l3)
    replay_path = _write_verified_l3_replay(tmp_path, l3)

    result = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )

    assert l3["route"] == l2["route"]
    assert l3["duration_sec"] == l2["duration_sec"] == 59.034
    assert result["observed_semantic_action_counts"]["deliberate_jump"] == 4
    assert result["observed_semantic_action_counts"]["deterministic_block_placement"] == 2


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("missing_reset", "episode reset evidence"),
        ("missing_post_capture", "exactly one post-capture"),
        ("wrong_input", "input dispatch evidence"),
        ("server_log", "prefix hash does not match"),
        ("post_not_final", "final replay record"),
        ("nonextending_prefix", "must extend the episode-reset prefix"),
    ],
)
def test_l3_evidence_tampering_fails_closed(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    trajectory = _l3_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    _write_json(trajectory_path, trajectory)
    replay_path = _write_verified_l3_replay(tmp_path, trajectory)
    records = [json.loads(line) for line in replay_path.read_text().splitlines()]
    if tamper == "missing_reset":
        records[0].pop("episode_reset_evidence")
    elif tamper == "missing_post_capture":
        records.pop()
    elif tamper == "wrong_input":
        placement_record = next(
            record
            for record in records
            if isinstance(record.get("event"), dict)
            and record["event"].get("semantic_action")
            == "deterministic_block_placement"
        )
        placement_record["semantic_evidence"]["input_events"][1]["mouse_button"] = 1
    elif tamper == "server_log":
        log = tmp_path / "server.log"
        log.write_text(
            log.read_text().replace("inventory_empty", "inventory_emptx"), encoding="utf-8"
        )
    elif tamper == "post_not_final":
        records.append({"event": {"replay_control": "late_control"}})
    elif tamper == "nonextending_prefix":
        records[0]["episode_reset_evidence"]["server_log"] = _prefix_binding(
            (tmp_path / "server.log").read_text(encoding="utf-8")
        )
    replay_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    with pytest.raises(ActionCurriculumError, match=message):
        summarize_action_run(
            trajectory_path,
            replay_path,
            execution_mode="open_loop_event_replay",
        )


def test_l3_summary_requires_observed_l2_capability(tmp_path: Path) -> None:
    trajectory = _l3_trajectory()
    trajectory_path = tmp_path / "trajectory.json"
    _write_json(trajectory_path, trajectory)
    replay_path = _write_verified_l3_replay(tmp_path, trajectory)
    summary = summarize_action_run(
        trajectory_path,
        replay_path,
        execution_mode="open_loop_event_replay",
    )
    summary["observed_semantic_action_counts"]["deliberate_jump"] = 0

    with pytest.raises(ActionCurriculumError, match="required action 'deliberate_jump'"):
        validate_action_summary(summary)


def test_l3_placement_target_must_match_support_face() -> None:
    trajectory = _l3_trajectory()
    _first_placement(trajectory)["placement"]["target"] = [-11, 65, -13]

    with pytest.raises(ActionCurriculumError, match="not adjacent to the declared support face"):
        planned_action_contract(trajectory)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("action_id", "action_ids must be unique"),
        ("hotbar_slot", "hotbar slots must be unique"),
        ("block", "placement blocks must be unique"),
    ],
)
def test_l3_placement_identity_and_hotbar_slots_are_unique(
    field: str,
    message: str,
) -> None:
    trajectory = _l3_trajectory()
    second_triplet = _placement_triplet(
        t=3.0,
        action_id="place_emerald",
        route_index=1,
    )
    second = second_triplet[1]
    second["placement"].update(
        {
            "block": "minecraft:emerald_block",
            "hotbar_slot": 2,
            "target": [14, 65, 13],
            "support": [14, 65, 14],
            "face": "north",
        }
    )
    second["placement"][field] = _first_placement(trajectory)["placement"][field]
    trajectory["events"].extend(second_triplet)

    with pytest.raises(ActionCurriculumError, match=message):
        planned_action_contract(trajectory)


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("missing_aim", "placement_aim event has an unstable field set"),
        ("wrong_aim", "placement_aim event does not match"),
        ("wrong_restore", "placement_aim_restore event does not match"),
        ("wrong_route_index", "placement_aim event does not match"),
        ("wrong_input_duration", "duration does not match input_duration_sec"),
        ("bad_timestamp", "timestamp must be finite"),
        ("orphan_aim", "unbound or missing"),
    ],
)
def test_l3_placement_camera_sequence_is_strictly_bound(
    tamper: str,
    message: str,
) -> None:
    trajectory = _l3_trajectory()
    events = trajectory["events"]
    placement_index = events.index(_first_placement(trajectory))
    if tamper == "missing_aim":
        events.pop(placement_index - 1)
    elif tamper == "wrong_aim":
        events[placement_index - 1]["mouse_dx"] += 1
    elif tamper == "wrong_restore":
        events[placement_index + 1]["mouse_dy"] += 1
    elif tamper == "wrong_route_index":
        events[placement_index - 1]["route_index"] += 1
    elif tamper == "wrong_input_duration":
        events[placement_index]["duration"] = 0.2
    elif tamper == "bad_timestamp":
        events[placement_index]["t"] = "now"
    elif tamper == "orphan_aim":
        events.append(
            {
                "t": 2.8,
                "mouse_dx": 1,
                "mouse_dy": 1,
                "duration": 0.35,
                "placement_aim": True,
                "route_index": 0,
            }
        )

    with pytest.raises(ActionCurriculumError, match=message):
        planned_action_contract(trajectory)


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
