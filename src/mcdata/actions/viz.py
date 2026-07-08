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
    goals = [_point(point) for point in (spec or {}).get("goals", [])]
    for idx, (x, z) in enumerate(goals, 1):
        ax.text(x + 0.2, z + 0.2, str(idx), fontsize=9, color="#111111", weight="bold")
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


def _point(value: Any) -> tuple[int, int]:
    return int(value[0]), int(value[1])
