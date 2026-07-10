from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def render_trajectory_map(
    trajectory: dict[str, Any],
    *,
    spec: dict[str, Any] | None,
    out: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    route = [(int(point["x"]), int(point["z"])) for point in trajectory.get("route", [])]
    fig, ax = plt.subplots(figsize=(7, 7), dpi=140)
    if spec:
        _draw_spec(ax, spec, rectangle_cls=Rectangle)
    if route:
        xs = [point[0] for point in route]
        zs = [point[1] for point in route]
        ax.plot(xs, zs, color="#1f77b4", linewidth=2.2, marker="o", markersize=2.4)
        ax.scatter([xs[0]], [zs[0]], color="#2ca02c", s=80, zorder=4, label="start")
        ax.scatter([xs[-1]], [zs[-1]], color="#d62728", s=80, zorder=4, label="end")
    goal_values = trajectory.get("goals") or (spec or {}).get("goals", [])
    goals = [_point(point) for point in goal_values]
    for idx, (x, z) in enumerate(goals, 1):
        ax.text(x + 0.2, z + 0.2, str(idx), fontsize=9, color="#111111", weight="bold")
    jump_indices = sorted(
        {
            index
            for event in trajectory.get("events", [])
            if isinstance(event, dict)
            and event.get("semantic_action") == "deliberate_jump"
            for index in (event.get("route_index"),)
            if isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < len(route)
        }
    )
    jump_points = [route[index] for index in jump_indices]
    if jump_points:
        ax.scatter(
            [point[0] for point in jump_points],
            [point[1] for point in jump_points],
            marker="^",
            color="#9467bd",
            edgecolor="#ffffff",
            s=90,
            zorder=5,
            label="deliberate jump",
        )
    _draw_placement_targets(ax, trajectory, route)
    _draw_combat_targets(ax, trajectory, route)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x")
    ax.set_ylabel("z")
    ax.grid(True, linewidth=0.5, alpha=0.35)
    ax.legend(loc="upper right")
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)


def load_trajectory(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _draw_placement_targets(
    ax: Any,
    trajectory: dict[str, Any],
    route: list[tuple[int, int]],
) -> None:
    placements = [
        event
        for event in trajectory.get("events", [])
        if isinstance(event, dict)
        and event.get("semantic_action") == "deterministic_block_placement"
        and isinstance(event.get("placement"), dict)
    ]
    targets = [
        (int(event["placement"]["target"][0]), int(event["placement"]["target"][2]))
        for event in placements
    ]
    if not targets:
        return
    ax.scatter(
        [point[0] for point in targets],
        [point[1] for point in targets],
        marker="P",
        color="#e6a700",
        edgecolor="#111111",
        s=105,
        zorder=6,
        label="verified block target",
    )
    for event, target in zip(placements, targets, strict=True):
        route_index = event.get("route_index")
        if isinstance(route_index, int) and 0 <= route_index < len(route):
            source = route[route_index]
            ax.plot(
                [source[0], target[0]],
                [source[1], target[1]],
                color="#e6a700",
                linewidth=1.2,
                linestyle=":",
                zorder=4,
            )


def _draw_combat_targets(
    ax: Any,
    trajectory: dict[str, Any],
    route: list[tuple[int, int]],
) -> None:
    combats = [
        event
        for event in trajectory.get("events", [])
        if isinstance(event, dict)
        and event.get("semantic_action") == "controlled_combat"
        and isinstance(event.get("combat"), dict)
    ]
    targets = [
        (float(event["combat"]["spawn"][0]), float(event["combat"]["spawn"][2]))
        for event in combats
    ]
    if not targets:
        return
    ax.scatter(
        [point[0] for point in targets],
        [point[1] for point in targets],
        marker="X",
        color="#b2182b",
        edgecolor="#ffffff",
        s=125,
        zorder=7,
        label="controlled combat target",
    )
    for event, target in zip(combats, targets, strict=True):
        route_index = event.get("route_index")
        if isinstance(route_index, int) and 0 <= route_index < len(route):
            source = route[route_index]
            ax.plot(
                [source[0], target[0]],
                [source[1], target[1]],
                color="#b2182b",
                linewidth=1.2,
                linestyle=":",
                zorder=4,
            )


def _draw_spec(ax: Any, spec: dict[str, Any], *, rectangle_cls: Any) -> None:
    bounds = spec.get("bounds")
    if bounds:
        min_x, max_x, min_z, max_z = [int(value) for value in bounds]
        ax.add_patch(
            rectangle_cls(
                (min_x, min_z),
                max_x - min_x,
                max_z - min_z,
                fill=False,
                edgecolor="#222222",
                linewidth=1.5,
            )
        )
        ax.set_xlim(min_x - 1, max_x + 1)
        ax.set_ylim(min_z - 1, max_z + 1)
    for rect in spec.get("blocked_rects", []) or []:
        min_x, min_z, max_x, max_z = [int(value) for value in rect]
        ax.add_patch(
            rectangle_cls(
                (min_x, min_z),
                max_x - min_x,
                max_z - min_z,
                facecolor="#ff7f0e",
                edgecolor="#ff7f0e",
                alpha=0.25,
            )
        )
    blocked = [_point(point) for point in spec.get("blocked", [])]
    if blocked:
        xs = [point[0] for point in blocked]
        zs = [point[1] for point in blocked]
        ax.scatter(xs, zs, marker="x", color="#d62728", s=55, label="blocked")
    scene_obstacles = [_point(point) for point in spec.get("_scene_obstacles", [])]
    if scene_obstacles:
        xs = [point[0] for point in scene_obstacles]
        zs = [point[1] for point in scene_obstacles]
        ax.scatter(
            xs,
            zs,
            marker="s",
            color="#d62728",
            alpha=0.18,
            s=48,
            linewidths=0,
            label="scene obstacle",
        )


def _point(value: Any) -> tuple[int, int]:
    if isinstance(value, dict):
        return int(value["x"]), int(value["z"])
    return int(value[0]), int(value[1])
