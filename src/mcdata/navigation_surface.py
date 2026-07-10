from __future__ import annotations

import hashlib
import heapq
import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from mcdata.scene_model import SceneSpec

AIR_BLOCK = "minecraft:air"
UNKNOWN_BLOCK = "mcdata:unknown"
SURFACE_SCHEMA_VERSION = 1
DERIVATION_POLICY = "declarative_scene_flat_surface_v1"
EDGE_POLICY = "orthogonal_cardinal_height_delta_1_v1"

# This is intentionally an exact allowlist, not a heuristic such as "not air".
# Additions require a reviewed full-cube collision claim and a surface hash change.
FULL_BLOCK_SUPPORT_ALLOWLIST = frozenset(
    {
        "minecraft:cut_sandstone",
        "minecraft:dark_oak_planks",
        "minecraft:deepslate_tiles",
        "minecraft:grass_block",
        "minecraft:mossy_stone_bricks",
        "minecraft:mud_bricks",
        "minecraft:polished_andesite",
        "minecraft:polished_blackstone_bricks",
        "minecraft:stone_bricks",
        "minecraft:stripped_spruce_wood[axis=z]",
        "minecraft:waxed_oxidized_cut_copper",
    }
)

_FLUID_BLOCKS = frozenset(
    {
        "minecraft:bubble_column",
        "minecraft:lava",
        "minecraft:water",
    }
)
_HAZARD_BLOCKS = frozenset(
    {
        "minecraft:cactus",
        "minecraft:campfire",
        "minecraft:fire",
        "minecraft:lava",
        "minecraft:magma_block",
        "minecraft:powder_snow",
        "minecraft:soul_campfire",
        "minecraft:soul_fire",
        "minecraft:sweet_berry_bush",
        "minecraft:wither_rose",
    }
)
_UNSUPPORTED_SUPPORT_SUFFIXES = (
    "_door",
    "_fence",
    "_fence_gate",
    "_slab",
    "_stairs",
    "_trapdoor",
    "_wall",
)

NodeKey = tuple[int, int, int]


class NavigationSurfaceError(ValueError):
    """Raised when a navigation surface or its provenance cannot be verified."""


@dataclass(frozen=True, order=True)
class NavigationNode:
    """One controlled 2.5D surface candidate in Minecraft world coordinates."""

    x: int
    feet_y: int
    z: int
    support_block: str
    feet_block: str
    head_block: str
    headroom_blocks: int
    fluid: bool
    hazard: bool
    traversable: bool
    rejection_codes: tuple[str, ...]

    @property
    def key(self) -> NodeKey:
        return self.x, self.feet_y, self.z

    @property
    def column(self) -> tuple[int, int]:
        return self.x, self.z


@dataclass(frozen=True)
class NavigationSurface:
    surface_id: str
    terrain_instance_id: str
    support_allowlist: tuple[str, ...]
    nodes: tuple[NavigationNode, ...]
    surface_sha256: str

    def by_key(self) -> dict[NodeKey, NavigationNode]:
        return {node.key: node for node in self.nodes}

    def by_column(self) -> dict[tuple[int, int], NavigationNode]:
        return {node.column: node for node in self.nodes}


@dataclass(frozen=True, order=True)
class SurfaceEdge:
    source: NodeKey
    target: NodeKey
    primitive: str
    required_capabilities: tuple[str, ...]


