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
import fcntl
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from rich.console import Console

from mcdata.action_curriculum import (
    ActionCurriculumError,
    planned_action_contract,
    summarize_action_run,
    validate_action_summary,
)
from mcdata.config import load_asset_config, load_profile
from mcdata.manifest import build_run_manifest, write_run_manifest
from mcdata.mojang import latest_release, release_versions
from mcdata.modrinth import project_versions
from mcdata.packs import install_asset_set, install_mods, load_resourcepack_sources
from mcdata.paths import ProjectPaths, ensure_dir
from mcdata.qa.probe import probe_video
from mcdata.resourcepacks import discover_target_resource_format, materialize_resourcepacks
from mcdata.render.options import write_iris_config, write_options
from mcdata.render.combat import CombatExecutor
from mcdata.render.placement import PlacementExecutor
from mcdata.render.probe import (
    start_position_probe,
    wait_for_position_sample,
    write_positions_jsonl,
)
from mcdata.render.resourcepack_gate import (
    ResourcePackRuntimeError,
    validate_resourcepack_runtime,
)
from mcdata.render.scene import (
    apply_join_state,
    expected_scene_fill_count,
    verify_scene_commands,
)
from mcdata.render.server import (
    server_profile_name,
    start_server,
    wait_for_player_join,
)
from mcdata.runlog import RunLogger
from mcdata.scene_model import load_scene, scene_mapping
from mcdata.settings import CaptureSettings

console = Console()

REQUIRED_MODS = ["fabric-api", "sodium", "iris"]


@dataclass(frozen=True)
class RunOptions:
    dry_run: bool
    capture: bool
    strategy: str | None
    duration: int | None
    with_server: bool
    replay_actions: bool
    trajectory_path: Path | None
    game_version: str | None
    server_port: int | None
    lane: str | None
    probe_interval: float
    debug_no_reapply: bool
    debug_no_replay_gate: bool


@dataclass(frozen=True)
class RunPlan:
    paths: ProjectPaths
    profile_name: str
    profile: dict[str, Any]
    game_version: str
    work_dir: Path
    run_dir: Path
    started_at: str
    capture_settings: CaptureSettings
    run_trajectory_path: Path | None
    trajectory_info: dict[str, Any] | None
    resources: dict[str, Any]
    metadata: dict[str, Any]
    cmd: list[str]
    dry_run: bool
    capture: bool
    duration: int | None
    with_server: bool
    replay_actions: bool
    lane: str | None
    probe_interval: float
    debug_no_reapply: bool
    debug_no_replay_gate: bool


@dataclass
class RunState:
    server_proc: subprocess.Popen | None = None
    game_proc: subprocess.Popen | None = None
    capture_proc: subprocess.Popen | None = None
    replay_thread: threading.Thread | None = None
    replay_stop: threading.Event = field(default_factory=threading.Event)
    ready_event: threading.Event = field(default_factory=threading.Event)
    position_probe_stop: threading.Event | None = None
    position_probe_baseline: int = 0
    position_probe_sent_at: list[float] = field(default_factory=list)
    replay_start_mono: float | None = None
    capture_cmd: list[str] | None = None
    resourcepack_runtime: dict[str, object] | None = None
    placement_executor: PlacementExecutor | CombatExecutor | None = None
    placement_reset_evidence: dict[str, Any] | None = None
    placement_finalized: bool = False
    worker_error: BaseException | None = None
    error: str | None = None


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


@contextmanager
def _profile_instance_lock(paths: ProjectPaths, profile_name: str) -> Iterator[None]:
    lock_dir = paths.work_base / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(profile_name.encode("utf-8")).hexdigest() + ".lock"
    with (lock_dir / lock_name).open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def bootstrap_profile(
    root: Path,
    profile_name: str,
    *,
    game_version: str | None = None,
    server_port: int | None = None,
    lane: str | None = None,
) -> dict[str, Any]:
    paths = ProjectPaths.from_root(root)
    with _profile_instance_lock(paths, profile_name):
        return _bootstrap_profile_unlocked(
            root,
            profile_name,
            game_version=game_version,
            server_port=server_port,
            lane=lane,
        )


