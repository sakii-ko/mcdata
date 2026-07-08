from __future__ import annotations

import os
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from mcdata.mojang import version_manifest
from mcdata.net import download_file, get_json

console = Console()


def ensure_server(
    server_root: Path,
    launcher_dir: Path,
    *,
    game_version: str,
    profile_name: str,
    profile: dict[str, Any],
    lane: str | None = None,
) -> dict[str, Any]:
    server_profile = server_profile_name(profile, profile_name=profile_name, lane=lane)
    server_dir = server_root / server_profile
    cache_dir = server_root / "cache"
    server_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    jar = server_dir / f"minecraft_server.{game_version}.jar"
    cached_jar = cache_dir / f"minecraft_server.{game_version}.jar"
    if not cached_jar.exists():
        url = _server_download_url(game_version)
        console.print(f"Downloading Minecraft server {game_version} -> {cached_jar.name}")
        download_file(url, cached_jar)
    if not jar.exists():
        try:
            jar.symlink_to(os.path.relpath(cached_jar, server_dir))
        except OSError:
            jar.write_bytes(cached_jar.read_bytes())

    (server_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    _write_server_properties(server_dir / "server.properties", profile, level_name=server_profile)
    java = _java_path(launcher_dir)
    return {"server_dir": server_dir, "jar": jar, "java": java}


def start_server(
    server_root: Path,
    launcher_dir: Path,
    *,
    game_version: str,
    profile_name: str,
    profile: dict[str, Any],
    run_dir: Path,
    lane: str | None = None,
    wait_sec: int = 45,
) -> subprocess.Popen:
    info = ensure_server(
        server_root,
        launcher_dir,
        game_version=game_version,
        profile_name=profile_name,
        profile=profile,
        lane=lane,
    )
    log_path = run_dir / "server.log"
    memory = str(profile.get("server_memory", "2G"))
    cmd = [
        str(info["java"]),
        f"-Xms{memory}",
        f"-Xmx{memory}",
        "-jar",
        str(info["jar"]),
        "nogui",
    ]
    console.print("Starting local Minecraft server...")
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=info["server_dir"],
        stdin=subprocess.PIPE,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    _wait_for_server_log(log_path, proc, wait_sec=wait_sec)
    _apply_world_state(proc, profile)
    return proc


def _server_download_url(game_version: str) -> str:
    manifest = version_manifest()
    version_url = None
    for item in manifest.get("versions", []):
        if item.get("id") == game_version:
            version_url = item.get("url")
            break
    if not version_url:
        raise RuntimeError(f"Could not find Mojang metadata for version {game_version}")
    data = get_json(str(version_url))
    downloads = data.get("downloads", {}) if isinstance(data, dict) else {}
    server = downloads.get("server")
    if not server or not server.get("url"):
        raise RuntimeError(f"No server jar in Mojang metadata for {game_version}")
    return str(server["url"])


def server_profile_name(
    profile: dict[str, Any],
    *,
    profile_name: str,
    lane: str | None = None,
) -> str:
    name = str(profile.get("world_profile") or profile_name)
    if lane:
        return f"{name}__{lane}"
    return name


def _write_server_properties(path: Path, profile: dict[str, Any], *, level_name: str | None = None) -> None:
    props = {
        "allow-flight": "true",
        "difficulty": "peaceful",
        "enable-command-block": "true",
        "gamemode": str(profile.get("gamemode", "creative")),
        "generate-structures": "true",
        "level-name": level_name or str(profile.get("world_profile", "world")),
        "level-seed": str(profile.get("world_seed", 1)),
        "max-players": "4",
        "motd": "mcdata",
        "online-mode": "false",
        "pvp": "false",
        "server-ip": "127.0.0.1",
        "server-port": str(profile.get("server_port", 25565)),
        "simulation-distance": str(profile.get("simulation_distance", 4)),
        "spawn-protection": "0",
        "view-distance": str(profile.get("server_view_distance", 8)),
    }
    path.write_text("\n".join(f"{k}={v}" for k, v in sorted(props.items())) + "\n", encoding="utf-8")


def _java_path(launcher_dir: Path) -> Path:
    candidates = sorted(launcher_dir.glob("jvm/*/bin/java"))
    if candidates:
        return candidates[-1]
    return Path("java")


def _wait_for_server_log(log_path: Path, proc: subprocess.Popen, *, wait_sec: int) -> None:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Minecraft server exited early; see {log_path}")
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if "Done (" in text or "For help, type" in text:
                return
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for Minecraft server; see {log_path}")


def wait_for_player_join(log_path: Path, player: str, *, proc: subprocess.Popen | None = None, wait_sec: int = 120) -> None:
    deadline = time.time() + wait_sec
    needle = f"{player} joined the game"
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"Minecraft server exited before {player} joined; see {log_path}")
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            if needle in text:
                return
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for {player} to join; see {log_path}")


def _apply_world_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    gamerules = dict(state.get("gamerules", {}) or {})
    commands = [
        *_gamerule_commands(gamerules),
        *_time_weather_commands(state),
        *_scene_commands(state.get("scene", {}) if isinstance(state.get("scene"), dict) else {}),
    ]
    _write_commands(proc, commands)


def apply_join_state(proc: subprocess.Popen, profile: dict[str, Any]) -> None:
    if proc.stdin is None:
        return
    state = profile.get("world_state", {}) if isinstance(profile.get("world_state"), dict) else {}
    commands = [*_time_weather_commands(state)]
    player = state.get("player", {}) if isinstance(state.get("player"), dict) else {}
    if player:
        commands.append(_tp_command("@a", player))
    _write_commands(proc, commands)


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
            _write_commands(
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
) -> int:
    positions = parse_position_log(log_path, username=username)
    rotations = parse_rotation_log(log_path, username=username)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for idx, item in enumerate(positions):
            row = {"idx": idx, **item}
            if idx < len(rotations):
                row["yaw"] = rotations[idx]["yaw"]
            if sent_at is not None and replay_start_mono is not None and idx < len(sent_at):
                row["t_rel"] = sent_at[idx] - replay_start_mono
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return len(positions)


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


def _write_commands(proc: subprocess.Popen, commands: list[str]) -> None:
    if proc.stdin is None:
        return
    try:
        for command in commands:
            proc.stdin.write(command + "\n")
        proc.stdin.flush()
    except OSError:
        return


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
        f"fill {ox - 18} {oy} {oz - 18} {ox + 18} {oy + 28} {oz + 18} minecraft:air",
        f"fill {ox - 24} {oy - 4} {oz - 24} {ox + 24} {oy - 2} {oz + 24} minecraft:dirt",
        f"fill {ox - 24} {oy - 1} {oz - 24} {ox + 24} {oy - 1} {oz + 24} minecraft:grass_block",
        f"fill {ox - 15} {oy - 1} {oz - 15} {ox + 15} {oy - 1} {oz + 15} minecraft:smooth_stone",
        f"fill {ox - 14} {oy} {oz - 2} {ox - 5} {oy} {oz + 7} minecraft:water",
        f"fill {ox - 14} {oy - 1} {oz - 2} {ox - 5} {oy - 1} {oz + 7} minecraft:blue_concrete",
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
