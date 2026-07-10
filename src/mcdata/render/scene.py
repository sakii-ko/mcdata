from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from mcdata.scene_model import parse_scene, scene_commands

SCENE_FAILURE_PATTERNS = (
    "Too many blocks",
    "Cannot place",
    "Expected",
    "Unknown",
)
SCENE_RECEIPT_PATTERNS = (
    "Successfully filled",
    "Changed the block",
    "No blocks were filled",
)


def apply_world_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    gamerules = dict(state.get("gamerules", {}) or {})
    commands = [
        *_gamerule_commands(gamerules),
        *_time_weather_commands(state),
        *_scene_commands(state.get("scene", {}) if isinstance(state.get("scene"), dict) else {}),
    ]
    if state.get("clear_non_player_entities"):
        commands.append("kill @e[type=!minecraft:player]")
    if state.get("clear_dropped_items"):
        commands.append("kill @e[type=minecraft:item]")
    write_commands(proc, commands)


def apply_join_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    commands = [*_time_weather_commands(state)]
    if state.get("clear_inventory"):
        commands.append("clear @a")
    if state.get("pregrant_recipes"):
        commands.append("recipe give @a *")
    player = state.get("player", {}) if isinstance(state.get("player"), dict) else {}
    if player:
        commands.append(_tp_command("@a", player))
    write_commands(proc, commands)


def expected_scene_fill_count(profile: dict[str, Any]) -> int:
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    scene = state.get("scene", {}) if isinstance(state.get("scene"), dict) else {}
    return _count_receipted_scene_commands(_scene_commands(scene))


def verify_scene_commands(
    log_path: Path,
    *,
    expected_fill_count: int,
    wait_sec: float = 10.0,
    poll_sec: float = 0.2,
) -> int:
    deadline = time.time() + wait_sec
    last_count = 0
    while time.time() <= deadline:
        lines = _read_log_lines(log_path)
        for line in lines:
            if _is_scene_failure_line(line):
                raise RuntimeError(f"Scene command failed: {line.strip()} (see {log_path})")
        last_count = sum(1 for line in lines if _is_scene_receipt_line(line))
        if last_count == expected_fill_count:
            return last_count
        if last_count > expected_fill_count:
            raise RuntimeError(
                f"Scene command receipt count exceeded expected count: "
                f"saw {last_count}/{expected_fill_count}; see {log_path}"
            )
        time.sleep(poll_sec)
    raise TimeoutError(
        f"Timed out waiting for scene command receipts: "
        f"saw {last_count}/{expected_fill_count}; see {log_path}"
    )


def write_commands(proc: subprocess.Popen, commands: list[str]) -> None:
    if proc.stdin is None:
        return
    try:
        for command in commands:
            proc.stdin.write(command + "\n")
        proc.stdin.flush()
    except OSError:
        return


def _read_log_lines(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return log_path.read_text(encoding="utf-8", errors="replace").splitlines()


def _is_scene_failure_line(line: str) -> bool:
    return any(pattern in line for pattern in SCENE_FAILURE_PATTERNS)


def _is_scene_receipt_line(line: str) -> bool:
    return any(pattern in line for pattern in SCENE_RECEIPT_PATTERNS)


def _count_receipted_scene_commands(commands: list[str]) -> int:
    return sum(1 for command in commands if command.startswith(("fill ", "setblock ")))


def _gamerule_commands(gamerules: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for key, value in sorted(gamerules.items()):
        commands.append(f"gamerule {key} {_bool_or_value(value)}")
    return commands


def _time_weather_commands(state: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    time_value = state.get("time")
    if time_value is not None:
        commands.append(f"time set {time_value}")
    weather = state.get("weather")
    if weather:
        commands.append(f"weather {weather} {int(state.get('weather_duration_sec', 999999))}")
    return commands


def _tp_command(target: str, player: dict[str, Any]) -> str:
    return (
        f"tp {target} "
        f"{_num(player.get('x', 0))} {_num(player.get('y', 67))} {_num(player.get('z', -12))} "
        f"{_num(player.get('yaw', 0))} {_num(player.get('pitch', 15))}"
    )


def _scene_commands(scene: dict[str, Any]) -> list[str]:
    if not scene.get("enabled", True):
        return []
    return scene_commands(parse_scene(scene))


def _bool_or_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _num(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)
