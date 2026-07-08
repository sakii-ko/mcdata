from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from mcdata.mojang import version_manifest
from mcdata.net import download_file, get_json
from mcdata.render.scene import apply_world_state

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
    apply_world_state(proc, profile)
    return proc


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


def wait_for_player_join(
    log_path: Path,
    player: str,
    *,
    proc: subprocess.Popen | None = None,
    wait_sec: int = 120,
) -> None:
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
