from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from rich.console import Console

from mcdata.render.scene import write_commands

console = Console()


def start_position_probe(
    proc: subprocess.Popen,
    username: str,
    *,
    interval_sec: float = 5.0,
    sent_at: list[float] | None = None,
) -> threading.Event:
    stop_event = threading.Event()

    def run() -> None:
        while not stop_event.is_set():
            if sent_at is not None:
                sent_at.append(time.monotonic())
            write_commands(
                proc,
                [
                    f"data get entity {username} Pos",
                    f"data get entity {username} Rotation",
                ],
            )
            stop_event.wait(interval_sec)

    threading.Thread(target=run, daemon=True).start()
    return stop_event


def wait_for_position_sample(
    log_path: Path,
    username: str,
    *,
    proc: subprocess.Popen | None = None,
    after_count: int = 0,
    wait_sec: float = 10.0,
) -> int:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"Minecraft server exited before position probe sample; see {log_path}")
        count = len(parse_position_log(log_path, username=username))
        if count > after_count:
            return count
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for position probe sample; see {log_path}")


def write_positions_jsonl(
    log_path: Path,
    out_path: Path,
    *,
    username: str,
    sent_at: list[float] | None = None,
    replay_start_mono: float | None = None,
    replay_log_path: Path | None = None,
) -> int:
    positions = parse_position_log(log_path, username=username)
    rotations = parse_rotation_log(log_path, username=username)
    t_rel_baseline = _position_time_baseline(
        replay_log_path=replay_log_path,
        fallback_mono=replay_start_mono,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for idx, item in enumerate(positions):
            row = {"idx": idx, **item}
            if idx < len(rotations):
                row["yaw"] = rotations[idx]["yaw"]
            if sent_at is not None and t_rel_baseline is not None and idx < len(sent_at):
                row["t_rel"] = sent_at[idx] - t_rel_baseline
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return len(positions)


def replay_start_mono_from_log(replay_log_path: Path) -> float | None:
    if not replay_log_path.exists():
        return None
    first_line = replay_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not first_line:
        return None
    try:
        first = json.loads(first_line[0])
    except json.JSONDecodeError:
        return None
    if first.get("event") != "start":
        return None
    mono = first.get("mono")
    if not isinstance(mono, int | float):
        return None
    return float(mono)


def parse_position_log(log_path: Path, *, username: str) -> list[dict[str, float]]:
    if not log_path.exists():
        return []
    needle = f"{username} has the following entity data:"
    positions: list[dict[str, float]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle not in line:
            continue
        raw = line.split(needle, 1)[1]
        values = _parse_position_values(raw)
        if values is not None:
            x, y, z = values
            positions.append({"x": x, "y": y, "z": z})
    return positions


def parse_rotation_log(log_path: Path, *, username: str) -> list[dict[str, float]]:
    if not log_path.exists():
        return []
    needle = f"{username} has the following entity data:"
    rotations: list[dict[str, float]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if needle not in line:
            continue
        raw = line.split(needle, 1)[1]
        values = _parse_rotation_values(raw)
        if values is not None:
            yaw, _pitch = values
            rotations.append({"yaw": yaw})
    return rotations


def _position_time_baseline(
    *,
    replay_log_path: Path | None,
    fallback_mono: float | None,
) -> float | None:
    if replay_log_path is not None:
        replay_start = replay_start_mono_from_log(replay_log_path)
        if replay_start is not None:
            return replay_start
        if fallback_mono is not None:
            console.print(
                f"Warning: replay start not found in {replay_log_path}; "
                "using capture-ready position baseline."
            )
    return fallback_mono


def _parse_position_values(raw: str) -> tuple[float, float, float] | None:
    values = [
        float(match.group(1))
        for match in re.finditer(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)[dDfF]?", raw)
    ]
    if len(values) < 3:
        return None
    return values[0], values[1], values[2]


def _parse_rotation_values(raw: str) -> tuple[float, float] | None:
    values = [
        float(match.group(1))
        for match in re.finditer(r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)[dDfF]?", raw)
    ]
    if len(values) != 2:
        return None
    return values[0], values[1]
