from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.action_combat import (
    validate_combat_input_evidence,
    validate_combat_post_capture_evidence,
    validate_combat_reset_evidence,
)
from mcdata.action_placement import receipt_marker
from mcdata.render import combat
from mcdata.render import pipeline


def _placement_event() -> dict:
    return {
        "semantic_action": "deterministic_block_placement",
        "placement": {
            "action_id": "place_gold",
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


def _combat_event() -> dict:
    return {
        "t": 10.0,
        "duration": 1.0,
        "semantic_action": "controlled_combat",
        "route_index": 77,
        "combat": {
            "action_id": "spar_golem",
            "target_entity": "minecraft:iron_golem",
            "target_tag": "mcdata_l4_target",
            "target_uuid": "4d434441-5441-4c34-8000-000000000004",
            "spawn": [16.5, 64.0, -6.5],
            "rotation": [0.0, 0.0],
            "initial_health": 20.0,
            "knockback_resistance": 1.0,
            "weapon": "minecraft:wooden_sword",
            "hotbar_slot": 3,
            "item_count": 1,
            "aim_dx_px": 0,
            "aim_dy_px": -20,
            "input_settle_sec": 0.1,
            "attack_probe_delay_sec": 0.25,
            "input_duration_sec": 1.0,
            "receipt_timeout_sec": 3.0,
        },
    }


def _fake_receipt_probe(log_path: Path):
    def probe(_command: str, action_id: str, phase: str, *, timeout: float) -> dict:
        assert timeout > 0
        marker = receipt_marker(action_id, phase)
        line = f"[Server thread/INFO]: [Server] {marker}"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return {
            "phase": phase,
            "marker": marker,
            "line": line,
            "probe_attempts": 1,
        }

    return probe


def _fake_score_query(log_path: Path, values: dict[str, int]):
    def query(holder: str, *, timeout: float) -> dict:
        assert timeout > 0
        value = values[holder]
        line = f"[Server thread/INFO]: {holder} has {value} [mcdata_l4]"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return {
            "holder": holder,
            "objective": "mcdata_l4",
            "value": value,
            "line": line,
            "probe_attempts": 1,
        }

    return query


def _fake_objective_mutation(log_path: Path):
    def mutate(action: str, *, timeout: float) -> dict:
        assert timeout > 0
        prefix = "Created new" if action == "add" else "Removed"
        line = f"[Server thread/INFO]: {prefix} objective [mcdata_l4]"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return {"objective": "mcdata_l4", "line": line}

    return mutate


def test_l4_executor_uses_real_attack_and_proves_attacker_health_and_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("[Server thread/INFO]: server ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = combat.CombatExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
    )
    fake_probe = _fake_receipt_probe(log_path)
    monkeypatch.setattr(executor, "_probe", fake_probe)
    monkeypatch.setattr(executor._placement, "_probe", fake_probe)
    monkeypatch.setattr(
        executor,
        "_query_score",
        _fake_score_query(
            log_path,
            {
                "#target_count": 1,
                "#health_before": 2000,
                "#knockback": 1000,
                "#mob_spawning": 0,
                "#attacker_ok": 1,
                "#target_count_after": 1,
                "#health_after": 1600,
                "#spawn_mobs_final": 0,
            },
        ),
    )
    monkeypatch.setattr(
        executor,
        "_objective_mutation",
        _fake_objective_mutation(log_path),
    )
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    events = [_placement_event(), _combat_event()]

    reset = executor.prepare(events)
    prepare_commands = proc.stdin.getvalue().splitlines()
    assert any(
        command.startswith("summon minecraft:iron_golem 16.5 64 -6.5 ")
        for command in prepare_commands
    )
    assert any("UUID:[I;" in command and "NoAI:1b" in command for command in prepare_commands)
    assert any("minecraft:knockback_resistance base set 1" in command for command in prepare_commands)
    assert any("hotbar.2 with minecraft:wooden_sword 1" in command for command in prepare_commands)
    assert not any(command.startswith(("tp ", "damage ", "difficulty ")) for command in prepare_commands)
    assert not any("gamerule spawn_mobs false" in command for command in prepare_commands)
    assert "x=16.5,y=64,z=-6.5,distance=..0.25" in combat._snapshot_selector(
        events[1]["combat"]
    )

    sent: list[dict] = []
    input_evidence = executor.dispatch(
        events[1],
        lambda primitive: sent.append(primitive) or True,
    )
    capture_commands = proc.stdin.getvalue().splitlines()[len(prepare_commands) :]
    assert sent == [
        {"key": "3", "action": "tap"},
        {"mouse_button": 1, "action": "click"},
    ]
    assert any(" on attacker " in command for command in capture_commands)
    assert not any(command.startswith(("tp ", "damage ", "kill ")) for command in capture_commands)

    final = executor.finalize()
    all_commands = proc.stdin.getvalue().splitlines()
    assert any(command.startswith("kill @e[nbt={UUID:") for command in all_commands)
    assert "kill @e[type=minecraft:item]" in all_commands
    assert not any("gamerule spawn_mobs true" in command for command in all_commands)

    replay_path = tmp_path / "replay_log.jsonl"
    validate_combat_reset_evidence(
        reset["combat"],
        events,
        replay_log_path=replay_path,
    )
    validate_combat_input_evidence(
        input_evidence,
        events[1],
        replay_log_path=replay_path,
    )
    validate_combat_post_capture_evidence(
        final["combat"],
        events,
        replay_log_path=replay_path,
    )
    assert final["combat"]["remaining_health_score"] == 1600


def test_l4_executor_never_claims_failed_left_click(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("ready\n", encoding="utf-8")
    executor = combat.CombatExecutor(
        SimpleNamespace(stdin=StringIO(), poll=lambda: None),
        server_log_path=log_path,
        username="mcdata_bot",
    )
    monkeypatch.setattr(combat.time, "sleep", lambda _seconds: None)
    calls = 0

    def send(_primitive: dict) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    with pytest.raises(RuntimeError, match="attack input dispatch failed"):
        executor.dispatch(_combat_event(), send)


def test_l4_failure_cleanup_attempts_items_and_objective_after_target_probe_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = combat.CombatExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
    )
    executor._spec = _combat_event()["combat"]
    calls = 0
    probe_commands: list[str] = []

    def probe(command, action_id, phase, *, timeout):
        nonlocal calls
        calls += 1
        probe_commands.append(command)
        if calls == 1:
            raise TimeoutError("target stayed alive")
        return _fake_receipt_probe(log_path)(command, action_id, phase, timeout=timeout)

    objectives: list[str] = []
    monkeypatch.setattr(executor, "_probe", probe)
    monkeypatch.setattr(
        executor,
        "_query_score",
        _fake_score_query(log_path, {"#spawn_mobs_final": 0}),
    )
    monkeypatch.setattr(
        executor,
        "_objective_mutation",
        lambda action, *, timeout: objectives.append(action)
        or {"objective": "mcdata_l4", "line": "Removed objective [mcdata_l4]"},
    )

    with pytest.raises(RuntimeError, match="target stayed alive"):
        executor._cleanup_combat(phase="failure_cleanup_complete")

    assert "kill @e[type=minecraft:item]" in proc.stdin.getvalue().splitlines()
    assert objectives == ["remove"]
    assert "inventory.* minecraft:wooden_sword" in probe_commands[-1]
    assert "hotbar.* minecraft:wooden_sword" in probe_commands[-1]
    assert "armor.* minecraft:wooden_sword" in probe_commands[-1]
    assert "weapon.offhand minecraft:wooden_sword" in probe_commands[-1]


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("add", "Created new objective [mcdata_l4]"),
        ("remove", "Removed objective [mcdata_l4]"),
    ],
)
def test_objective_mutation_is_sent_once_and_bound_to_new_success_line(
    tmp_path: Path,
    monkeypatch,
    action: str,
    expected: str,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text(f"old {expected}\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = combat.CombatExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
        poll_sec=0,
    )
    calls: list[list[str]] = []

    def fake_write(_proc, commands):
        calls.append(commands)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[Server thread/INFO]: {expected}\n")

    monkeypatch.setattr(combat, "write_commands", fake_write)

    evidence = executor._objective_mutation(action, timeout=1)

    assert len(calls) == 1
    assert evidence["line"].endswith(expected)


def test_score_parser_ignores_prior_line_and_wrong_holder(tmp_path: Path) -> None:
    prior = "[Server thread/INFO]: #health_after has 1600 [mcdata_l4]\n"
    wrong = "[Server thread/INFO]: #other has 1600 [mcdata_l4]\n"
    log_path = tmp_path / "server.log"
    log_path.write_text(prior + wrong, encoding="utf-8")

    assert combat._find_score_line(
        log_path,
        holder="#health_after",
        objective="mcdata_l4",
        after_byte=len(prior.encode()),
    ) is None
    assert combat._find_score_line(
        log_path,
        holder="#health_after",
        objective="mcdata_l4",
        after_byte=0,
    ) == (1600, prior.strip())


def test_pipeline_rejects_l4_without_capture_and_managed_server(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        '{"events":[{"semantic_action":"controlled_combat"}]}\n',
        encoding="utf-8",
    )
    plan = SimpleNamespace(
        replay_actions=True,
        run_trajectory_path=trajectory,
        dry_run=False,
        trajectory_info={"type": "astar_walk"},
        capture=False,
        with_server=True,
    )

    with pytest.raises(RuntimeError, match="L4 combat replay requires capture"):
        pipeline._replay_phase(
            plan,
            pipeline.RunState(server_proc=SimpleNamespace()),
            SimpleNamespace(log=lambda *_args, **_kwargs: None),
        )


def test_pipeline_selects_cumulative_executor_and_writes_l4_final_control(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        '{"events":[{"semantic_action":"controlled_combat"}]}\n',
        encoding="utf-8",
    )
    sentinel = SimpleNamespace(
        post_capture_control="l4_post_capture_verification",
        finalize=lambda: {
            "action_ids": ["place_gold", "spar_golem"],
            "cleanup_command_count": 7,
            "placement": {"receipts": [{}, {}]},
            "combat": {"receipts": [{}, {}]},
        },
    )
    monkeypatch.setattr(pipeline, "CombatExecutor", lambda *_args, **_kwargs: sentinel)
    monkeypatch.setattr(pipeline, "_start_replay_thread", lambda *_args: None)
    plan = SimpleNamespace(
        replay_actions=True,
        run_trajectory_path=trajectory,
        dry_run=False,
        trajectory_info={"type": "astar_walk"},
        capture=True,
        with_server=True,
        run_dir=tmp_path,
        profile={"username": "mcdata_bot"},
    )
    state = pipeline.RunState(server_proc=SimpleNamespace())
    runlog = SimpleNamespace(log=lambda *_args, **_kwargs: None)

    pipeline._replay_phase(plan, state, runlog)
    assert state.placement_executor is sentinel
    (tmp_path / "replay_log.jsonl").write_text("", encoding="utf-8")
    pipeline._finalize_placement_actions(plan, state, runlog)

    record = json.loads((tmp_path / "replay_log.jsonl").read_text().strip())
    assert record["event"] == {"replay_control": "l4_post_capture_verification"}
    assert state.placement_finalized is True


def test_pipeline_counts_nested_l3_and_l4_receipts() -> None:
    evidence = {
        "placement": {"placements": [{"receipts": [{}, {}]}, {"receipts": [{}, {}]}]},
        "combat": {"receipts": [{}, {}, {}]},
    }

    assert pipeline._semantic_receipt_count(evidence) == 7