def make_navigation_node(
    x: int,
    feet_y: int,
    z: int,
    *,
    support_block: str,
    feet_block: str,
    head_block: str,
    support_allowlist: Iterable[str] = FULL_BLOCK_SUPPORT_ALLOWLIST,
) -> NavigationNode:
    """Classify a node from the three block cells that define player occupancy."""
    allowlist = frozenset(support_allowlist)
    blocks = (support_block, feet_block, head_block)
    fluid = any(_is_fluid(block) for block in blocks)
    hazard = any(_is_hazard(block) for block in blocks)
    headroom = 0
    if feet_block == AIR_BLOCK:
        headroom = 1
        if head_block == AIR_BLOCK:
            headroom = 2

    rejection_codes: list[str] = []
    if UNKNOWN_BLOCK in blocks:
        rejection_codes.append("unknown_block")
    if _unsupported_support_shape(support_block):
        rejection_codes.append("unsupported_support_shape")
    if support_block not in allowlist:
        rejection_codes.append("support_not_in_full_block_allowlist")
    if fluid:
        rejection_codes.append("fluid")
    if hazard:
        rejection_codes.append("hazard")
    if feet_block != AIR_BLOCK:
        rejection_codes.append("feet_not_air")
    if head_block != AIR_BLOCK:
        rejection_codes.append("head_not_air")
    if headroom < 2:
        rejection_codes.append("insufficient_headroom")

    return NavigationNode(
        x=int(x),
        feet_y=int(feet_y),
        z=int(z),
        support_block=support_block,
        feet_block=feet_block,
        head_block=head_block,
        headroom_blocks=headroom,
        fluid=fluid,
        hazard=hazard,
        traversable=not rejection_codes,
        rejection_codes=tuple(rejection_codes),
    )


def build_navigation_surface(
    surface_id: str,
    terrain_instance_id: str,
    nodes: Iterable[NavigationNode],
    *,
    support_allowlist: Iterable[str] = FULL_BLOCK_SUPPORT_ALLOWLIST,
) -> NavigationSurface:
    """Validate, sort, and content-address an in-memory canonical surface."""
    ordered_allowlist = tuple(sorted(set(support_allowlist)))
    canonical_nodes = (
        make_navigation_node(
            node.x,
            node.feet_y,
            node.z,
            support_block=node.support_block,
            feet_block=node.feet_block,
            head_block=node.head_block,
            support_allowlist=ordered_allowlist,
        )
        for node in nodes
    )
    ordered_nodes = tuple(sorted(canonical_nodes, key=lambda node: node.key))
    _validate_nodes(ordered_nodes)
    provisional = NavigationSurface(
        surface_id=surface_id,
        terrain_instance_id=terrain_instance_id,
        support_allowlist=ordered_allowlist,
        nodes=ordered_nodes,
        surface_sha256="",
    )
    surface = NavigationSurface(
        surface_id=surface_id,
        terrain_instance_id=terrain_instance_id,
        support_allowlist=ordered_allowlist,
        nodes=ordered_nodes,
        surface_sha256=navigation_surface_sha256(provisional),
    )
    return surface


def navigation_surface_payload(surface: NavigationSurface) -> dict[str, Any]:
    """Return the stable content payload; stored hashes and timestamps are excluded."""
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "coordinate_frame": "minecraft_world_block_xyz",
        "edge_policy": EDGE_POLICY,
        "max_surfaces_per_xz": 1,
        "surface_id": surface.surface_id,
        "terrain_instance_id": surface.terrain_instance_id,
        "support_allowlist": list(surface.support_allowlist),
        "nodes": [_node_mapping(node) for node in surface.nodes],
    }


