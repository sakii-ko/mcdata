import json
import subprocess
import threading
from pathlib import Path

import pytest

from mcdata.actions import replay


class _AlreadyStarted:
    def wait(self, timeout: float | None = None) -> bool:
        return True


def test_replay_writes_scheduled_and_actual_times(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        json.dumps({"events": [{"t": 0.0, "key": "w", "action": "tap"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _window_name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda _backend, *, warned=None: [])
    monkeypatch.setattr(replay, "_send_event_xdotool", lambda _event, **_kwargs: None)

    replay.replay_trajectory(trajectory, start_event=_AlreadyStarted(), run_dir=tmp_path)

    records = [
        json.loads(line)
        for line in (tmp_path / "replay_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 2
    assert records[0]["event"] == "start"
    assert records[0]["mono"] >= 0.0
    assert records[1]["scheduled_t"] == 0.0
    assert records[1]["actual_t"] >= 0.0
    assert records[1]["event"]["key"] == "w"
    assert records[1]["execution_status"] == "executed"


def test_replay_marks_unimplemented_semantic_actions_contract_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "t": 0.0,
                        "semantic_action": "deterministic_block_placement",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _window_name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda _backend, *, warned=None: [])
    sent: list[dict] = []
    monkeypatch.setattr(
        replay, "_send_event_xdotool", lambda event, **_kwargs: sent.append(dict(event))
    )

    replay.replay_trajectory(trajectory, start_event=_AlreadyStarted(), run_dir=tmp_path)

    records = [
        json.loads(line)
        for line in (tmp_path / "replay_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[1]["execution_status"] == "unsupported_contract_only"
    assert sent == []


def test_replay_dispatches_real_slot_and_use_inputs_but_leaves_l3_pending_probe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trajectory = tmp_path / "trajectory.json"
    event = {
        "t": 0.0,
        "semantic_action": "deterministic_block_placement",
        "placement": {"action_id": "place_gold"},
    }
    trajectory.write_text(json.dumps({"events": [event]}), encoding="utf-8")
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _window_name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda _backend, *, warned=None: [])
    sent: list[dict] = []
    monkeypatch.setattr(
        replay,
        "_send_event_xdotool",
        lambda primitive, **_kwargs: sent.append(dict(primitive)) or True,
    )

    class Executor:
        def dispatch(self, _event, send_input):
            inputs = [
                {"key": "1", "action": "tap"},
                {"mouse_button": 3, "action": "click"},
            ]
            assert all(send_input(item) for item in inputs)
            return {"input_events": inputs}

    replay.replay_trajectory(
        trajectory,
        start_event=_AlreadyStarted(),
        run_dir=tmp_path,
        semantic_executor=Executor(),
        episode_reset_evidence={"kind": "test_reset"},
    )

    records = [
        json.loads(line)
        for line in (tmp_path / "replay_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["episode_reset_evidence"] == {"kind": "test_reset"}
    assert records[1]["execution_status"] == "input_dispatched_pending_probe"
    assert records[1]["semantic_evidence"] == {"input_events": sent}
    assert sent == [
        {"key": "1", "action": "tap"},
        {"mouse_button": 3, "action": "click"},
    ]


def test_xdotool_right_click_uses_mouse_button_three(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=0)

    monkeypatch.setattr(replay.subprocess, "run", fake_run)

    assert replay._send_event_xdotool({"mouse_button": 3, "action": "click"}) is True
    assert calls == [["xdotool", "click", "3"]]


@pytest.mark.parametrize("tag", ["placement_aim", "placement_aim_restore"])
def test_failed_placement_camera_input_never_claims_execution(
    tag: str,
    monkeypatch,
) -> None:
    monkeypatch.setattr(replay, "_send_backend_event", lambda *_args, **_kwargs: False)

    with pytest.raises(RuntimeError, match="Placement camera .* input dispatch failed"):
        replay._dispatch_replay_event(
            {"mouse_dx": 600, "mouse_dy": -150, tag: True},
            backend="xdotool",
            semantic_executor=None,
            warned=set(),
            stop_event=None,
            held=set(),
        )


def test_update_held_tracks_key_lifecycle() -> None:
    held: set[str] = set()

    replay._update_held(held, {"key": "w", "action": "down"})
    replay._update_held(held, {"mouse_dx": 10})
    replay._update_held(held, {"key": "space", "action": "tap"})
    replay._update_held(held, {"key": "w", "action": "up"})

    assert held == set()


def test_replay_releases_held_key_when_stopped(tmp_path: Path, monkeypatch) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        json.dumps(
            {
                "events": [
                    {"t": 0.0, "key": "w", "action": "down"},
                    {"t": 10.0, "key": "w", "action": "up"},
                ]
            }
        ),
        encoding="utf-8",
    )
    sent: list[dict] = []
    stop = threading.Event()

    def fake_send(event: dict, *, warned=None, stop_event=None) -> None:
        sent.append(dict(event))
        stop.set()

    released: list[str] = []
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _window_name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda _backend, *, warned=None: [])
    monkeypatch.setattr(replay, "_send_event_xdotool", fake_send)
    monkeypatch.setattr(
        replay,
        "_release_keys",
        lambda keys, _backend, *, warned=None: released.extend(keys),
    )

    replay.replay_trajectory(trajectory, stop_event=stop, run_dir=tmp_path)

    assert sent == [{"t": 0.0, "key": "w", "action": "down"}]
    assert released == ["w"]
    records = [
        json.loads(line)
        for line in (tmp_path / "replay_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["event"] == {"keys": ["w"], "replay_control": "released_keys"}


def test_xdotool_failure_warns_once(monkeypatch, capsys) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode=1, stderr="no window\n")

    monkeypatch.setattr(replay.subprocess, "run", fake_run)
    warned: set[tuple[str, ...]] = set()

    replay._send_event_xdotool({"key": "w", "action": "down"}, warned=warned)
    replay._send_event_xdotool({"key": "w", "action": "down"}, warned=warned)

    captured = capsys.readouterr()
    assert len(calls) == 2
    assert captured.out.count("Warning: xdotool command failed") == 1


def test_input_controller_tracks_keys_and_releases_on_close(monkeypatch) -> None:
    sent: list[dict] = []
    released: list[str] = []
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        replay,
        "_send_event_xdotool",
        lambda event, **_kwargs: sent.append(dict(event)),
    )
    monkeypatch.setattr(
        replay,
        "_release_keys",
        lambda keys, _backend, *, warned=None: released.extend(keys),
    )

    with replay.InputController() as controller:
        controller.key_down("w")
        controller.key_down("w")
        controller.move_mouse(20, -2)
        controller.tap("space")

    assert sent == [
        {"key": "w", "action": "down"},
        {"mouse_dx": 20, "mouse_dy": -2},
        {"key": "space", "action": "tap"},
    ]
    assert released == ["w"]


def test_input_controller_key_up_is_sent_before_close(monkeypatch) -> None:
    sent: list[dict] = []
    released: list[str] = []
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        replay,
        "_send_event_xdotool",
        lambda event, **_kwargs: sent.append(dict(event)),
    )
    monkeypatch.setattr(
        replay,
        "_release_keys",
        lambda keys, _backend, *, warned=None: released.extend(keys),
    )

    controller = replay.InputController()
    controller.key_down("w")
    controller.key_up("w")
    controller.close()

    assert sent == [
        {"key": "w", "action": "down"},
        {"key": "w", "action": "up"},
    ]
    assert released == []


def test_input_controller_releases_held_key_after_stop(monkeypatch) -> None:
    sent: list[dict] = []
    stop = threading.Event()
    monkeypatch.setattr(replay, "_backend", lambda: "xdotool")
    monkeypatch.setattr(replay, "_focus_window", lambda _name, *, warned=None: None)
    monkeypatch.setattr(replay, "_release_inherited_keys", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        replay,
        "_send_event_xdotool",
        lambda event, **_kwargs: sent.append(dict(event)),
    )

    controller = replay.InputController(stop_event=stop)
    controller.key_down("s")
    stop.set()
    controller.key_up("s")
    controller.key_down("w")
    controller.close()

    assert sent == [
        {"key": "s", "action": "down"},
        {"key": "s", "action": "up"},
    ]