def _bootstrap_profile_unlocked(
    root: Path,
    profile_name: str,
    *,
    game_version: str | None = None,
    server_port: int | None = None,
    lane: str | None = None,
) -> dict[str, Any]:
    paths = ProjectPaths.from_root(root)
    profile = _profile_with_overrides(
        _profile_with_scene(paths.configs, profile_name),
        server_port=server_port,
    )
    game_version = game_version or resolve_game_version(profile)
    work_dir = ensure_dir(paths.instance_dir(profile_name))
    server_root = ensure_dir(
        (paths.root / str(profile.get("server_dir", ".mcdata/servers"))).resolve()
    )
    ensure_dir(paths.main_dir)

    console.print(f"Profile: {profile_name}")
    console.print(f"Minecraft version: {game_version}")
    console.print(f"Instance: {work_dir}")

    mods: list[str] = []
    if profile.get("loader") == "fabric":
        mods = install_mods(
            work_dir, game_version=game_version, slugs=list(profile.get("mods", []))
        )

    asset_config = load_asset_config(paths.configs)
    source_resourcepacks, shaderpack = install_asset_set(
        work_dir,
        game_version=game_version,
        asset_config=asset_config,
        asset_set_name=str(profile.get("asset_set", "vanilla")),
    )
    resourcepacks, resourcepack_resolution = _install_client_and_materialize_resourcepacks(
        paths,
        work_dir,
        profile=profile,
        game_version=game_version,
        expected_sources=source_resourcepacks,
    )

    write_options(
        work_dir,
        quality=str(profile.get("quality", "low")),
        resourcepacks=resourcepacks,
        overrides=dict(profile.get("options", {}) or {}),
    )
    write_iris_config(
        work_dir,
        shaderpack=shaderpack,
        enabled=bool(shaderpack),
        shader_options=profile.get("shader_options"),
    )
    _write_manifest(
        work_dir,
        {
            "profile": profile_name,
            "minecraft_version": game_version,
            "loader": profile.get("loader", "vanilla"),
            "asset_set": profile.get("asset_set", "vanilla"),
            "mods": mods,
            "resourcepacks": resourcepacks,
            "resourcepack_resolution": resourcepack_resolution,
            "shaderpack": shaderpack,
            "server_dir": str(
                server_root / server_profile_name(profile, profile_name=profile_name, lane=lane)
            ),
            "server_port": profile.get("server_port", 25565),
            "lane": lane,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"profile": profile, "game_version": game_version, "work_dir": str(work_dir)}


def _install_client_and_materialize_resourcepacks(
    paths: ProjectPaths,
    work_dir: Path,
    *,
    profile: dict[str, Any],
    game_version: str,
    expected_sources: list[str],
) -> tuple[list[str], dict[str, Any]]:
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
    target = discover_target_resource_format(paths.main_dir, game_version)
    source_manifest = load_resourcepack_sources(work_dir)
    if source_manifest.get("target_game_version") != game_version:
        raise RuntimeError("Resource-pack source manifest targets a different Minecraft version")
    sources = list(source_manifest.get("resourcepacks", []))
    source_names = [source.get("filename") for source in sources if isinstance(source, dict)]
    if source_names != expected_sources or len(source_names) != len(sources):
        raise RuntimeError("Resource-pack source manifest does not match installed selection")
    effective_names, resolution = materialize_resourcepacks(
        work_dir,
        sources=sources,
        target=target,
        game_version=game_version,
    )
    (work_dir / ".mcdata" / "resourcepack-runtime.json").unlink(missing_ok=True)
    return effective_names, resolution


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
    server_port: int | None = None,
    lane: str | None = None,
    probe_interval: float = 5.0,
    debug_no_reapply: bool = False,
    debug_no_replay_gate: bool = False,
) -> dict[str, Any]:
    options = RunOptions(
        dry_run=dry_run,
        capture=capture,
        strategy=strategy,
        duration=duration,
        with_server=with_server,
        replay_actions=replay_actions,
        trajectory_path=trajectory_path,
        game_version=game_version,
        server_port=server_port,
        lane=lane,
        probe_interval=probe_interval,
        debug_no_reapply=debug_no_reapply,
        debug_no_replay_gate=debug_no_replay_gate,
    )
    paths = ProjectPaths.from_root(root)
    with _profile_instance_lock(paths, profile_name):
        plan = _plan_run(
            root,
            profile_name,
            options=options,
        )
        return _execute_run_plan(_refresh_run_resources(plan))


def _refresh_run_resources(plan: RunPlan) -> RunPlan:
    resources = _resource_manifest(plan.work_dir, plan.profile)
    resources["resourcepack_runtime"] = None
    _verify_resourcepack_manifest_consistency(resources)
    return replace(plan, resources=resources)


def _execute_run_plan(plan: RunPlan) -> dict[str, Any]:
    console.print("Launch command:")
    console.print(" ".join(shlex.quote(part) for part in plan.cmd))
    state = RunState()
    with RunLogger(plan.run_dir, console=console) as runlog:
        runlog.log(
            "launch",
            "command",
            cmd=plan.cmd,
            dry_run=plan.dry_run,
            game_version=plan.game_version,
            capture_settings=asdict(plan.capture_settings),
        )
        try:
            _server_phase(plan, state, runlog)
            _replay_phase(plan, state, runlog)
            _client_phase(plan, state, runlog)
        except Exception as exc:
            state.error = f"{type(exc).__name__}: {exc}"
            runlog.log("teardown", "error", error=state.error)
            raise
        finally:
            _teardown_phase(plan, state, runlog)
    return plan.metadata


def matrix_trajectory_path(paths: ProjectPaths, *, strategy: str, lane: str | None) -> Path:
    return paths.output_dir / "trajectories" / f"{strategy}_matrix_{lane or 'main'}.json"


def run_matrix_profiles(
    root: Path,
    profiles: list[str],
    *,
    strategy: str,
    duration: int,
    capture: bool,
    with_server: bool,
    replay_actions: bool,
    bootstrap: bool,
    trajectory_path: Path,
    game_version: str | None,
    server_port: int | None,
    lane: str | None,
    probe_interval: float,
) -> None:
    if not profiles:
        raise ValueError("At least one profile is required")
    paths = ProjectPaths.from_root(root)
    first_profile = load_profile(paths.configs, profiles[0])
    resolved_game_version = game_version or resolve_game_version(first_profile)
    console.print(f"Resolved matrix Minecraft version once: {resolved_game_version}")
    for name in profiles:
        console.print(f"Matrix profile: {name}")
        if bootstrap:
            bootstrap_profile(
                root,
                name,
                game_version=resolved_game_version,
                server_port=server_port,
                lane=lane,
            )
        launch_profile(
            root,
            name,
            dry_run=False,
            capture=capture,
            strategy=strategy,
            duration=duration,
            with_server=with_server,
            replay_actions=replay_actions,
            trajectory_path=trajectory_path,
            game_version=resolved_game_version,
            server_port=server_port,
            lane=lane,
            probe_interval=probe_interval,
        )


