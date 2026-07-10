from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcdata.navigation_surface import (
    AIR_BLOCK,
    UNKNOWN_BLOCK,
    NavigationSurfaceError,
    astar_surface,
    build_navigation_surface,
    derive_scene_navigation_surface,
    edge_primitive_counts,
    make_navigation_node,
    navigation_surface_document,
    surface_edges,
)
from mcdata.navigation_surface_artifact import load_navigation_surface_artifact
from mcdata.scene_model import load_scene, parse_scene, walk_obstacles

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = ROOT / "configs" / "navigation_surfaces" / "showcase_plaza_flat_v1.json"
TERRAIN_INSTANCE_ID = "showcase_plaza_flat_v1_mc26_2_seed1_render_matrix_base"
STONE = "minecraft:stone"


def _node(
    x: int,
    feet_y: int,
    z: int,
    *,
    support: str = STONE,
    feet: str = AIR_BLOCK,
    head: str = AIR_BLOCK,
):
    return make_navigation_node(
        x,
        feet_y,
        z,
        support_block=support,
        feet_block=feet,
        head_block=head,
        support_allowlist={support},
    )


def _surface(nodes):
    return build_navigation_surface(
        "synthetic_step_course_unit_fixture",
        "synthetic_not_a_registered_terrain",
        nodes,
        support_allowlist={STONE},
    )


def test_canonical_plaza_surface_is_rederived_and_content_addressed() -> None:
    surface = load_navigation_surface_artifact(
        ARTIFACT_PATH,
        repository_root=ROOT,
        expected_terrain_instance_id=TERRAIN_INSTANCE_ID,
    )

    assert len(surface.nodes) == 1089
    assert sum(node.traversable for node in surface.nodes) == 825
    assert edge_primitive_counts(surface) == {
        "walk": 3064,
        "jump_up": 0,
        "drop_down": 0,
    }
    assert surface.surface_sha256 == (
        "c6d0a6e6cda4df3bd75cf006002c98da917163a7989e545ae7028633f85be5fb"
    )


def test_canonical_surface_preserves_existing_flat_plaza_walkable_columns() -> None:
    scene = load_scene(ROOT / "configs")
    surface = load_navigation_surface_artifact(ARTIFACT_PATH, repository_root=ROOT)
    expected_columns = {
        (x, z)
        for x in range(-16, 17)
        for z in range(-16, 17)
        if (x, z) not in walk_obstacles(scene)
    }
    actual_columns = {node.column for node in surface.nodes if node.traversable}

    assert actual_columns == expected_columns
    assert all(node.feet_y == 64 for node in surface.nodes)


def test_surface_nodes_record_support_occupancy_headroom_fluid_and_hazard() -> None:
    surface = load_navigation_surface_artifact(ARTIFACT_PATH, repository_root=ROOT)
    nodes = surface.by_key()

    clear = nodes[(0, 64, -14)]
    assert clear.support_block == "minecraft:cut_sandstone"
    assert clear.feet_block == clear.head_block == AIR_BLOCK
    assert clear.headroom_blocks == 2
    assert clear.traversable

    water = nodes[(-10, 64, 0)]
    assert water.support_block == "minecraft:water"
    assert water.fluid and not water.hazard and not water.traversable
    assert "fluid" in water.rejection_codes

    rim = nodes[(-14, 64, 0)]
    assert rim.feet_block == "minecraft:polished_blackstone_bricks"
    assert rim.headroom_blocks == 0
    assert not rim.traversable

    canonical = navigation_surface_document(surface)
    assert canonical["surface_sha256"] == surface.surface_sha256
    assert set(canonical["nodes"][0]) == {
        "position",
        "support_block",
        "feet_block",
        "head_block",
        "headroom_blocks",
        "fluid",
        "hazard",
        "traversable",
        "rejection_codes",
    }


@pytest.mark.parametrize(
    ("support", "expected_code"),
    [
        (UNKNOWN_BLOCK, "unknown_block"),
        ("minecraft:stone_slab", "unsupported_support_shape"),
        ("minecraft:stone_stairs", "unsupported_support_shape"),
        ("minecraft:oak_door", "unsupported_support_shape"),
        ("minecraft:ladder", "unsupported_support_shape"),
        ("minecraft:water", "fluid"),
        ("minecraft:magma_block", "hazard"),
    ],
)
def test_non_full_unknown_shaped_fluid_or_hazard_support_fails_closed(
    support: str, expected_code: str
) -> None:
    node = _node(0, 64, 0, support=support)

    assert not node.traversable
    assert expected_code in node.rejection_codes


@pytest.mark.parametrize("occupancy", [UNKNOWN_BLOCK, "minecraft:cave_air", STONE])
def test_only_exact_air_counts_as_feet_and_headroom(occupancy: str) -> None:
    feet_blocked = _node(0, 64, 0, feet=occupancy)
    head_blocked = _node(0, 64, 0, head=occupancy)

    assert not feet_blocked.traversable
    assert not head_blocked.traversable
    assert feet_blocked.headroom_blocks == 0
    assert head_blocked.headroom_blocks == 1