def navigation_surface_sha256(surface: NavigationSurface) -> str:
    encoded = json.dumps(
        navigation_surface_payload(surface),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def navigation_surface_document(surface: NavigationSurface) -> dict[str, Any]:
    """Return the full schema-validated surface envelope including its content SHA."""
    return {
        **navigation_surface_payload(surface),
        "surface_sha256": surface.surface_sha256,
    }


def derive_scene_navigation_surface(
    scene: SceneSpec,
    *,
    surface_id: str,
    terrain_instance_id: str,
    feet_y: int,
    x_bounds: tuple[int, int],
    z_bounds: tuple[int, int],
    support_allowlist: Iterable[str] = FULL_BLOCK_SUPPORT_ALLOWLIST,
) -> NavigationSurface:
    """Derive one fixed-feet-Y surface per x/z from an ordered declarative scene."""
    min_x, max_x = x_bounds
    min_z, max_z = z_bounds
    if min_x > max_x or min_z > max_z:
        raise NavigationSurfaceError("Navigation surface bounds must be ascending")
    blocks = _scene_blocks(
        scene,
        x_bounds=x_bounds,
        y_bounds=(feet_y - 1, feet_y + 1),
        z_bounds=z_bounds,
    )
    nodes = (
        make_navigation_node(
            x,
            feet_y,
            z,
            support_block=blocks.get((x, feet_y - 1, z), UNKNOWN_BLOCK),
            feet_block=blocks.get((x, feet_y, z), UNKNOWN_BLOCK),
            head_block=blocks.get((x, feet_y + 1, z), UNKNOWN_BLOCK),
            support_allowlist=support_allowlist,
        )
        for x in range(min_x, max_x + 1)
        for z in range(min_z, max_z + 1)
    )
    return build_navigation_surface(
        surface_id,
        terrain_instance_id,
        nodes,
        support_allowlist=support_allowlist,
    )


def surface_edges(surface: NavigationSurface) -> tuple[SurfaceEdge, ...]:
    """Derive stable directed walk/jump-up/drop-down adjacency."""
    columns = surface.by_column()
    edges: list[SurfaceEdge] = []
    for source in surface.nodes:
        if not source.traversable:
            continue
        targets = [
            columns.get((source.x + dx, source.z + dz))
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
        ]
        for target in sorted((item for item in targets if item is not None), key=lambda n: n.key):
            if not target.traversable:
                continue
            delta_y = target.feet_y - source.feet_y
            primitive = _edge_primitive(delta_y)
            if primitive is None:
                continue
            required = ("navigation",)
            if primitive == "jump_up":
                required = ("navigation", "deliberate_jump")
            edges.append(
                SurfaceEdge(
                    source=source.key,
                    target=target.key,
                    primitive=primitive,
                    required_capabilities=required,
                )
            )
    return tuple(sorted(edges))


def astar_surface(
    surface: NavigationSurface,
    start: NodeKey,
    goal: NodeKey,
    *,
    capabilities: Iterable[str],
) -> tuple[SurfaceEdge, ...]:
    """Route with deterministic heap/neighbor tie breaks and explicit action gates."""
    capability_set = frozenset(capabilities)
    if "navigation" not in capability_set:
        raise NavigationSurfaceError("Surface A* requires the navigation capability")
    nodes = surface.by_key()
    for label, key in (("start", start), ("goal", goal)):
        node = nodes.get(key)
        if node is None:
            raise NavigationSurfaceError(f"Surface A* {label} node is missing: {key}")
        if not node.traversable:
            raise NavigationSurfaceError(f"Surface A* {label} node is not traversable: {key}")
    if start == goal:
        return ()

    adjacency: dict[NodeKey, list[SurfaceEdge]] = {}
    for edge in surface_edges(surface):
        if set(edge.required_capabilities) <= capability_set:
            adjacency.setdefault(edge.source, []).append(edge)

    queue: list[tuple[int, int, int, NodeKey]] = []
    start_h = _node_distance(start, goal)
    heapq.heappush(queue, (start_h, start_h, 0, start))
    cost: dict[NodeKey, int] = {start: 0}
    came_from: dict[NodeKey, tuple[NodeKey, SurfaceEdge]] = {}
    while queue:
        _priority, _heuristic, queued_cost, current = heapq.heappop(queue)
        if queued_cost != cost.get(current):
            continue
        if current == goal:
            break
        for edge in adjacency.get(current, []):
            new_cost = queued_cost + 1
            if new_cost >= cost.get(edge.target, 2**63 - 1):
                continue
            cost[edge.target] = new_cost
            heuristic = _node_distance(edge.target, goal)
            heapq.heappush(
                queue,
                (new_cost + heuristic, heuristic, new_cost, edge.target),
            )
            came_from[edge.target] = (current, edge)

    if goal not in came_from:
        raise NavigationSurfaceError(
            f"Surface A* could not route from {start} to {goal} with "
            f"capabilities {sorted(capability_set)}"
        )
    route: list[SurfaceEdge] = []
    cursor = goal
    while cursor != start:
        previous, edge = came_from[cursor]
        route.append(edge)
        cursor = previous
    return tuple(reversed(route))


def edge_primitive_counts(surface: NavigationSurface) -> dict[str, int]:
    counts = {"walk": 0, "jump_up": 0, "drop_down": 0}
    for edge in surface_edges(surface):
        counts[edge.primitive] += 1
    return counts


def _validate_nodes(nodes: Sequence[NavigationNode]) -> None:
    keys: set[NodeKey] = set()
    columns: set[tuple[int, int]] = set()
    for node in nodes:
        if node.key in keys:
            raise NavigationSurfaceError(f"Duplicate navigation surface node: {node.key}")
        if node.column in columns:
            raise NavigationSurfaceError(
                f"Navigation surface v1 permits at most one surface per x/z: {node.column}"
            )
        keys.add(node.key)
        columns.add(node.column)


def _node_mapping(node: NavigationNode) -> dict[str, Any]:
    return {
        "position": [node.x, node.feet_y, node.z],
        "support_block": node.support_block,
        "feet_block": node.feet_block,
        "head_block": node.head_block,
        "headroom_blocks": node.headroom_blocks,
        "fluid": node.fluid,
        "hazard": node.hazard,
        "traversable": node.traversable,
        "rejection_codes": list(node.rejection_codes),
    }


def _scene_blocks(
    scene: SceneSpec,
    *,
    x_bounds: tuple[int, int],
    y_bounds: tuple[int, int],
    z_bounds: tuple[int, int],
) -> dict[NodeKey, str]:
    blocks: dict[NodeKey, str] = {}
    ox, oy, oz = scene.origin
    for entry in scene.entries:
        if entry.kind == "forceload":
            continue
        entry_x = sorted((ox + entry.start[0], ox + entry.end[0]))
        entry_y = sorted((oy + entry.start[1], oy + entry.end[1]))
        entry_z = sorted((oz + entry.start[2], oz + entry.end[2]))
        min_x, max_x = max(x_bounds[0], entry_x[0]), min(x_bounds[1], entry_x[1])
        min_y, max_y = max(y_bounds[0], entry_y[0]), min(y_bounds[1], entry_y[1])
        min_z, max_z = max(z_bounds[0], entry_z[0]), min(z_bounds[1], entry_z[1])
        if min_x > max_x or min_y > max_y or min_z > max_z:
            continue
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                for z in range(min_z, max_z + 1):
                    key = (x, y, z)
                    if entry.replace is None or blocks.get(key) == entry.replace:
                        blocks[key] = str(entry.block)
    return blocks


def _edge_primitive(delta_y: int) -> str | None:
    if delta_y == 0:
        return "walk"
    if delta_y == 1:
        return "jump_up"
    if delta_y == -1:
        return "drop_down"
    return None


def _node_distance(first: NodeKey, second: NodeKey) -> int:
    return abs(first[0] - second[0]) + abs(first[2] - second[2])


def _base_block(block: str) -> str:
    return block.split("[", 1)[0]


def _is_fluid(block: str) -> bool:
    return _base_block(block) in _FLUID_BLOCKS or "waterlogged=true" in block


def _is_hazard(block: str) -> bool:
    return _base_block(block) in _HAZARD_BLOCKS


def _unsupported_support_shape(block: str) -> bool:
    base = _base_block(block)
    return base == "minecraft:ladder" or base.endswith(_UNSUPPORTED_SUPPORT_SUFFIXES)