def _plan_run(
    root: Path,
    profile_name: str,
    *,
    options: RunOptions,
) -> RunPlan:
    paths = ProjectPaths.from_root(root)
    profile = _profile_with_overrides(
        _profile_with_scene(paths.configs, profile_name),
        server_port=options.server_port,
    )
    game_version = options.game_version or resolve_game_version(profile)
    work_dir = paths.instance_dir(profile_name)
    _bootstrap_missing_work_dir(root, profile_name, game_version, work_dir, options)

    run_dir = _run_dir(paths.output_dir, profile_name, lane=options.lane)
    started_at = datetime.now(timezone.utc).isoformat()
    capture_settings = CaptureSettings.from_env(profile)
    run_trajectory_path = (
        _copy_trajectory(run_dir, options.trajectory_path) if options.trajectory_path else None
    )
    trajectory_info = _trajectory_manifest(
        run_trajectory_path,
        source_path=options.trajectory_path,
        strategy=options.strategy,
    )
    metadata = _run_metadata(
        profile_name=profile_name,
        game_version=game_version,
        work_dir=work_dir,
        run_dir=run_dir,
        strategy=options.strategy,
        duration=options.duration,
        capture=options.capture,
        with_server=options.with_server,
        replay_actions=options.replay_actions,
        lane=options.lane,
        probe_interval=options.probe_interval,
        debug_no_reapply=options.debug_no_reapply,
        debug_no_replay_gate=options.debug_no_replay_gate,
        started_at=started_at,
    )
    _write_json(run_dir / "metadata.json", metadata)

    cmd = _launch_command(
        paths,
        work_dir,
        profile=profile,
        game_version=game_version,
        capture_settings=capture_settings,
        dry_run=options.dry_run,
        with_server=options.with_server,
    )
    return _make_run_plan(
        paths=paths,
        profile_name=profile_name,
        profile=profile,
        game_version=game_version,
        work_dir=work_dir,
        run_dir=run_dir,
        started_at=started_at,
        capture_settings=capture_settings,
        run_trajectory_path=run_trajectory_path,
        trajectory_info=trajectory_info,
        resources={},
        metadata=metadata,
        cmd=cmd,
        dry_run=options.dry_run,
        capture=options.capture,
        duration=options.duration,
        with_server=options.with_server,
        replay_actions=options.replay_actions,
        lane=options.lane,
        probe_interval=options.probe_interval,
        debug_no_reapply=options.debug_no_reapply,
        debug_no_replay_gate=options.debug_no_replay_gate,
    )


def _bootstrap_missing_work_dir(
    root: Path,
    profile_name: str,
    game_version: str,
    work_dir: Path,
    options: RunOptions,
) -> None:
    if work_dir.exists():
        return
    _bootstrap_profile_unlocked(
        root,
        profile_name,
        game_version=game_version,
        server_port=options.server_port,
        lane=options.lane,
    )


def _run_metadata(
    *,
    profile_name: str,
    game_version: str,
    work_dir: Path,
    run_dir: Path,
    strategy: str | None,
    duration: int | None,
    capture: bool,
    with_server: bool,
    replay_actions: bool,
    lane: str | None,
    probe_interval: float,
    debug_no_reapply: bool,
    debug_no_replay_gate: bool,
    started_at: str,
) -> dict[str, Any]:
    return {
        "profile": profile_name,
        "minecraft_version": game_version,
        "work_dir": str(work_dir),
        "run_dir": str(run_dir),
        "strategy": strategy,
        "duration": duration,
        "capture": capture,
        "with_server": with_server,
        "replay_actions": replay_actions,
        "lane": lane,
        "probe_interval": probe_interval,
        "debug_no_reapply": debug_no_reapply,
        "debug_no_replay_gate": debug_no_replay_gate,
        "started_at": started_at,
    }


def _make_run_plan(
    *,
    paths: ProjectPaths,
    profile_name: str,
    profile: dict[str, Any],
    game_version: str,
    work_dir: Path,
    run_dir: Path,
    started_at: str,
    capture_settings: CaptureSettings,
    run_trajectory_path: Path | None,
    trajectory_info: dict[str, Any] | None,
    resources: dict[str, Any],
    metadata: dict[str, Any],
    cmd: list[str],
    dry_run: bool,
    capture: bool,
    duration: int | None,
    with_server: bool,
    replay_actions: bool,
    lane: str | None,
    probe_interval: float,
    debug_no_reapply: bool,
    debug_no_replay_gate: bool,
) -> RunPlan:
    return RunPlan(
        paths=paths,
        profile_name=profile_name,
        profile=profile,
        game_version=game_version,
        work_dir=work_dir,
        run_dir=run_dir,
        started_at=started_at,
        capture_settings=capture_settings,
        run_trajectory_path=run_trajectory_path,
        trajectory_info=trajectory_info,
        resources=resources,
        metadata=metadata,
        cmd=cmd,
        dry_run=dry_run,
        capture=capture,
        duration=duration,
        with_server=with_server,
        replay_actions=replay_actions,
        lane=lane,
        probe_interval=probe_interval,
        debug_no_reapply=debug_no_reapply,
        debug_no_replay_gate=debug_no_replay_gate,
    )


def _launch_command(
    paths: ProjectPaths,
    work_dir: Path,
    *,
    profile: dict[str, Any],
    game_version: str,
    capture_settings: CaptureSettings,
    dry_run: bool,
    with_server: bool,
) -> list[str]:
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
    return cmd


