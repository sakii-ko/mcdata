from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mcdata.actions import generate_strategy
from mcdata.actions.viz import load_trajectory, render_trajectory_map
from mcdata.config import load_yaml
from mcdata.dataset import DatasetValidationError, collect_runtime_logs, write_dataset_index
from mcdata.doctor import run_doctor
from mcdata.paths import ProjectPaths
from mcdata.qa.report import write_compare_report, write_run_report
from mcdata.render.pipeline import (
    bootstrap_profile,
    launch_profile,
    matrix_trajectory_path,
    remote_tmux_command,
    run_matrix_profiles,
)
from mcdata.scene_model import load_scene, walk_obstacles
from mcdata.settings import apply_display_override

app = typer.Typer(no_args_is_help=True)
console = Console()


def _hidden_positive_float(value: object, default: float, option_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if result <= 0:
        raise typer.BadParameter(f"{option_name} must be greater than 0")
    return result


@app.command()
def doctor() -> None:
    """Check local rendering/bootstrap capabilities."""
    run_doctor()


@app.command()
def bootstrap(
    profile: str = typer.Option("fabric_low", "--profile", "-p"),
    root: Path = typer.Option(Path("."), "--root"),
    game_version: Optional[str] = typer.Option(None, "--game-version"),
) -> None:
    """Create/update a Minecraft instance for a profile."""
    bootstrap_profile(root.resolve(), profile, game_version=game_version)


@app.command()
def run(
    profile: str = typer.Option("fabric_low", "--profile", "-p"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    capture: bool = typer.Option(False, "--capture"),
    strategy: Optional[str] = typer.Option(None, "--strategy"),
    duration: Optional[int] = typer.Option(None, "--duration"),
    with_server: bool = typer.Option(False, "--with-server"),
    replay_actions: bool = typer.Option(False, "--replay-actions"),
    display: Optional[str] = typer.Option(None, "--display"),
    server_port: Optional[int] = typer.Option(None, "--server-port"),
    lane: Optional[str] = typer.Option(None, "--lane"),
    game_version: Optional[str] = typer.Option(None, "--game-version"),
    probe_interval: float = typer.Option(5.0, "--probe-interval", hidden=True),
    debug_no_reapply: bool = typer.Option(False, "--debug-no-reapply", hidden=True),
    debug_no_replay_gate: bool = typer.Option(False, "--debug-no-replay-gate", hidden=True),
) -> None:
    """Launch Minecraft for a profile."""
    debug_no_reapply = debug_no_reapply if isinstance(debug_no_reapply, bool) else False
    debug_no_replay_gate = debug_no_replay_gate if isinstance(debug_no_replay_gate, bool) else False
    probe_interval = _hidden_positive_float(probe_interval, 5.0, "--probe-interval")
    if display:
        apply_display_override(display)
    root = root.resolve()
    paths = ProjectPaths.from_root(root)
    trajectory_path: Path | None = None
    if strategy:
        out = paths.output_dir / "trajectories" / f"{strategy}.json"
        generate_strategy(paths.configs, strategy, out)
        trajectory_path = out
        console.print(f"Wrote trajectory: {out}")
    launch_profile(
        root,
        profile,
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


@app.command("make-trajectory")
def make_trajectory(
    strategy: str = typer.Argument(...),
    out: Path = typer.Option(Path("runs/trajectory.json"), "--out"),
    root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Generate an action trajectory JSON file."""
    trajectory = generate_strategy(root.resolve() / "configs", strategy, out)
    console.print(f"Wrote {len(trajectory.get('events', []))} events to {out}")


@app.command("viz-trajectory")
def viz_trajectory(
    traj: Path = typer.Argument(...),
    out: Path = typer.Option(..., "--out"),
    spec_strategy: Optional[str] = typer.Option(None, "--spec-strategy"),
    root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Render a top-down trajectory map."""
    spec = None
    if spec_strategy:
        config_dir = root.resolve() / "configs"
        strategies = load_yaml(config_dir / "actions.yml").get("strategies", {})
        if spec_strategy not in strategies:
            known = ", ".join(sorted(strategies))
            raise typer.BadParameter(f"Unknown strategy '{spec_strategy}'. Known strategies: {known}")
        spec = dict(strategies[spec_strategy])
        spec["_scene_obstacles"] = sorted(walk_obstacles(load_scene(config_dir)))
    render_trajectory_map(load_trajectory(traj), spec=spec, out=out)
    console.print(f"Wrote trajectory map: {out}")


@app.command("run-matrix")
def run_matrix(
    profiles: str = typer.Option(
        "matrix_low,matrix_textured,matrix_shader_high",
        "--profiles",
        help="Comma-separated profile names that share one action/world setup.",
    ),
    root: Path = typer.Option(Path("."), "--root"),
    strategy: str = typer.Option("ground_astar_loop", "--strategy"),
    duration: int = typer.Option(60, "--duration"),
    capture: bool = typer.Option(True, "--capture/--no-capture"),
    with_server: bool = typer.Option(True, "--with-server/--no-server"),
    replay_actions: bool = typer.Option(True, "--replay-actions/--no-replay-actions"),
    bootstrap: bool = typer.Option(True, "--bootstrap/--no-bootstrap"),
    display: Optional[str] = typer.Option(None, "--display"),
    server_port: Optional[int] = typer.Option(None, "--server-port"),
    lane: Optional[str] = typer.Option(None, "--lane"),
    probe_interval: float = typer.Option(5.0, "--probe-interval", hidden=True),
    game_version: Optional[str] = typer.Option(None, "--game-version"),
) -> None:
    """Run the same trajectory/world through multiple render-quality profiles."""
    probe_interval = _hidden_positive_float(probe_interval, 5.0, "--probe-interval")
    if display:
        apply_display_override(display)
    root = root.resolve()
    paths = ProjectPaths.from_root(root)
    names = [item.strip() for item in profiles.split(",") if item.strip()]
    if not names:
        raise typer.BadParameter("At least one profile is required")
    trajectory_path = matrix_trajectory_path(paths, strategy=strategy, lane=lane)
    generate_strategy(paths.configs, strategy, trajectory_path)
    console.print(f"Wrote shared trajectory: {trajectory_path}")
    run_matrix_profiles(
        root,
        names,
        strategy=strategy,
        duration=duration,
        capture=capture,
        with_server=with_server,
        replay_actions=replay_actions,
        bootstrap=bootstrap,
        trajectory_path=trajectory_path,
        game_version=game_version,
        server_port=server_port,
        lane=lane,
        probe_interval=probe_interval,
    )


@app.command("qa-run")
def qa_run(
    input_path: Path = typer.Argument(...),
    frames: int = typer.Option(12, "--frames"),
    out_dir: Optional[Path] = typer.Option(None, "--out-dir"),
    border_mean_threshold: float = typer.Option(6.0, "--border-mean-threshold"),
    border_var_threshold: float = typer.Option(8.0, "--border-var-threshold"),
) -> None:
    """Generate offline QA report for a run dir or video file."""
    report = write_run_report(
        input_path,
        frames=frames,
        out_dir=out_dir,
        border_mean_threshold=border_mean_threshold,
        border_var_threshold=border_var_threshold,
    )
    console.print(f"Wrote QA report: {report['outputs']['markdown']}")


@app.command("qa-compare")
def qa_compare(
    inputs: list[Path] = typer.Argument(...),
    frames: int = typer.Option(12, "--frames"),
    out_dir: Path = typer.Option(Path("qa_compare"), "--out-dir"),
) -> None:
    """Compare aligned frames across two or more run dirs/videos."""
    report = write_compare_report(inputs, frames=frames, out_dir=out_dir)
    console.print(f"Wrote QA compare report: {report['outputs']['markdown']}")


@app.command("dataset-index")
def dataset_index(
    dataset_root: Path = typer.Argument(...),
    expected_profiles: str = typer.Option(..., "--expected-profiles"),
    primary_profile: str = typer.Option(..., "--primary-profile"),
    generator_commit: str = typer.Option(..., "--generator-commit"),
    strict_compare_report: Path = typer.Option(..., "--strict-compare-report"),
    diagnostic_compare_report: Optional[Path] = typer.Option(
        None, "--diagnostic-compare-report"
    ),
    visual_review: Optional[Path] = typer.Option(None, "--visual-review"),
    expected_width: int = typer.Option(1280, "--expected-width"),
    expected_height: int = typer.Option(720, "--expected-height"),
    expected_fps: float = typer.Option(24.0, "--expected-fps"),
    expected_duration: float = typer.Option(60.0, "--expected-duration"),
) -> None:
    """Build a fail-closed, checksummed index for an accepted run collection."""
    profiles = [item.strip() for item in expected_profiles.split(",") if item.strip()]
    if not profiles:
        raise typer.BadParameter("--expected-profiles must contain at least one profile")
    diagnostic_reports = [diagnostic_compare_report] if diagnostic_compare_report else []
    try:
        index = write_dataset_index(
            dataset_root,
            expected_profiles=profiles,
            primary_profile=primary_profile,
            generator_commit=generator_commit,
            strict_compare_report=strict_compare_report,
            diagnostic_compare_reports=diagnostic_reports,
            visual_review=visual_review,
            expected_width=expected_width,
            expected_height=expected_height,
            expected_fps=expected_fps,
            expected_duration=expected_duration,
        )
    except DatasetValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(
        f"Wrote dataset index: {dataset_root.resolve() / 'dataset_index.json'} "
        f"({index['status']}, {index['dataset_id']})"
    )


@app.command("dataset-collect-logs")
def dataset_collect_logs(
    dataset_root: Path = typer.Argument(...),
    expected_profiles: str = typer.Option(..., "--expected-profiles"),
) -> None:
    """Copy per-profile client logs into run dirs for portable runtime auditing."""
    profiles = [item.strip() for item in expected_profiles.split(",") if item.strip()]
    if not profiles:
        raise typer.BadParameter("--expected-profiles must contain at least one profile")
    try:
        outputs = collect_runtime_logs(dataset_root, expected_profiles=profiles)
    except DatasetValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"Collected {len(outputs)} client runtime logs under {dataset_root.resolve()}")


@app.command("remote-command")
def remote_command(
    host: str = typer.Option("rtx4090", "--host"),
    profile: str = typer.Option("fabric_low", "--profile", "-p"),
    config: Path = typer.Option(Path("configs/hosts.yml.example"), "--config"),
    capture: bool = typer.Option(False, "--capture"),
    strategy: str = typer.Option("idle_pan", "--strategy"),
    duration: int = typer.Option(60, "--duration"),
    with_server: bool = typer.Option(True, "--with-server/--no-server"),
    replay_actions: bool = typer.Option(True, "--replay-actions/--no-replay-actions"),
) -> None:
    """Print a tmux command for persistent remote rendering."""
    data = load_yaml(config)
    hosts = data.get("hosts", {})
    if host not in hosts:
        known = ", ".join(sorted(hosts))
        raise typer.BadParameter(f"Unknown host '{host}'. Known hosts: {known}")
    spec = hosts[host]
    cmd = remote_tmux_command(
        project_dir=str(spec["project_dir"]),
        profile=profile,
        session=str(spec.get("tmux_session", "mcdata-render")),
        display=str(spec.get("display", ":0")),
        capture=capture,
        strategy=strategy,
        duration=duration,
        with_server=with_server,
        replay_actions=replay_actions,
    )
    ssh = spec.get("ssh")
    if ssh:
        console.print(f"ssh {ssh} {cmd!r}")
    else:
        console.print(cmd)


if __name__ == "__main__":
    app()
