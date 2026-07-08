from __future__ import annotations

import json
import os
import platform
import signal
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from mcdata.config import load_asset_config, load_profile
from mcdata.manifest import build_run_manifest, write_run_manifest
from mcdata.mojang import latest_release, release_versions
from mcdata.modrinth import project_versions
from mcdata.packs import install_asset_set, install_mods
from mcdata.paths import ProjectPaths, ensure_dir
from mcdata.qa.probe import probe_video
from mcdata.render.options import write_iris_config, write_options
from mcdata.render.server import apply_join_state, start_server, wait_for_player_join
from mcdata.runlog import RunLogger
from mcdata.settings import CaptureSettings

console = Console()

REQUIRED_MODS = ["fabric-api", "sodium", "iris"]


def resolve_game_version(profile: dict[str, Any]) -> str:
    explicit = profile.get("game_version")
    if explicit:
        return str(explicit)

    strategy = profile.get("version_strategy", "latest_release")
    if strategy == "latest_release":
        return latest_release()
    if strategy == "latest_modded":
        return latest_modded_version()
    raise RuntimeError(f"Unknown version strategy: {strategy}")


def latest_modded_version() -> str:
    for version in release_versions(limit=80):
        ok = True
        for slug in REQUIRED_MODS:
            if not project_versions(slug, game_version=version.id, loaders=["fabric"]):
                ok = False
                break
        if ok:
            return version.id
    raise RuntimeError("Could not find a recent release supported by Fabric API, Sodium, and Iris")


def portablemc_base(paths: ProjectPaths, work_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "portablemc",
        "--main-dir",
        str(paths.main_dir),
        "--work-dir",
        str(work_dir),
        "--output",
        "machine",
    ]


def portablemc_version(profile: dict[str, Any], game_version: str) -> str:
    loader = profile.get("loader", "vanilla")
    if loader == "vanilla":
        return game_version
    if loader == "fabric":
        return f"fabric:{game_version}"
    raise RuntimeError(f"Unsupported loader: {loader}")