def _server_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if not plan.with_server or plan.dry_run:
        return
    runlog.log("server", "start")
    state.server_proc = start_server(
        (plan.paths.root / str(plan.profile.get("server_dir", ".mcdata/servers"))).resolve(),
        plan.paths.main_dir,
        game_version=plan.game_version,
        profile_name=plan.profile_name,
        profile=plan.profile,
        run_dir=plan.run_dir,
        lane=plan.lane,
    )
    runlog.log("server", "started", pid=state.server_proc.pid)
    expected_fill_count = expected_scene_fill_count(plan.profile)
    if expected_fill_count:
        receipt_count = verify_scene_commands(
            plan.run_dir / "server.log",
            expected_fill_count=expected_fill_count,
        )
        runlog.log(
            "server",
            "scene_verified",
            expected_fill_count=expected_fill_count,
            receipt_count=receipt_count,
        )


def _replay_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if not plan.replay_actions or not plan.run_trajectory_path or plan.dry_run:
        return
    if _is_feedback_navigation(plan):
        if not plan.with_server or not plan.capture:
            raise RuntimeError("feedback_roam replay requires capture with a managed server")
        state.replay_thread = _start_navigation_thread(plan, state)
        runlog.log(
            "navigation",
            "thread_started",
            trajectory=str(plan.run_trajectory_path),
            controller="position_yaw_feedback",
        )
        return
    trajectory = _read_optional_json(plan.run_trajectory_path) or {}
    has_placement = any(
        event.get("semantic_action") == "deterministic_block_placement"
        for event in trajectory.get("events", [])
        if isinstance(event, dict)
    )
    has_combat = any(
        event.get("semantic_action") == "controlled_combat"
        for event in trajectory.get("events", [])
        if isinstance(event, dict)
    )
    if has_combat:
        if not plan.capture or not plan.with_server or state.server_proc is None:
            raise RuntimeError("L4 combat replay requires capture with a managed server")
        state.placement_executor = CombatExecutor(
            state.server_proc,
            server_log_path=plan.run_dir / "server.log",
            username=str(plan.profile.get("username")),
        )
    elif has_placement:
        if not plan.capture or not plan.with_server or state.server_proc is None:
            raise RuntimeError("L3 block placement replay requires capture with a managed server")
        state.placement_executor = PlacementExecutor(
            state.server_proc,
            server_log_path=plan.run_dir / "server.log",
            username=str(plan.profile.get("username")),
        )
    state.replay_thread = _start_replay_thread(plan, state)
    runlog.log("replay", "thread_started", trajectory=str(plan.run_trajectory_path))


def _client_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if plan.dry_run:
        runlog.log("launch", "dry_run_start")
        subprocess.run(plan.cmd, cwd=plan.paths.root, check=True)
        runlog.log("launch", "dry_run_complete")
        return
    state.game_proc = subprocess.Popen(plan.cmd, cwd=plan.paths.root, start_new_session=True)
    runlog.log("launch", "process_started", pid=state.game_proc.pid)
    _join_phase(plan, state, runlog)
    if plan.capture:
        _capture_phase(plan, state, runlog)
    else:
        _release_replay(state, runlog)
        _wait_for_game(state.game_proc, plan.run_dir / "game.exitcode", duration=plan.duration)
        runlog.log("launch", "process_exit", returncode=state.game_proc.returncode)


def _join_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if not (plan.with_server and state.server_proc and (plan.capture or plan.replay_actions)):
        return
    if state.game_proc is None:
        raise RuntimeError("Minecraft client process was not started")
    username = str(plan.profile.get("username"))
    runlog.log("join", "wait_start", username=username)
    wait_for_player_join(
        plan.run_dir / "server.log",
        username,
        proc=state.server_proc,
        wait_sec=int(plan.profile.get("join_timeout_sec", 180)),
    )
    runlog.log("join", "player_joined", username=username)
    apply_join_state(state.server_proc, plan.profile)
    runlog.log("join", "apply_join_state")
    _warmup_after_join(plan, state, runlog)
    _verify_joined_resourcepacks(plan, state, runlog)
    if plan.capture:
        _prepare_joined_capture(plan, state, runlog)


