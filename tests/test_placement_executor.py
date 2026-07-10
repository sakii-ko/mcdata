from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.actions import replay
from mcdata.action_placement import (
    receipt_marker,
    validate_episode_reset_evidence,
    validate_placement_input_evidence,
    validate_post_capture_evidence,
)
from mcdata.render import placement
from mcdata.render import pipeline


def _event(
    *,
    action_id: str = "place_gold",
    slot: int = 1,
    block: str = "minecraft:gold_block",
    target: list[int] | None = None,
    support: list[int] | None = None,
) -> dict:
    return {
        "t": 1.0,
        "semantic_action": "deterministic_block_placement",
        "placement": {
            "action_id": action_id,
            "block": block,
            "support_block": "minecraft:glass",
            "hotbar_slot": slot,
            "item_count": 2,
            "target": target or [-12, 65, -13],
            "support": support or [-13, 65, -13],
            "face": "east",
            "aim_dx_px": 610,
            "aim_dy_px": -150,
            "input_settle_sec": 0.1,
            "input_duration_sec": 0.25,
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
        return {"phase": phase, "marker": marker, "line": line, "probe_attempts": 1}

    return probe


def test_l3_executor_prepares_before_capture_dispatches_inputs_and_cleans_after(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("[Server thread/INFO]: server ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = placement.PlacementExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
    )
    monkeypatch.setattr(executor, "_probe", _fake_receipt_probe(log_path))
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)
    events = [_event()]

    reset = executor.prepare(events)
    pre_capture_commands = proc.stdin.getvalue().splitlines()
    assert pre_capture_commands == [
        "clear mcdata_bot",
        "kill @e[type=!minecraft:player]",
        "kill @e[type=minecraft:item]",
        "setblock -12 65 -13 minecraft:air",
        "setblock -13 65 -13 minecraft:glass",
        "item replace entity mcdata_bot hotbar.0 with minecraft:gold_block 2",
    ]
    assert not any(command.startswith("tp ") for command in pre_capture_commands)

    sent: list[dict] = []
    input_evidence = executor.dispatch(events[0], lambda item: sent.append(item) or True)
    assert sent == [
        {"key": "1", "action": "tap"},
        {"mouse_button": 3, "action": "click"},
    ]

    final = executor.finalize()
    all_commands = proc.stdin.getvalue().splitlines()
    assert all_commands[-3:] == [
        "clear mcdata_bot minecraft:gold_block",
        "setblock -12 65 -13 minecraft:air",
        "setblock -13 65 -13 minecraft:air",
    ]
    assert not any(command.startswith("tp ") for command in all_commands)

    replay_path = tmp_path / "replay_log.jsonl"
    validate_episode_reset_evidence(reset, events, replay_log_path=replay_path)
    validate_placement_input_evidence(input_evidence, events[0])
    validate_post_capture_evidence(final, events, replay_log_path=replay_path)


def test_l3_executor_rejects_failed_use_input(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = placement.PlacementExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
    )
    monkeypatch.setattr(placement.time, "sleep", lambda _seconds: None)
    calls = 0

    def send(_item: dict) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    with pytest.raises(RuntimeError, match="use-button dispatch failed"):
        executor.dispatch(_event(), send)


def test_rejected_l3_run_still_resets_inventory_target_and_support(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = placement.PlacementExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
    )
    monkeypatch.setattr(executor, "_probe", _fake_receipt_probe(log_path))
    executor.prepare([_event()])

    evidence = executor.cleanup_after_failure()

    assert evidence["cleanup_command_count"] == 3
    assert evidence["receipts"][0]["phase"] == "failure_cleanup_complete"
    assert proc.stdin.getvalue().splitlines()[-3:] == [
        "clear mcdata_bot minecraft:gold_block",
        "setblock -12 65 -13 minecraft:air",
        "setblock -13 65 -13 minecraft:air",
    ]


def test_l3_probe_times_out_without_conditional_server_receipt(tmp_path: Path) -> None:
    log_path = tmp_path / "server.log"
    log_path.write_text("ready\n", encoding="utf-8")
    proc = SimpleNamespace(stdin=StringIO(), poll=lambda: None)
    executor = placement.PlacementExecutor(
        proc,
        server_log_path=log_path,
        username="mcdata_bot",
        poll_sec=0,
    )

    with pytest.raises(TimeoutError, match="see .*server.log"):
        executor._probe(
            "execute if block 0 0 0 minecraft:gold_block run say {marker}",
            "place_gold",
            "block_placed",
            timeout=0,
        )


def test_l3_receipt_parser_rejects_error_echo_and_prior_success(tmp_path: Path) -> None:
    marker = receipt_marker("place_gold", "block_placed")
    prior = f"[Server thread/INFO]: [Server] {marker}\n"
    error = f"Incorrect argument for command: execute run say {marker}\n"
    log_path = tmp_path / "server.log"
    log_path.write_text(prior + error, encoding="utf-8")

    assert placement._find_marker_line(log_path, marker, after_byte=len(prior)) is None
    assert placement._find_marker_line(log_path, marker, after_byte=0) == prior.strip()


def test_pipeline_rejects_l3_without_capture_and_managed_server(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"events":[{"semantic_action":"deterministic_block_placement"}]}\n')
    plan = SimpleNamespace(
        replay_actions=True,
        run_trajectory_path=trajectory,
        dry_run=False,
        trajectory_info={"type": "astar_walk"},
        capture=False,
        with_server=True,
    )

    with pytest.raises(RuntimeError, match="requires capture with a managed server"):
        pipeline._replay_phase(
            plan,
            pipeline.RunState(server_proc=SimpleNamespace()),
            SimpleNamespace(log=lambda *_args, **_kwargs: None),
        )


def test_pipeline_prepares_l3_after_join_reset_and_before_capture_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    order: list[str] = []
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"events":[]}\n', encoding="utf-8")

    class Executor:
        def prepare(self, events):
            assert events == []
            order.append("placement_prepare")
            return {
                "action_ids": ["place_gold"],
                "reset_command_count": 6,
                "receipts": [{}, {}, {}, {}, {}],
            }

    plan = SimpleNamespace(
        run_trajectory_path=trajectory,
        debug_no_reapply=False,
        profile={},
        capture_settings=object(),
    )
    state = pipeline.RunState(
        server_proc=SimpleNamespace(),
        game_proc=SimpleNamespace(),
        placement_executor=Executor(),
    )
    monkeypatch.setattr(
        pipeline,
        "apply_join_state",
        lambda _proc, _profile: order.append("join_reset"),
    )
    monkeypatch.setattr(
        pipeline,
        "_wait_or_raise_if_exited",
        lambda _proc, _seconds: order.append("join_settle"),
    )
    monkeypatch.setattr(
        pipeline,
        "_prepare_capture_view",
        lambda _settings: order.append("capture_view"),
    )

    pipeline._prepare_joined_capture(
        plan,
        state,
        SimpleNamespace(log=lambda *_args, **_kwargs: None),
    )

    assert order == ["join_reset", "join_settle", "placement_prepare", "capture_view"]


def test_l3_replay_thread_reads_reset_evidence_only_after_capture_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"events":[]}\n', encoding="utf-8")
    received: list[dict] = []

    def fake_replay(_path, **kwargs):
        received.append(kwargs)

    monkeypatch.setattr(replay, "replay_trajectory", fake_replay)
    plan = SimpleNamespace(run_trajectory_path=trajectory, run_dir=tmp_path)
    state = pipeline.RunState(placement_executor=SimpleNamespace())
    thread = pipeline._start_replay_thread(plan, state)
    assert received == []

    reset = {"kind": "l3_episode_reset"}
    state.placement_reset_evidence = reset
    state.ready_event.set()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert received[0]["start_event"] is None
    assert received[0]["episode_reset_evidence"] is reset


def test_capture_runs_post_verification_before_terminating_client(
    tmp_path: Path,
    monkeypatch,
) -> None:
    order: list[str] = []

    class Game:
        args = ["game"]
        returncode = None

        def poll(self):
            return None

    class Capture:
        args = ["ffmpeg"]
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(
        pipeline,
        "_terminate_process_tree",
        lambda _proc, *, timeout: order.append(f"terminate:{timeout}"),
    )

    pipeline._wait_for_capture(
        Game(),
        Capture(),
        tmp_path / "exitcode",
        post_capture=lambda: order.append("verify_and_cleanup"),
    )

    assert order == ["verify_and_cleanup", "terminate:20"]


def test_capture_fails_if_client_exits_before_l3_finalization(tmp_path: Path) -> None:
    class Game:
        args = ["game"]
        returncode = 0

        def poll(self):
            return 0

    class Capture:
        args = ["ffmpeg"]
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 255

        def wait(self, timeout):
            assert timeout == 10
            return self.returncode

    with pytest.raises(RuntimeError, match="exited before L3 post-capture"):
        pipeline._wait_for_capture(
            Game(),
            Capture(),
            tmp_path / "exitcode",
            post_capture=lambda: None,
        )
