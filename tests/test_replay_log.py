import json
import subprocess
import threading
from pathlib import Path

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
    assert len(records) == 1
    assert records[0]["scheduled_t"] == 0.0
    assert records[0]["actual_t"] >= 0.0
    assert records[0]["event"]["key"] == "w"


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
