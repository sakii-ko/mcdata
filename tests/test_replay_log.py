import json
import subprocess
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
    monkeypatch.setattr(replay, "_send_event_xdotool", lambda _event, *, warned=None: None)

    replay.replay_trajectory(trajectory, start_event=_AlreadyStarted(), run_dir=tmp_path)

    records = [
        json.loads(line)
        for line in (tmp_path / "replay_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert records[0]["scheduled_t"] == 0.0
    assert records[0]["actual_t"] >= 0.0
    assert records[0]["event"]["key"] == "w"


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