def _warmup_after_join(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if plan.capture_settings.ready_delay_sec <= 0:
        return
    if state.game_proc is None:
        raise RuntimeError("Minecraft client process was not started")
    console.print(
        "Player joined; waiting "
        f"{plan.capture_settings.ready_delay_sec:.1f}s before capture/actions..."
    )
    runlog.log("warmup", "start", seconds=plan.capture_settings.ready_delay_sec)
    _wait_or_raise_if_exited(state.game_proc, plan.capture_settings.ready_delay_sec)
    runlog.log("warmup", "end")


def _verify_joined_resourcepacks(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    runtime_path = plan.work_dir / ".mcdata" / "resourcepack-runtime.json"
    resolution = plan.resources["resourcepack_resolution"]
    expected_filenames = [pack["filename"] for pack in resolution["packs"]]
    try:
        record = validate_resourcepack_runtime(
            plan.work_dir,
            expected_filenames=expected_filenames,
            timeout_sec=10.0,
        )
    except ResourcePackRuntimeError as exc:
        state.resourcepack_runtime = exc.record.to_dict()
        _write_json(runtime_path, state.resourcepack_runtime)
        runlog.log("resourcepacks", "runtime_fail", **state.resourcepack_runtime)
        raise
    state.resourcepack_runtime = record.to_dict()
    _write_json(runtime_path, state.resourcepack_runtime)
    runlog.log("resourcepacks", "runtime_pass", **state.resourcepack_runtime)


def _prepare_joined_capture(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if state.server_proc is None or state.game_proc is None:
        raise RuntimeError("Capture prep requires server and client processes")
    if plan.debug_no_reapply:
        runlog.log("join", "re_apply_state_skipped", debug=True)
    else:
        apply_join_state(state.server_proc, plan.profile)
        runlog.log("join", "re_apply_state")
        _wait_or_raise_if_exited(state.game_proc, 1.0)
    _prepare_placement_actions(plan, state, runlog)
    _prepare_capture_view(plan.capture_settings)
    runlog.log("capture", "view_prepared")


def _capture_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if state.game_proc is None:
        raise RuntimeError("Capture requires a Minecraft client process")
    _log_window_geometry(plan, runlog)
    state.capture_proc, state.capture_cmd = _start_capture(
        plan.run_dir,
        settings=plan.capture_settings,
        duration=plan.duration,
    )
    runlog.log("capture", "start", cmd=state.capture_cmd, pid=state.capture_proc.pid)
    if plan.with_server and state.server_proc:
        _start_position_probe_phase(plan, state, runlog)
    _release_replay(state, runlog)
    _wait_for_capture(
        state.game_proc,
        state.capture_proc,
        plan.run_dir / "game.exitcode",
        stop_event=state.replay_stop,
        worker_error=lambda: state.worker_error,
        post_capture=(
            (lambda: _finalize_placement_actions(plan, state, runlog))
            if state.placement_executor is not None
            else None
        ),
    )
    runlog.log("capture", "stop", returncode=state.capture_proc.returncode)
    if state.position_probe_stop:
        state.position_probe_stop.set()
        runlog.log("position_probe", "stop")


def _log_window_geometry(plan: RunPlan, runlog: RunLogger) -> None:
    geometry_record = _window_geometry_record(
        requested_width=plan.capture_settings.width,
        requested_height=plan.capture_settings.height,
    )
    runlog.log(
        "capture",
        "window_geometry",
        **geometry_record,
        requested_width=plan.capture_settings.width,
        requested_height=plan.capture_settings.height,
        desktop=plan.capture_settings.desktop,
    )


def _start_position_probe_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if state.server_proc is None:
        return
    username = str(plan.profile.get("username"))
    interval = _effective_probe_interval(plan)
    state.position_probe_stop = start_position_probe(
        state.server_proc,
        username,
        interval_sec=interval,
        sent_at=state.position_probe_sent_at,
    )
    runlog.log(
        "position_probe",
        "start",
        interval_sec=interval,
        requested_interval_sec=plan.probe_interval,
    )
    if plan.debug_no_replay_gate:
        runlog.log("position_probe", "first_sample_skipped", debug=True)
        return
    count = wait_for_position_sample(
        plan.run_dir / "server.log",
        username,
        proc=state.server_proc,
        after_count=state.position_probe_baseline,
        wait_sec=10.0,
    )
    runlog.log("position_probe", "first_sample", count=count)


def _release_replay(state: RunState, runlog: RunLogger) -> None:
    state.replay_start_mono = time.monotonic()
    state.ready_event.set()
    runlog.log("replay", "released")


def _teardown_phase(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    state.replay_stop.set()
    state.ready_event.set()
    if state.position_probe_stop:
        state.position_probe_stop.set()
    worker_failure = _join_replay_worker(state, runlog)
    if state.worker_error is not None:
        worker_failure = (
            f"action/navigation worker failed: "
            f"{type(state.worker_error).__name__}: {state.worker_error}"
        )
    cleanup_failure = _cleanup_rejected_placement_actions(state, runlog)
    if cleanup_failure and not worker_failure:
        worker_failure = cleanup_failure
    _terminate_running_processes(state, runlog)
    _write_position_log(plan, state, runlog)
    raise_worker_failure = bool(worker_failure and state.error is None)
    if worker_failure and state.error is None:
        state.error = worker_failure
    action_failure = _write_run_manifest_for_plan(plan, state, runlog)
    if raise_worker_failure:
        raise RuntimeError(worker_failure)
    if action_failure:
        raise RuntimeError(action_failure)


def _join_replay_worker(state: RunState, runlog: RunLogger) -> str | None:
    if state.replay_thread is None:
        return None
    state.replay_thread.join(timeout=5)
    alive = state.replay_thread.is_alive()
    runlog.log("replay", "thread_joined", alive=alive)
    if alive:
        return "action/navigation worker remained alive after 5s teardown timeout"
    return None


def _cleanup_rejected_placement_actions(
    state: RunState,
    runlog: RunLogger,
) -> str | None:
    if state.placement_executor is None or state.placement_finalized:
        return None
    try:
        evidence = state.placement_executor.cleanup_after_failure()
    except Exception as exc:
        message = f"L3 rejected-run cleanup failed: {type(exc).__name__}: {exc}"
        runlog.log("placement", "rejected_cleanup_failed", error=message)
        return message
    runlog.log(
        "placement",
        "rejected_cleanup_verified",
        action_ids=evidence["action_ids"],
        cleanup_command_count=evidence["cleanup_command_count"],
        receipt_count=_semantic_receipt_count(evidence),
    )
    return None


def _terminate_running_processes(state: RunState, runlog: RunLogger) -> None:
    _terminate_if_running(
        state.capture_proc,
        timeout=10,
        runlog=runlog,
        event="capture_terminated",
    )
    _terminate_if_running(
        state.game_proc,
        timeout=20,
        runlog=runlog,
        event="game_terminated",
    )
    _terminate_if_running(
        state.server_proc,
        timeout=20,
        runlog=runlog,
        event="server_terminated",
    )


def _terminate_if_running(
    proc: subprocess.Popen | None,
    *,
    timeout: int,
    runlog: RunLogger,
    event: str,
) -> None:
    if proc and proc.poll() is None:
        _terminate_process_tree(proc, timeout=timeout)
        runlog.log("teardown", event, returncode=proc.returncode)


def _write_position_log(plan: RunPlan, state: RunState, runlog: RunLogger) -> None:
    if not state.server_proc:
        return
    count = write_positions_jsonl(
        plan.run_dir / "server.log",
        plan.run_dir / "positions.jsonl",
        username=str(plan.profile.get("username")),
        sent_at=state.position_probe_sent_at,
        replay_start_mono=state.replay_start_mono,
        replay_log_path=_action_timing_log(plan),
    )
    runlog.log("position_probe", "positions_written", count=count)


def _write_run_manifest_for_plan(
    plan: RunPlan, state: RunState, runlog: RunLogger
) -> str | None:
    resources = dict(plan.resources)
    resources["resourcepack_runtime"] = state.resourcepack_runtime
    action_curriculum, action_error = _action_curriculum_for_plan(plan)
    raise_action_failure = bool(action_error and state.error is None)
    if action_error and state.error is None:
        state.error = f"action curriculum validation failed: {action_error}"
    manifest = build_run_manifest(
        run_id=plan.run_dir.name,
        profile_name=plan.profile_name,
        profile=plan.profile,
        mc_version=plan.game_version,
        resources=resources,
        trajectory=plan.trajectory_info,
        action_curriculum=action_curriculum,
        capture=_capture_manifest(
            enabled=plan.capture,
            settings=plan.capture_settings,
            ffmpeg_cmd=state.capture_cmd,
            run_dir=plan.run_dir,
        ),
        env=_env_manifest(display=plan.capture_settings.display),
        git=_git_manifest(plan.paths.root),
        started_at=plan.started_at,
        ended_at=datetime.now(timezone.utc).isoformat(),
        error=state.error,
        lane=plan.lane,
    )
    write_run_manifest(plan.run_dir, manifest)
    runlog.log("teardown", "manifest_written", path=str(plan.run_dir / "manifest.json"))
    return state.error if raise_action_failure else None


def _action_curriculum_for_plan(
    plan: RunPlan,
) -> tuple[dict[str, Any] | None, str | None]:
    if plan.run_trajectory_path is None or plan.trajectory_info is None:
        return None, None
    evidence_path = _action_timing_log(plan)
    execution_mode = str(plan.trajectory_info.get("execution_mode"))
    try:
        summary = summarize_action_run(
            plan.run_trajectory_path,
            evidence_path,
            execution_mode=execution_mode,
            require_evidence=False,
        )
    except ActionCurriculumError as exc:
        summary = summarize_action_run(
            plan.run_trajectory_path,
            None,
            execution_mode=execution_mode,
            require_evidence=False,
        )
        return summary, str(exc)
    if not plan.replay_actions or plan.dry_run:
        return summary, None
    try:
        validate_action_summary(summary, require_evidence=True)
    except ActionCurriculumError as exc:
        return summary, str(exc)
    return summary, None


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


def _start_replay_thread(plan: RunPlan, state: RunState) -> threading.Thread:
    from mcdata.actions.replay import replay_trajectory

    if plan.run_trajectory_path is None:
        raise RuntimeError("action replay requires a trajectory")

    def run() -> None:
        try:
            start_event: threading.Event | None = state.ready_event
            if state.placement_executor is not None:
                while not state.ready_event.wait(0.25):
                    if state.replay_stop.is_set():
                        return
                if state.replay_stop.is_set():
                    return
                if state.placement_reset_evidence is None:
                    raise RuntimeError("L3 pre-capture reset evidence is missing")
                start_event = None
            replay_trajectory(
                plan.run_trajectory_path,
                start_event=start_event,
                stop_event=state.replay_stop,
                run_dir=plan.run_dir,
                semantic_executor=state.placement_executor,
                episode_reset_evidence=state.placement_reset_evidence,
            )
        except BaseException as exc:
            state.worker_error = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _prepare_placement_actions(
    plan: RunPlan,
    state: RunState,
    runlog: RunLogger,
) -> None:
    if state.placement_executor is None or plan.run_trajectory_path is None:
        return
    trajectory = _read_optional_json(plan.run_trajectory_path) or {}
    events = [event for event in trajectory.get("events", []) if isinstance(event, dict)]
    state.placement_reset_evidence = state.placement_executor.prepare(events)
    runlog.log(
        "placement",
        "pre_capture_reset_verified",
        action_ids=state.placement_reset_evidence["action_ids"],
        reset_command_count=state.placement_reset_evidence["reset_command_count"],
        receipt_count=_semantic_receipt_count(state.placement_reset_evidence),
    )


def _finalize_placement_actions(
    plan: RunPlan,
    state: RunState,
    runlog: RunLogger,
) -> None:
    if state.placement_executor is None or state.placement_finalized:
        return
    if state.replay_thread is not None:
        state.replay_thread.join(timeout=5)
        if state.replay_thread.is_alive():
            raise RuntimeError("L3 replay worker remained alive after capture")
    if state.worker_error is not None:
        raise RuntimeError(
            f"L3 input worker failed: {type(state.worker_error).__name__}: {state.worker_error}"
        ) from state.worker_error
    evidence = state.placement_executor.finalize()
    from mcdata.actions.replay import append_replay_control

    append_replay_control(
        plan.run_dir / "replay_log.jsonl",
        getattr(
            state.placement_executor,
            "post_capture_control",
            "l3_post_capture_verification",
        ),
        semantic_evidence=evidence,
    )
    state.placement_finalized = True
    runlog.log(
        "placement",
        "post_capture_verified_and_cleaned",
        action_ids=evidence["action_ids"],
        cleanup_command_count=evidence["cleanup_command_count"],
        receipt_count=_semantic_receipt_count(evidence),
    )


def _semantic_receipt_count(evidence: dict[str, Any]) -> int:
    if "receipt_count" in evidence:
        return int(evidence["receipt_count"])
    if isinstance(evidence.get("receipts"), list):
        return len(evidence["receipts"])
    if isinstance(evidence.get("placements"), list):
        return sum(
            len(item.get("receipts", []))
            for item in evidence["placements"]
            if isinstance(item, dict)
        )
    return sum(
        _semantic_receipt_count(value)
        for key in ("placement", "combat")
        for value in (evidence.get(key),)
        if isinstance(value, dict)
    )


def _start_navigation_thread(plan: RunPlan, state: RunState) -> threading.Thread:
    from mcdata.render.navigation import run_feedback_navigation

    if plan.run_trajectory_path is None:
        raise RuntimeError("feedback navigation requires a trajectory")

    def run() -> None:
        try:
            run_feedback_navigation(
                plan.run_trajectory_path,
                server_log_path=plan.run_dir / "server.log",
                username=str(plan.profile.get("username")),
                start_event=state.ready_event,
                stop_event=state.replay_stop,
                run_dir=plan.run_dir,
                position_query_sent_at=state.position_probe_sent_at,
            )
        except BaseException as exc:
            state.worker_error = exc

    thread = threading.Thread(target=run, daemon=True)
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
    ffmpeg_cmd += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        str(capture_file),
    ]
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
    if x < 0 or y < 0:
        console.print(f"Window is at {x},{y}; falling back to display capture {display}.")
        return display
    if window_width < width or window_height < height:
        console.print(
            f"Window is {window_width}x{window_height}; falling back to display capture {display}."
        )
        return display
    return f"{display}+{x},{y}"


def _window_geometry_record(
    window_name: str = "Minecraft",
    *,
    requested_width: int | None = None,
    requested_height: int | None = None,
) -> dict[str, object]:
    geometry = _minecraft_window_geometry(window_name=window_name)
    if geometry is None:
        return {"geometry": None, "warning": "unavailable"}
    x, y, width, height = geometry
    warnings: list[str] = []
    if x < 0 or y < 0:
        warnings.append("offscreen")
    if requested_width is not None and requested_height is not None:
        if width != requested_width or height != requested_height:
            warnings.append("size_mismatch")
    return {
        "geometry": {"x": x, "y": y, "width": width, "height": height},
        "warning": ",".join(warnings) if warnings else None,
    }


def _minecraft_window_geometry(window_name: str = "Minecraft") -> tuple[int, int, int, int] | None:
    geometry = _minecraft_window_geometry_xdotool(window_name)
    if geometry is not None:
        return geometry
    return _minecraft_window_geometry_xwininfo(window_name)


def _minecraft_window_geometry_xdotool(window_name: str) -> tuple[int, int, int, int] | None:
    if not shutil.which("xdotool"):
        return None
    try:
        search = subprocess.run(
            ["xdotool", "search", "--name", window_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    if search.returncode != 0:
        return None
    candidates: list[tuple[str, tuple[int, int, int, int]]] = []
    for window_id in search.stdout.split():
        geometry = _xdotool_window_geometry(window_id)
        if geometry is not None:
            candidates.append((window_id, geometry))
    if not candidates:
        return None
    window_id, geometry = max(candidates, key=lambda item: item[1][2] * item[1][3])
    if geometry[0] < 0 or geometry[1] < 0:
        moved_geometry = _move_xdotool_window_onscreen(window_id)
        if moved_geometry is not None:
            return moved_geometry
    return geometry


def _move_xdotool_window_onscreen(window_id: str) -> tuple[int, int, int, int] | None:
    try:
        result = subprocess.run(
            ["xdotool", "windowmove", window_id, "0", "0"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return _xdotool_window_geometry(window_id)


def _xdotool_window_geometry(window_id: str) -> tuple[int, int, int, int] | None:
    try:
        result = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", window_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    values: dict[str, int] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in {"X", "Y", "WIDTH", "HEIGHT"}:
            continue
        try:
            values[key] = int(value)
        except ValueError:
            return None
    if {"X", "Y", "WIDTH", "HEIGHT"} <= values.keys():
        return values["X"], values["Y"], values["WIDTH"], values["HEIGHT"]
    return None


def _minecraft_window_geometry_xwininfo(window_name: str) -> tuple[int, int, int, int] | None:
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


def _wait_for_capture(
    game_proc: subprocess.Popen,
    capture_proc: subprocess.Popen,
    exitcode_file: Path,
    *,
    stop_event: threading.Event | None = None,
    worker_error: Callable[[], BaseException | None] | None = None,
    post_capture: Callable[[], None] | None = None,
) -> None:
    while capture_proc.poll() is None:
        failure = worker_error() if worker_error else None
        if failure is not None:
            if stop_event:
                stop_event.set()
            capture_proc.terminate()
            capture_proc.wait(timeout=10)
            raise RuntimeError(
                f"capture worker failed: {type(failure).__name__}: {failure}"
            ) from failure
        if game_proc.poll() is not None:
            if stop_event:
                stop_event.set()
            capture_proc.terminate()
            capture_proc.wait(timeout=10)
            _write_exitcode(exitcode_file, game_proc.returncode)
            if game_proc.returncode:
                raise subprocess.CalledProcessError(game_proc.returncode, game_proc.args)
            if post_capture is not None:
                raise RuntimeError(
                    "Minecraft client exited before L3 post-capture verification"
                )
            return
        time.sleep(0.5)
    if stop_event:
        stop_event.set()
    failure = worker_error() if worker_error else None
    if failure is not None:
        raise RuntimeError(
            f"capture worker failed: {type(failure).__name__}: {failure}"
        ) from failure
    if capture_proc.returncode:
        raise subprocess.CalledProcessError(capture_proc.returncode, capture_proc.args)
    if game_proc.poll() is None:
        try:
            if post_capture is not None:
                post_capture()
        finally:
            _terminate_process_tree(game_proc, timeout=20)
            _write_exitcode(exitcode_file, 0)
    else:
        _write_exitcode(exitcode_file, game_proc.returncode)
        if post_capture is not None:
            raise RuntimeError("Minecraft client exited before L3 post-capture verification")


def _wait_for_game(
    game_proc: subprocess.Popen, exitcode_file: Path, *, duration: int | None
) -> None:
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
    planned_action_contract(data)
    route = data.get("route", [])
    return {
        "strategy": strategy,
        "path": str(trajectory_path),
        "source_path": str(source_path) if source_path else None,
        "sha256": _sha256(trajectory_path),
        "event_count": len(data.get("events", [])),
        "duration_sec": data.get("duration_sec"),
        "type": data.get("type"),
        "execution_mode": (
            "online_position_yaw_feedback"
            if data.get("type") == "feedback_roam"
            else "open_loop_event_replay"
        ),
        "route_point_count": len(route) if isinstance(route, list) else 0,
    }


def _is_feedback_navigation(plan: RunPlan) -> bool:
    return bool(plan.trajectory_info and plan.trajectory_info.get("type") == "feedback_roam")


def _effective_probe_interval(plan: RunPlan) -> float:
    if not _is_feedback_navigation(plan) or plan.run_trajectory_path is None:
        return plan.probe_interval
    data = json.loads(plan.run_trajectory_path.read_text(encoding="utf-8"))
    navigation = data.get("navigation", {})
    control_interval = float(navigation.get("control_interval_sec", plan.probe_interval))
    if control_interval <= 0:
        raise RuntimeError("feedback_roam control_interval_sec must be positive")
    return min(plan.probe_interval, control_interval)


def _action_timing_log(plan: RunPlan) -> Path | None:
    if not plan.replay_actions:
        return None
    filename = "navigation_log.jsonl" if _is_feedback_navigation(plan) else "replay_log.jsonl"
    return plan.run_dir / filename


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
        "resourcepack_resolution": _read_optional_json(
            work_dir / ".mcdata" / "resourcepack-resolution.json"
        ),
        "resourcepack_runtime": _read_optional_json(
            work_dir / ".mcdata" / "resourcepack-runtime.json"
        ),
        "shaderpacks": _file_manifest_list(work_dir / "shaderpacks", suffixes={".zip"}),
    }


def _verify_resourcepack_manifest_consistency(resources: dict[str, Any]) -> None:
    resolution = resources.get("resourcepack_resolution")
    actual = resources.get("resourcepacks")
    if not isinstance(resolution, dict) or not isinstance(resolution.get("packs"), list):
        raise RuntimeError("Resource-pack resolution provenance is missing or invalid")
    if not isinstance(actual, list):
        raise RuntimeError("Effective resource-pack file manifest is invalid")

    expected_by_name: dict[str, str] = {}
    for pack in resolution["packs"]:
        if not isinstance(pack, dict):
            raise RuntimeError("Resource-pack resolution contains a non-object pack record")
        filename = pack.get("filename")
        effective_sha256 = pack.get("effective_sha256")
        if not isinstance(filename, str) or not isinstance(effective_sha256, str):
            raise RuntimeError("Resource-pack resolution record lacks filename/effective SHA-256")
        if filename in expected_by_name:
            raise RuntimeError(f"Duplicate resource-pack resolution record: {filename}")
        expected_by_name[filename] = effective_sha256

    actual_by_name: dict[str, str] = {}
    for pack in actual:
        if not isinstance(pack, dict):
            raise RuntimeError("Effective resource-pack manifest contains a non-object record")
        filename = pack.get("filename")
        sha256 = pack.get("sha256")
        if not isinstance(filename, str) or not isinstance(sha256, str):
            raise RuntimeError("Effective resource-pack manifest record lacks filename/SHA-256")
        if filename in actual_by_name:
            raise RuntimeError(f"Duplicate effective resource-pack file: {filename}")
        actual_by_name[filename] = sha256

    if actual_by_name != expected_by_name:
        raise RuntimeError(
            "Effective resource-pack files do not match bootstrap resolution provenance"
        )


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return data


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
    source = "git" if commit is not None else None
    if commit is None:
        sync_commit = _sync_commit(root)
        if sync_commit:
            commit = sync_commit
            source = "sync_commit"
    return {
        "commit": commit,
        "dirty": bool(status),
        "source": source,
        "status_porcelain": status.splitlines() if status else [],
    }


def _sync_commit(root: Path) -> str | None:
    path = root / ".sync_commit"
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


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


def _profile_with_overrides(
    profile: dict[str, Any],
    *,
    server_port: int | None,
) -> dict[str, Any]:
    if server_port is None:
        return profile
    return {**profile, "server_port": int(server_port)}


def _profile_with_scene(config_dir: Path, profile_name: str) -> dict[str, Any]:
    profile = load_profile(config_dir, profile_name)
    scene_spec = load_scene(config_dir)
    world_state = dict(profile.get("world_state", {}) or {})
    scene = dict(world_state.get("scene", {}) or {})
    base_scene = scene_mapping(scene_spec)
    scene.setdefault("enabled", True)
    scene.setdefault("origin", base_scene["origin"])
    scene["entries"] = base_scene["entries"]
    world_state["scene"] = scene
    return {**profile, "world_state": world_state}


def _run_dir(output_dir: Path, profile: str, *, lane: str | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"{profile}__{lane}" if lane else profile
    path = output_dir / f"{stamp}_{suffix}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_manifest(work_dir: Path, manifest: dict[str, Any]) -> None:
    _write_json(work_dir / "mcdata_manifest.json", manifest)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