def bootstrap_profile(root: Path, profile_name: str, *, game_version: str | None = None) -> dict[str, Any]:
    paths = ProjectPaths.from_root(root)
    profile = load_profile(paths.configs, profile_name)
    game_version = game_version or resolve_game_version(profile)
    work_dir = ensure_dir(paths.instance_dir(profile_name))
    server_root = ensure_dir((paths.root / str(profile.get("server_dir", ".mcdata/servers"))).resolve())
    ensure_dir(paths.main_dir)

    console.print(f"Profile: {profile_name}")
    console.print(f"Minecraft version: {game_version}")
    console.print(f"Instance: {work_dir}")

    mods: list[str] = []
    if profile.get("loader") == "fabric":
        mods = install_mods(work_dir, game_version=game_version, slugs=list(profile.get("mods", [])))

    asset_config = load_asset_config(paths.configs)
    resourcepacks, shaderpack = install_asset_set(
        work_dir,
        game_version=game_version,
        asset_config=asset_config,
        asset_set_name=str(profile.get("asset_set", "vanilla")),
    )

    write_options(
        work_dir,
        quality=str(profile.get("quality", "low")),
        resourcepacks=resourcepacks,
        overrides=dict(profile.get("options", {}) or {}),
    )
    write_iris_config(work_dir, shaderpack=shaderpack, enabled=bool(shaderpack))
    _write_manifest(
        work_dir,
        {
            "profile": profile_name,
            "minecraft_version": game_version,
            "loader": profile.get("loader", "vanilla"),
            "asset_set": profile.get("asset_set", "vanilla"),
            "mods": mods,
            "resourcepacks": resourcepacks,
            "shaderpack": shaderpack,
            "server_dir": str(server_root / profile_name),
            "server_port": profile.get("server_port", 25565),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    install_cmd = portablemc_base(paths, work_dir) + [
        "start",
        "--dry",
        "--resolution",
        f"{profile.get('width')}x{profile.get('height')}",
        "--username",
        str(profile.get("username")),
        "--jvm-args",
        str(profile.get("jvm_args")),
        portablemc_version(profile, game_version),
    ]
    console.print("Installing launcher assets with PortableMC dry run...")
    subprocess.run(install_cmd, cwd=paths.root, check=True)
    return {"profile": profile, "game_version": game_version, "work_dir": str(work_dir)}


def launch_profile(
    root: Path,
    profile_name: str,
    *,
    dry_run: bool,
    capture: bool,
    strategy: str | None,
    duration: int | None,
    with_server: bool,
    replay_actions: bool,
    trajectory_path: Path | None,
    game_version: str | None = None,
) -> dict[str, Any]:
    paths = ProjectPaths.from_root(root)
    profile = load_profile(paths.configs, profile_name)
    game_version = game_version or resolve_game_version(profile)
    work_dir = paths.instance_dir(profile_name)
    if not work_dir.exists():
        bootstrap_profile(root, profile_name, game_version=game_version)

    run_dir = _run_dir(paths.output_dir, profile_name)
    started_at = datetime.now(timezone.utc).isoformat()
    capture_settings = CaptureSettings.from_env(profile)
    run_trajectory_path = _copy_trajectory(run_dir, trajectory_path) if trajectory_path else None
    trajectory_info = _trajectory_manifest(
        run_trajectory_path,
        source_path=trajectory_path,
        strategy=strategy,
    )
    metadata = {
        "profile": profile_name,
        "minecraft_version": game_version,
        "work_dir": str(work_dir),
        "run_dir": str(run_dir),
        "strategy": strategy,
        "duration": duration,
        "capture": capture,
        "with_server": with_server,
        "replay_actions": replay_actions,
        "started_at": started_at,
    }
    _write_json(run_dir / "metadata.json", metadata)

    cmd = portablemc_base(paths, work_dir) + [
        "start",
        "--resolution",
        f"{capture_settings.width}x{capture_settings.height}",
        "--username",
        str(profile.get("username")),
        "--jvm-args",
        str(profile.get("jvm_args")),
    ]
    if dry_run:
        cmd.append("--dry")
    if with_server:
        cmd += ["--server", "127.0.0.1", "--server-port", str(profile.get("server_port", 25565))]
    cmd.append(portablemc_version(profile, game_version))

    console.print("Launch command:")
    console.print(" ".join(shlex.quote(part) for part in cmd))
    server_proc: subprocess.Popen | None = None
    game_proc: subprocess.Popen | None = None
    capture_proc: subprocess.Popen | None = None
    replay_thread: threading.Thread | None = None
    capture_cmd: list[str] | None = None
    error: str | None = None
    ready_event = threading.Event()
    with RunLogger(run_dir, console=console) as runlog:
        runlog.log(
            "launch",
            "command",
            cmd=cmd,
            dry_run=dry_run,
            game_version=game_version,
            capture_settings=asdict(capture_settings),
        )
        try:
            if with_server and not dry_run:
                runlog.log("server", "start")
                server_proc = start_server(
                    (paths.root / str(profile.get("server_dir", ".mcdata/servers"))).resolve(),
                    paths.main_dir,
                    game_version=game_version,
                    profile_name=profile_name,
                    profile=profile,
                    run_dir=run_dir,
                )
                runlog.log("server", "started", pid=server_proc.pid)
            if replay_actions and run_trajectory_path and not dry_run:
                replay_thread = _start_replay_thread(
                    run_trajectory_path,
                    start_event=ready_event,
                    run_dir=run_dir,
                )
                runlog.log("replay", "thread_started", trajectory=str(run_trajectory_path))
            if dry_run:
                runlog.log("launch", "dry_run_start")
                subprocess.run(cmd, cwd=paths.root, check=True)
                runlog.log("launch", "dry_run_complete")
            else:
                game_proc = subprocess.Popen(cmd, cwd=paths.root, start_new_session=True)
                runlog.log("launch", "process_started", pid=game_proc.pid)
                if with_server and server_proc and (capture or replay_actions):
                    runlog.log("join", "wait_start", username=str(profile.get("username")))
                    wait_for_player_join(
                        run_dir / "server.log",
                        str(profile.get("username")),
                        proc=server_proc,
                        wait_sec=int(profile.get("join_timeout_sec", 180)),
                    )
                    runlog.log("join", "player_joined", username=str(profile.get("username")))
                    apply_join_state(server_proc, profile)
                    runlog.log("join", "apply_join_state")
                    if capture_settings.ready_delay_sec > 0:
                        console.print(
                            "Player joined; waiting "
                            f"{capture_settings.ready_delay_sec:.1f}s before capture/actions..."
                        )
                        runlog.log("warmup", "start", seconds=capture_settings.ready_delay_sec)
                        _wait_or_raise_if_exited(game_proc, capture_settings.ready_delay_sec)
                        runlog.log("warmup", "end")
                    if capture:
                        _prepare_capture_view(capture_settings)
                        runlog.log("capture", "view_prepared")
                if capture:
                    capture_proc, capture_cmd = _start_capture(
                        run_dir,
                        settings=capture_settings,
                        duration=duration,
                    )
                    runlog.log("capture", "start", cmd=capture_cmd, pid=capture_proc.pid)
                    ready_event.set()
                    _wait_for_capture(game_proc, capture_proc, run_dir / "game.exitcode")
                    runlog.log("capture", "stop", returncode=capture_proc.returncode)
                else:
                    ready_event.set()
                    _wait_for_game(game_proc, run_dir / "game.exitcode", duration=duration)
                    runlog.log("launch", "process_exit", returncode=game_proc.returncode)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            runlog.log("teardown", "error", error=error)
            raise
        finally:
            ready_event.set()
            if capture_proc and capture_proc.poll() is None:
                _terminate_process_tree(capture_proc, timeout=10)
                runlog.log("teardown", "capture_terminated", returncode=capture_proc.returncode)
            if game_proc and game_proc.poll() is None:
                _terminate_process_tree(game_proc, timeout=20)
                runlog.log("teardown", "game_terminated", returncode=game_proc.returncode)
            if server_proc and server_proc.poll() is None:
                _terminate_process_tree(server_proc, timeout=20)
                runlog.log("teardown", "server_terminated", returncode=server_proc.returncode)
            if replay_thread:
                replay_thread.join(timeout=2)
                runlog.log("replay", "thread_joined", alive=replay_thread.is_alive())
            manifest = build_run_manifest(
                run_id=run_dir.name,
                profile_name=profile_name,
                profile=profile,
                mc_version=game_version,
                resources=_resource_manifest(work_dir, profile),
                trajectory=trajectory_info,
                capture=_capture_manifest(
                    enabled=capture,
                    settings=capture_settings,
                    ffmpeg_cmd=capture_cmd,
                    run_dir=run_dir,
                ),
                env=_env_manifest(display=capture_settings.display),
                git=_git_manifest(paths.root),
                started_at=started_at,
                ended_at=datetime.now(timezone.utc).isoformat(),
                error=error,
            )
            write_run_manifest(run_dir, manifest)
            runlog.log("teardown", "manifest_written", path=str(run_dir / "manifest.json"))
    return metadata


def remote_tmux_command(
    *,
    project_dir: str,
    profile: str,
    session: str,
    display: str,
    capture: bool,
    strategy: str,
    duration: int,
    with_server: bool,
    replay_actions: bool,
) -> str:
    run = (
        f"cd {shlex.quote(project_dir)} && "
        f". .venv/bin/activate && "
        f"export DISPLAY={shlex.quote(display)} && "
        f"mcdata bootstrap --profile {shlex.quote(profile)} && "
        f"mcdata run --profile {shlex.quote(profile)} "
        f"--strategy {shlex.quote(strategy)} --duration {duration}"
    )
    if capture:
        run += " --capture"
    if with_server:
        run += " --with-server"
    if replay_actions:
        run += " --replay-actions"
    return f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(run)}"


def _start_replay_thread(
    trajectory_path: Path,
    *,
    start_event: threading.Event,
    run_dir: Path,
) -> threading.Thread:
    from mcdata.actions.replay import replay_trajectory

    thread = threading.Thread(
        target=replay_trajectory,
        args=(trajectory_path,),
        kwargs={"start_event": start_event, "run_dir": run_dir},
        daemon=True,
    )
    thread.start()
    return thread


def _prepare_capture_view(settings: CaptureSettings) -> None:
    from mcdata.actions.replay import prepare_capture_view

    console.print("Preparing in-game view for capture...")
    prepare_capture_view(hide_hud=settings.hide_hud, settle_sec=settings.view_settle_sec)


def _start_capture(
    run_dir: Path,
    *,
    settings: CaptureSettings,
    duration: int | None,
) -> tuple[subprocess.Popen, list[str]]:
    capture_file = run_dir / "capture.mp4"
    capture_input = _capture_input(
        settings.display,
        width=settings.width,
        height=settings.height,
        desktop=settings.desktop,
    )
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-video_size",
        f"{settings.width}x{settings.height}",
        "-framerate",
        str(settings.fps),
        "-f",
        "x11grab",
        "-i",
        capture_input,
    ]
    if duration:
        ffmpeg_cmd += ["-t", str(duration)]
    ffmpeg_cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(capture_file)]
    console.print("Starting capture:")
    console.print(" ".join(shlex.quote(part) for part in ffmpeg_cmd))
    log = (run_dir / "capture.log").open("w", encoding="utf-8")
    return (
        subprocess.Popen(
            ffmpeg_cmd,
            cwd=run_dir,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        ),
        ffmpeg_cmd,
    )


