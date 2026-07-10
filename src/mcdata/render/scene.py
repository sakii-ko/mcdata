from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from mcdata.scene_model import MAX_FILL_VOLUME, parse_scene, scene_commands

SCENE_FAILURE_PATTERNS = (
    "Too many blocks",
    "Cannot place",
    "Incorrect argument for command",
    "Expected",
    "Unknown",
)
SERVER_MUTATION_RECEIPT_PATTERNS = (
    "Successfully filled",
    "Changed the block",
    "No blocks were filled",
    "Biomes set between",
    "biome entry/entries set between",
)
MAX_BIOME_VOLUME = 32768


def apply_world_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    gamerules = dict(state.get("gamerules", {}) or {})
    commands = [
        *_gamerule_commands(gamerules),
        *_time_weather_commands(state),
        *_scene_commands(state.get("scene", {}) if isinstance(state.get("scene"), dict) else {}),
        *_biome_commands(state.get("biome", {}) if isinstance(state.get("biome"), dict) else {}),
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
    """Count scene and biome mutations that must emit server receipts before launch."""
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    scene = state.get("scene", {}) if isinstance(state.get("scene"), dict) else {}
    biome = state.get("biome", {}) if isinstance(state.get("biome"), dict) else {}
    return _count_receipted_scene_commands(_scene_commands(scene)) + len(_biome_commands(biome))


def verify_scene_commands(
    log_path: Path,
    *,
    expected_fill_count: int,
    wait_sec: float = 10.0,
    poll_sec: float = 0.2,
) -> int:
    """Wait for every configured scene/biome mutation receipt, failing on server errors."""
    deadline = time.time() + wait_sec
    last_count = 0
    while time.time() <= deadline:
        lines = _read_log_lines(log_path)
        for line in lines:
            if _is_scene_failure_line(line):
                raise RuntimeError(f"Server mutation failed: {line.strip()} (see {log_path})")
        last_count = sum(1 for line in lines if _is_scene_receipt_line(line))
        if last_count == expected_fill_count:
            return last_count
        if last_count > expected_fill_count:
            raise RuntimeError(
                f"Server mutation receipt count exceeded expected count: "
                f"saw {last_count}/{expected_fill_count}; see {log_path}"
            )
        time.sleep(poll_sec)
    raise TimeoutError(
        f"Timed out waiting for scene/biome mutation receipts: "
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
    return any(pattern in line for pattern in SERVER_MUTATION_RECEIPT_PATTERNS)


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


def _biome_commands(biome: dict[str, Any]) -> list[str]:
    if not biome:
        return []
    biome_id = biome.get("id")
    precipitation = biome.get("precipitation")
    regions = biome.get("regions")
    if not isinstance(biome_id, str) or not biome_id.strip():
        raise ValueError("world_state.biome.id must be a non-empty string")
    if precipitation not in {"rain", "snow"}:
        raise ValueError("world_state.biome.precipitation must be rain or snow")
    if not isinstance(regions, list) or not regions:
        raise ValueError("world_state.biome.regions must be a non-empty list")
    commands = []
    for index, value in enumerate(regions):
        if not isinstance(value, dict):
            raise ValueError(f"world_state.biome.regions[{index}] must be a mapping")
        start = _coordinate_triple(value.get("from"), f"biome region {index} from")
        end = _coordinate_triple(value.get("to"), f"biome region {index} to")
        volume = 1
        for left, right in zip(start, end, strict=True):
            volume *= abs(right - left) + 1
        if volume > MAX_BIOME_VOLUME:
            raise ValueError(
                f"world_state.biome.regions[{index}] volume {volume} exceeds {MAX_BIOME_VOLUME}"
            )
        commands.append(
            f"fillbiome {start[0]} {start[1]} {start[2]} {end[0]} {end[1]} {end[2]} {biome_id}"
        )
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
    additions = scene.get("additions")
    if additions is None:
        return scene_commands(parse_scene(scene))
    variant = scene.get("variant")
    if not isinstance(variant, str) or not variant.strip():
        raise ValueError("world_state.scene.variant must identify configured scene additions")
    if not isinstance(additions, list) or not additions:
        raise ValueError("world_state.scene.additions must be a non-empty list")
    if any(not isinstance(entry, dict) for entry in additions):
        raise ValueError("world_state.scene.additions entries must be mappings")
    origin = _coordinate_triple(scene.get("origin"), "scene additions origin")
    for index, entry in enumerate(additions):
        if entry.get("kind") != "fill":
            continue
        start = _coordinate_triple(entry.get("from"), f"scene addition {index} from")
        end = _coordinate_triple(entry.get("to"), f"scene addition {index} to")
        volume = 1
        for left, right in zip(start, end, strict=True):
            volume *= abs(right - left) + 1
        if volume > MAX_FILL_VOLUME:
            raise ValueError(
                f"world_state.scene.additions[{index}] volume {volume} exceeds "
                f"{MAX_FILL_VOLUME}"
            )
    addition_spec = parse_scene({"origin": list(origin), "entries": additions})
    if any(entry.kind == "forceload" for entry in addition_spec.entries):
        raise ValueError("world_state.scene.additions cannot contain forceload entries")
    base = scene_commands(parse_scene(scene))
    return [*base, *scene_commands(addition_spec)]


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


def _coordinate_triple(value: Any, label: str) -> tuple[int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(item) is not int for item in value)
    ):
        raise ValueError(f"{label} must contain exactly three integers")
    return value[0], value[1], value[2]
