from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mcdata.actions import generate_strategy
from mcdata.config import load_yaml
from mcdata.doctor import run_doctor
from mcdata.paths import ProjectPaths
from mcdata.render.pipeline import bootstrap_profile, launch_profile, remote_tmux_command

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def doctor() -> None:
    """Check local rendering/bootstrap capabilities."""
    run_doctor()


@app.command()
def bootstrap(
    profile: str = typer.Option("fabric_low", "--profile", "-p"),
    root: Path = typer.Option(Path("."), "--root"),
) -> None:
    """Create/update a Minecraft instance for a profile."""
    bootstrap_profile(root.resolve(), profile)


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
) -> None:
    """Launch Minecraft for a profile."""
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
) -> None:
    """Run the same trajectory/world through multiple render-quality profiles."""
    root = root.resolve()
    paths = ProjectPaths.from_root(root)
    names = [item.strip() for item in profiles.split(",") if item.strip()]
    trajectory_path = paths.output_dir / "trajectories" / f"{strategy}_matrix.json"
    generate_strategy(paths.configs, strategy, trajectory_path)
    console.print(f"Wrote shared trajectory: {trajectory_path}")
    for name in names:
        console.print(f"Matrix profile: {name}")
        if bootstrap:
            bootstrap_profile(root, name)
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
        )


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
