from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

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
    write_commands(proc, commands)


def apply_join_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    commands = [*_time_weather_commands(state)]
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
    origin = scene.get("origin", [0, 64, 0])
    ox, oy, oz = (int(origin[0]), int(origin[1]), int(origin[2]))
    return [
        f"forceload add {ox - 32} {oz - 32} {ox + 32} {oz + 32}",
        f"fill {ox - 18} {oy} {oz - 18} {ox + 18} {oy + 22} {oz + 18} minecraft:air",
        f"fill {ox - 18} {oy + 23} {oz - 18} {ox + 18} {oy + 28} {oz + 18} minecraft:air",
        f"fill {ox - 24} {oy - 4} {oz - 24} {ox + 24} {oy - 2} {oz + 24} minecraft:dirt",
        f"fill {ox - 24} {oy - 1} {oz - 24} {ox + 24} {oy - 1} {oz + 24} minecraft:grass_block",
        f"fill {ox - 15} {oy - 1} {oz - 15} {ox + 15} {oy - 1} {oz + 15} minecraft:smooth_stone",
        f"fill {ox - 14} {oy - 1} {oz - 2} {ox - 5} {oy - 1} {oz + 7} minecraft:water",
        f"fill {ox - 14} {oy - 2} {oz - 2} {ox - 5} {oy - 2} {oz + 7} minecraft:blue_concrete",
        f"fill {ox + 5} {oy} {oz - 2} {ox + 14} {oy} {oz + 7} minecraft:glass",
        f"fill {ox + 5} {oy - 1} {oz - 2} {ox + 14} {oy - 1} {oz + 7} minecraft:white_concrete",
        f"fill {ox - 2} {oy} {oz + 9} {ox + 2} {oy + 3} {oz + 9} minecraft:oak_leaves",
        f"fill {ox - 4} {oy} {oz + 14} {ox + 4} {oy + 4} {oz + 14} minecraft:white_concrete",
        f"setblock {ox - 10} {oy} {oz - 10} minecraft:torch",
        f"setblock {ox - 7} {oy} {oz - 10} minecraft:lantern",
        f"setblock {ox - 4} {oy} {oz - 10} minecraft:redstone_torch",
        f"setblock {ox - 1} {oy} {oz - 10} minecraft:redstone_lamp[lit=true]",
        f"fill {ox + 1} {oy} {oz - 11} {ox + 3} {oy} {oz - 9} minecraft:glass",
        f"setblock {ox + 2} {oy} {oz - 10} minecraft:lava",
        f"setblock {ox + 5} {oy} {oz - 10} minecraft:sea_lantern",
        f"setblock {ox + 8} {oy} {oz - 10} minecraft:glowstone",
        f"setblock {ox + 11} {oy} {oz - 10} minecraft:beacon",
        f"setblock {ox - 14} {oy} {oz + 12} minecraft:oak_log",
        f"setblock {ox - 14} {oy + 1} {oz + 12} minecraft:oak_leaves",
        f"setblock {ox + 14} {oy} {oz + 12} minecraft:polished_deepslate",
        f"setblock {ox + 14} {oy + 1} {oz + 12} minecraft:glass",
    ]


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