def test_v1_rejects_two_surfaces_in_one_xz_column() -> None:
    with pytest.raises(NavigationSurfaceError, match="at most one surface per x/z"):
        _surface([_node(0, 64, 0), _node(0, 65, 0)])


def test_edge_primitives_and_l2_jump_capability_gate() -> None:
    surface = _surface(
        [
            _node(0, 64, 0),
            _node(1, 65, 0),
            _node(2, 64, 0),
        ]
    )
    edges = surface_edges(surface)

    assert [(edge.source, edge.target, edge.primitive) for edge in edges] == [
        ((0, 64, 0), (1, 65, 0), "jump_up"),
        ((1, 65, 0), (0, 64, 0), "drop_down"),
        ((1, 65, 0), (2, 64, 0), "drop_down"),
        ((2, 64, 0), (1, 65, 0), "jump_up"),
    ]
    with pytest.raises(NavigationSurfaceError, match="could not route"):
        astar_surface(
            surface,
            (0, 64, 0),
            (2, 64, 0),
            capabilities={"navigation"},
        )
    route = astar_surface(
        surface,
        (0, 64, 0),
        (2, 64, 0),
        capabilities={"navigation", "deliberate_jump"},
    )
    assert [edge.primitive for edge in route] == ["jump_up", "drop_down"]
    drop = astar_surface(
        surface,
        (1, 65, 0),
        (2, 64, 0),
        capabilities={"navigation"},
    )
    assert [edge.primitive for edge in drop] == ["drop_down"]


def test_height_delta_greater_than_one_has_no_edge() -> None:
    surface = _surface([_node(0, 64, 0), _node(1, 66, 0)])

    assert surface_edges(surface) == ()


def test_headroom_and_hazard_nodes_never_receive_adjacency() -> None:
    surface = _surface(
        [
            _node(0, 64, 0),
            _node(1, 64, 0, head=STONE),
            _node(0, 64, 1, support="minecraft:magma_block"),
        ]
    )

    assert surface_edges(surface) == ()


def test_surface_astar_tie_break_is_stable_across_input_order() -> None:
    nodes = [
        _node(0, 64, 0),
        _node(1, 64, 0),
        _node(0, 64, 1),
        _node(1, 64, 1),
    ]
    paths = []
    for ordered in (nodes, list(reversed(nodes))):
        route = astar_surface(
            _surface(ordered),
            (0, 64, 0),
            (1, 64, 1),
            capabilities={"navigation"},
        )
        paths.append([route[0].source, *(edge.target for edge in route)])

    assert paths == [
        [(0, 64, 0), (0, 64, 1), (1, 64, 1)],
        [(0, 64, 0), (0, 64, 1), (1, 64, 1)],
    ]


def test_scene_derivation_uses_world_xz_origin() -> None:
    scene = parse_scene(
        {
            "origin": [10, 64, -7],
            "entries": [
                {
                    "kind": "fill",
                    "block": STONE,
                    "from": [0, -1, 0],
                    "to": [1, -1, 0],
                },
                {
                    "kind": "fill",
                    "block": AIR_BLOCK,
                    "from": [0, 0, 0],
                    "to": [1, 1, 0],
                },
                {
                    "kind": "setblock",
                    "block": STONE,
                    "at": [3, 0, 4],
                },
            ],
        }
    )
    surface = derive_scene_navigation_surface(
        scene,
        surface_id="origin_fixture",
        terrain_instance_id="synthetic_not_a_registered_terrain",
        feet_y=64,
        x_bounds=(10, 11),
        z_bounds=(-7, -7),
        support_allowlist={STONE},
    )

    assert [node.key for node in surface.nodes] == [(10, 64, -7), (11, 64, -7)]
    assert all(node.traversable for node in surface.nodes)
    assert walk_obstacles(scene) == {(13, -3)}


def test_artifact_expected_surface_hash_drift_fails_closed(tmp_path: Path) -> None:
    document = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    document["expected"]["surface_sha256"] = "a" * 64
    path = tmp_path / "surface.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(NavigationSurfaceError, match="derived summary mismatch"):
        load_navigation_surface_artifact(path, repository_root=ROOT)


def test_artifact_scene_hash_and_unknown_field_drift_fail_closed(tmp_path: Path) -> None:
    for mutate, match in (
        (
            lambda document: document["derivation"]["scene"].update(sha256="a" * 64),
            "scene SHA-256 mismatch",
        ),
        (lambda document: document.update(generated_at="now"), "violates schema"),
    ):
        document = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        mutate(document)
        path = tmp_path / f"surface-{match.split()[0]}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(NavigationSurfaceError, match=match):
            load_navigation_surface_artifact(path, repository_root=ROOT)