def _capture_input(display: str, *, width: int, height: int, desktop: bool) -> str:
    if desktop:
        return display
    geometry = _minecraft_window_geometry()
    if geometry is None:
        return display
    x, y, window_width, window_height = geometry
    if window_width < width or window_height < height:
        console.print(
            f"Window is {window_width}x{window_height}; falling back to display capture {display}."
        )
        return display
    return f"{display}+{x},{y}"


def _minecraft_window_geometry(window_name: str = "Minecraft") -> tuple[int, int, int, int] | None:
    try:
        result = subprocess.run(
            ["xwininfo", "-name", window_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    values: dict[str, int] = {}
    for line in result.stdout.splitlines():
        text = line.strip()
        for key, label in {
            "x": "Absolute upper-left X:",
            "y": "Absolute upper-left Y:",
            "width": "Width:",
            "height": "Height:",
        }.items():
            if text.startswith(label):
                try:
                    values[key] = int(text.removeprefix(label).strip())
                except ValueError:
                    return None
    if {"x", "y", "width", "height"} <= values.keys():
        return values["x"], values["y"], values["width"], values["height"]
    return None


def _wait_for_capture(game_proc: subprocess.Popen, capture_proc: subprocess.Popen, exitcode_file: Path) -> None:
    while capture_proc.poll() is None:
        if game_proc.poll() is not None:
            capture_proc.terminate()
            capture_proc.wait(timeout=10)
            _write_exitcode(exitcode_file, game_proc.returncode)
            if game_proc.returncode:
                raise subprocess.CalledProcessError(game_proc.returncode, game_proc.args)
            return
        time.sleep(0.5)
    if capture_proc.returncode:
        raise subprocess.CalledProcessError(capture_proc.returncode, capture_proc.args)
    if game_proc.poll() is None:
        _terminate_process_tree(game_proc, timeout=20)
        _write_exitcode(exitcode_file, 0)
    else:
        _write_exitcode(exitcode_file, game_proc.returncode)


def _wait_for_game(game_proc: subprocess.Popen, exitcode_file: Path, *, duration: int | None) -> None:
    if duration is None:
        rc = game_proc.wait()
        _write_exitcode(exitcode_file, rc)
        if rc:
            raise subprocess.CalledProcessError(rc, game_proc.args)
        return
    try:
        rc = game_proc.wait(timeout=int(duration) + 90)
        _write_exitcode(exitcode_file, rc)
        if rc:
            raise subprocess.CalledProcessError(rc, game_proc.args)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(game_proc, timeout=20)
        _write_exitcode(exitcode_file, 0)


def _wait_or_raise_if_exited(game_proc: subprocess.Popen, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if game_proc.poll() is not None:
            raise subprocess.CalledProcessError(game_proc.returncode, game_proc.args)
        time.sleep(min(0.25, deadline - time.monotonic()))


def _copy_trajectory(run_dir: Path, trajectory_path: Path) -> Path:
    dest = run_dir / "trajectory.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.tmp")
    shutil.copy2(trajectory_path, tmp)
    os.replace(tmp, dest)
    return dest


def _trajectory_manifest(
    trajectory_path: Path | None,
    *,
    source_path: Path | None,
    strategy: str | None,
) -> dict[str, Any] | None:
    if trajectory_path is None:
        return None
    data = json.loads(trajectory_path.read_text(encoding="utf-8"))
    return {
        "strategy": strategy,
        "path": str(trajectory_path),
        "source_path": str(source_path) if source_path else None,
        "sha256": _sha256(trajectory_path),
        "event_count": len(data.get("events", [])),
        "duration_sec": data.get("duration_sec"),
        "type": data.get("type"),
    }


def _capture_manifest(
    *,
    enabled: bool,
    settings: CaptureSettings,
    ffmpeg_cmd: list[str] | None,
    run_dir: Path,
) -> dict[str, Any]:
    capture_file = run_dir / "capture.mp4"
    ffprobe: dict[str, Any] | None = None
    if capture_file.exists():
        try:
            ffprobe = probe_video(capture_file)
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            ffprobe = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "enabled": enabled,
        "settings": asdict(settings),
        "file": str(capture_file) if capture_file.exists() else None,
        "ffmpeg_cmd": ffmpeg_cmd,
        "ffprobe": ffprobe,
    }


def _resource_manifest(work_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_set": str(profile.get("asset_set", "vanilla")),
        "mods": _file_manifest_list(work_dir / "mods", suffixes={".jar"}),
        "resourcepacks": _file_manifest_list(work_dir / "resourcepacks", suffixes={".zip"}),
        "shaderpacks": _file_manifest_list(work_dir / "shaderpacks", suffixes={".zip"}),
    }


def _file_manifest_list(path: Path, *, suffixes: set[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for item in sorted(path.iterdir()):
        if not item.is_file() or item.suffix not in suffixes:
            continue
        result.append(
            {
                "filename": item.name,
                "path": str(item),
                "sha256": _sha256(item),
                "size_bytes": item.stat().st_size,
            }
        )
    return result


def _env_manifest(*, display: str) -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "display": display,
        "gl_renderer": _gl_renderer(),
        "gpu": _nvidia_smi(),
    }


def _gl_renderer() -> str | None:
    if not shutil.which("glxinfo"):
        return None
    try:
        result = subprocess.run(
            ["glxinfo", "-B"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if "OpenGL renderer string:" in line:
            return line.split(":", 1)[1].strip()
    return None


def _nvidia_smi() -> list[dict[str, str]]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,driver_version",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    gpus: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 3:
            gpus.append({"index": parts[0], "name": parts[1], "driver_version": parts[2]})
    return gpus


def _git_manifest(root: Path) -> dict[str, Any]:
    commit = _git_output(root, "rev-parse", "HEAD")
    status = _git_output(root, "status", "--porcelain")
    return {
        "commit": commit,
        "dirty": bool(status),
        "status_porcelain": status.splitlines() if status else [],
    }


def _git_output(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_exitcode(path: Path, code: int | None) -> None:
    path.write_text(f"{0 if code is None else code}\n", encoding="utf-8")


def _terminate_process_tree(proc: subprocess.Popen, *, timeout: int) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except PermissionError:
            proc.kill()
        proc.wait(timeout=10)


def _run_dir(output_dir: Path, profile: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"{stamp}_{profile}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_manifest(work_dir: Path, manifest: dict[str, Any]) -> None:
    _write_json(work_dir / "mcdata_manifest.json", manifest)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
