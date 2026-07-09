from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcdata.config import load_yaml

MAX_FILL_VOLUME = 32768
NON_SOLID_BLOCK_PREFIXES = (
    "minecraft:air",
    "minecraft:water",
)


@dataclass(frozen=True)
class SceneEntry:
    kind: str
    block: str | None
    start: tuple[int, int, int]
    end: tuple[int, int, int]
    region: str | None = None
    split_axis: str | None = None
    walk_obstacle: bool = False


@dataclass(frozen=True)
class SceneSpec:
    origin: tuple[int, int, int]
    entries: tuple[SceneEntry, ...]


def load_scene(config_dir: Path) -> SceneSpec:
    data = load_yaml(config_dir / "scene.yml")
    scene = data.get("scene", {})
    if not isinstance(scene, dict):
        raise ValueError("scene.yml must contain a scene mapping")
    return parse_scene(scene)


def parse_scene(scene: dict[str, Any]) -> SceneSpec:
    origin = _triple(scene.get("origin", [0, 64, 0]))
    entries = tuple(_parse_entry(item) for item in scene.get("entries", []) or [])
    return SceneSpec(origin=origin, entries=entries)


def scene_commands(spec: SceneSpec) -> list[str]:
    commands: list[str] = []
    for entry in spec.entries:
        commands.extend(_entry_commands(spec.origin, entry))
    return commands


def scene_mapping(spec: SceneSpec, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "origin": list(spec.origin),
        "entries": [_entry_mapping(entry) for entry in spec.entries],
    }


def walk_obstacles(spec: SceneSpec) -> set[tuple[int, int]]:
    obstacles: set[tuple[int, int]] = set()
    for entry in spec.entries:
        if entry.walk_obstacle:
            obstacles.update((x, z) for x, _y, z in _cells(entry.start, entry.end))
            continue
        if entry.block is None or _is_non_solid(entry.block):
            continue
        for _x, rel_y, _z in _cells(entry.start, entry.end):
            if rel_y in {0, 1}:
                obstacles.update((x, z) for x, _y, z in _cells(entry.start, entry.end))
                break
    return obstacles


def _parse_entry(data: dict[str, Any]) -> SceneEntry:
    kind = str(data.get("kind", ""))
    if kind == "setblock":
        at = _triple(data["at"])
        return SceneEntry(
            kind=kind,
            block=str(data["block"]),
            start=at,
            end=at,
            region=_optional_str(data.get("region")),
            walk_obstacle=bool(data.get("walk_obstacle", False)),
        )
    if kind == "fill":
        return SceneEntry(
            kind=kind,
            block=str(data["block"]),
            start=_triple(data["from"]),
            end=_triple(data["to"]),
            region=_optional_str(data.get("region")),
            split_axis=_optional_str(data.get("split_axis")),
            walk_obstacle=bool(data.get("walk_obstacle", False)),
        )
    if kind == "forceload":
        start = _triple(data["from"])
        end = _triple(data["to"])
        return SceneEntry(kind=kind, block=None, start=start, end=end)
    raise ValueError(f"Unsupported scene entry kind: {kind}")


def _entry_mapping(entry: SceneEntry) -> dict[str, Any]:
    data: dict[str, Any] = {"kind": entry.kind}
    if entry.kind == "setblock":
        data["at"] = list(entry.start)
    else:
        data["from"] = list(entry.start)
        data["to"] = list(entry.end)
    if entry.block is not None:
        data["block"] = entry.block
    if entry.region:
        data["region"] = entry.region
    if entry.split_axis:
        data["split_axis"] = entry.split_axis
    if entry.walk_obstacle:
        data["walk_obstacle"] = True
    return data


def _entry_commands(origin: tuple[int, int, int], entry: SceneEntry) -> list[str]:
    if entry.kind == "setblock":
        x, y, z = _abs_point(origin, entry.start)
        return [f"setblock {x} {y} {z} {entry.block}"]
    if entry.kind == "forceload":
        start = _abs_point(origin, entry.start)
        end = _abs_point(origin, entry.end)
        return [f"forceload add {start[0]} {start[2]} {end[0]} {end[2]}"]
    if entry.kind == "fill":
        return [
            _fill_command(origin, start, end, str(entry.block))
            for start, end in _split_fill(entry)
        ]
    raise ValueError(f"Unsupported scene entry kind: {entry.kind}")


def _split_fill(entry: SceneEntry) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    if _volume(entry.start, entry.end) <= MAX_FILL_VOLUME:
        return [(entry.start, entry.end)]
    if entry.split_axis not in {None, "y"}:
        raise ValueError(f"Unsupported fill split axis: {entry.split_axis}")
    return _split_fill_y(entry.start, entry.end)


def _split_fill_y(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    x0, y0, z0 = _mins(start, end)
    x1, y1, z1 = _maxs(start, end)
    layer_area = (x1 - x0 + 1) * (z1 - z0 + 1)
    if layer_area > MAX_FILL_VOLUME:
        raise ValueError("fill layer exceeds command block limit; split on x/z first")
    max_layers = max(1, MAX_FILL_VOLUME // layer_area)
    chunks = []
    y = y0
    while y <= y1:
        chunk_y1 = min(y1, y + max_layers - 1)
        chunks.append(((x0, y, z0), (x1, chunk_y1, z1)))
        y = chunk_y1 + 1
    return chunks


def _fill_command(
    origin: tuple[int, int, int],
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    block: str,
) -> str:
    sx, sy, sz = _abs_point(origin, start)
    ex, ey, ez = _abs_point(origin, end)
    return f"fill {sx} {sy} {sz} {ex} {ey} {ez} {block}"


def _abs_point(origin: tuple[int, int, int], point: tuple[int, int, int]) -> tuple[int, int, int]:
    return origin[0] + point[0], origin[1] + point[1], origin[2] + point[2]


def _volume(start: tuple[int, int, int], end: tuple[int, int, int]) -> int:
    x0, y0, z0 = _mins(start, end)
    x1, y1, z1 = _maxs(start, end)
    return (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)


def _cells(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> set[tuple[int, int, int]]:
    x0, y0, z0 = _mins(start, end)
    x1, y1, z1 = _maxs(start, end)
    return {
        (x, y, z)
        for x in range(x0, x1 + 1)
        for y in range(y0, y1 + 1)
        for z in range(z0, z1 + 1)
    }


def _mins(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> tuple[int, int, int]:
    return min(left[0], right[0]), min(left[1], right[1]), min(left[2], right[2])


def _maxs(
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> tuple[int, int, int]:
    return max(left[0], right[0]), max(left[1], right[1]), max(left[2], right[2])


def _triple(value: Any) -> tuple[int, int, int]:
    return int(value[0]), int(value[1]), int(value[2])


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _is_non_solid(block: str) -> bool:
    return any(block.startswith(prefix) for prefix in NON_SOLID_BLOCK_PREFIXES)
