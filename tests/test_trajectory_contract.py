from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcdata.action_curriculum import planned_action_contract
from mcdata.actions.strategies import build_trajectory
from mcdata.config import load_yaml
from mcdata.qa.route import simulate_track
from mcdata.scene_model import load_scene, walk_obstacles

ROOT = Path(__file__).resolve().parents[1]
WALK_STRATEGY_TYPES = {"astar_walk", "roam", "feedback_roam"}


def _configured_strategy_specs() -> Iterator[tuple[str, dict[str, Any]]]:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    for name, spec in sorted(strategies.items()):
        if spec.get("type") != "external":
            yield name, dict(spec)


def test_configured_trajectories_are_deterministic_and_ordered() -> None:
    for name, spec in _configured_strategy_specs():
        first = _build_configured(name, spec)
        second = _build_configured(name, spec)

        assert first == second
        events = first.get("events", [])
        assert events == sorted(events, key=lambda event: event.get("t", 0))


def test_configured_key_events_are_paired() -> None:
    for name, spec in _configured_strategy_specs():
        trajectory = _build_configured(name, spec)
        pressed: dict[str, int] = {}
        for event in trajectory.get("events", []):
            key = event.get("key")
            action = event.get("action")
            if key is None or action == "tap":
                continue
            if action == "down":
                pressed[key] = pressed.get(key, 0) + 1
            elif action == "up":
                pressed[key] = pressed.get(key, 0) - 1
            assert pressed.get(key, 0) >= 0, f"{name}: key {key!r} released before down"
        assert not any(pressed.values()), f"{name}: unpaired key state {pressed}"


def test_ground_astar_loop_route_stays_inside_walkable_area() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["ground_astar_loop"]
    trajectory = _build_configured("ground_astar_loop", spec)
    _assert_route_stays_inside_walkable_area(trajectory, spec)


def test_walk_routes_stay_inside_configured_bounds() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    for name, spec in strategies.items():
        if spec.get("type") not in WALK_STRATEGY_TYPES:
            continue
        trajectory = _build_configured(name, spec)
        _assert_route_stays_inside_walkable_area(trajectory, spec)


def test_open_loop_walk_routes_have_simulatable_movement_segments() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    for name, spec in strategies.items():
        if spec.get("type") not in {"astar_walk", "roam"}:
            continue
        track = simulate_track(_build_configured(name, spec))
        assert track, name


def test_astar_blocking_covers_scene_occupied_cells() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    scene_obstacles = _scene_obstacles()
    for name, spec in strategies.items():
        if spec.get("type") != "astar_walk":
            continue
        covered = _covered_cells(spec)
        assert not (scene_obstacles - covered), name


def test_walk_routes_avoid_scene_occupied_cells() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    scene_obstacles = _scene_obstacles()
    for name, spec in strategies.items():
        if spec.get("type") not in WALK_STRATEGY_TYPES:
            continue
        trajectory = _build_configured(name, spec)
        route = {(point["x"], point["z"]) for point in trajectory["route"]}
        assert not (route & scene_obstacles), name


def test_configured_blocking_matches_derived_scene_obstacles() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    scene_obstacles = _scene_obstacles()
    for name, spec in strategies.items():
        if spec.get("type") != "astar_walk":
            continue
        assert _covered_cells(spec) == scene_obstacles, name


def test_roam_trajectories_are_byte_deterministic() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    for name, spec in strategies.items():
        if spec.get("type") != "roam":
            continue
        first = json.dumps(_build_configured(name, spec), indent=2, sort_keys=True) + "\n"
        second = json.dumps(_build_configured(name, spec), indent=2, sort_keys=True) + "\n"
        assert first == second, name


def test_roam_goals_have_minimum_adjacent_distance() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    for name, spec in strategies.items():
        if spec.get("type") != "roam":
            continue
        trajectory = _build_configured(name, spec)
        goals = [(point["x"], point["z"]) for point in trajectory["goals"]]
        points = [tuple(spec["start"]), *goals]

        assert len(goals) == spec["num_goals"], name
        assert all(
            _manhattan(first, second) >= spec["min_goal_dist"]
            for first, second in zip(points, points[1:])
        ), name


def test_walk_routes_keep_configured_obstacle_clearance() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    scene_obstacles = _scene_obstacles()
    for name, spec in strategies.items():
        if not spec.get("obstacle_clearance"):
            continue
        trajectory = _build_configured(name, spec)
        route = [(point["x"], point["z"]) for point in trajectory["route"]]
        clearance = int(spec["obstacle_clearance"])

        assert all(
            max(abs(x - obstacle_x), abs(z - obstacle_z)) > clearance
            for x, z in route
            for obstacle_x, obstacle_z in scene_obstacles
        ), name


def test_different_roam_seeds_produce_distinct_routes() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    routes = {
        name: tuple((point["x"], point["z"]) for point in _build_configured(name, spec)["route"])
        for name, spec in strategies.items()
        if spec.get("type") == "roam"
    }

    assert len(routes) == 6
    assert len(set(routes.values())) == len(routes)


def test_feedback_roam_ten_minute_plan_is_closed_and_covers_capture() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    spec = strategies["feedback_roam_10min"]
    trajectory = _build_configured("feedback_roam_10min", spec)
    planning = trajectory["planning"]

    assert trajectory["type"] == "feedback_roam"
    assert trajectory["duration_sec"] >= 604
    assert trajectory["events"] == []
    assert trajectory["route"][0] == {"x": 0, "z": -14}
    assert trajectory["route"][-1] == trajectory["route"][0]
    assert trajectory["goals"][-1] == trajectory["route"][0]
    assert planning["closed"] is True
    assert planning["obstacle_clearance"] >= 2
    assert planning["route_distance_blocks"] == len(trajectory["route"]) - 1
    assert planning["sampled_goal_count"] == len(trajectory["goals"]) - 1
    assert trajectory["navigation"]["waypoint_center_offset"] == 0.5
    assert (
        trajectory["navigation"]["hard_deviation_blocks"]
        > trajectory["navigation"]["soft_deviation_blocks"]
    )
    navigation = trajectory["navigation"]
    capped_turn_rate = (
        navigation["max_turn_px"]
        / navigation["turn_px_per_degree"]
        / (navigation["control_interval_sec"] * (navigation["turn_confirmation_samples"] + 1))
    )
    assert 200 <= capped_turn_rate <= 300
    assert navigation["min_moving_control_ratio"] == 0.5
    assert navigation["max_recovery_count"] == 3
    assert navigation["long_run_gate_duration_sec"] == 600
    assert navigation["min_long_run_progress_blocks"] == 1200
    assert navigation["min_10s_movement_blocks"] == 10

    route = [(point["x"], point["z"]) for point in trajectory["route"]]
    directions = [
        (second[0] - first[0], second[1] - first[1]) for first, second in zip(route, route[1:])
    ]
    turn_count = sum(first != second for first, second in zip(directions, directions[1:]))
    assert len(set(route)) >= 350
    assert turn_count >= 200
    assert {min(x for x, _z in route), max(x for x, _z in route)} == {-16, 16}
    assert {min(z for _x, z in route), max(z for _x, z in route)} == {-16, 16}
    assert any(-4 <= x <= 4 and -3 <= z <= 9 for x, z in route)
    assert any(x <= -5 and z <= -4 for x, z in route)
    assert any(x >= 5 and z <= -4 for x, z in route)
    assert any(-14 <= x <= -1 and z >= 10 for x, z in route)
    assert any(0 <= x <= 14 and z >= 10 for x, z in route)


def test_feedback_roam_ten_minute_family_is_balanced_closed_and_distinct() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    expected_names = {
        "feedback_roam_10min",
        "feedback_roam_10min_seed402",
        "feedback_roam_10min_seed403",
        "feedback_roam_10min_seed404",
        "feedback_roam_10min_seed405",
        "feedback_roam_10min_seed406",
    }
    family = {
        name: spec
        for name, spec in strategies.items()
        if name == "feedback_roam_10min" or name.startswith("feedback_roam_10min_seed")
    }

    assert set(family) == expected_names
    assert sorted(spec["seed"] for spec in family.values()) == list(range(401, 407))
    shared_specs = [
        {key: value for key, value in spec.items() if key != "seed"} for spec in family.values()
    ]
    assert all(spec == shared_specs[0] for spec in shared_specs[1:])
    assert shared_specs[0]["target_duration_sec"] == 604
    assert shared_specs[0]["obstacle_clearance"] == 2

    trajectories = {name: _build_configured(name, spec) for name, spec in family.items()}
    routes = {
        name: tuple((point["x"], point["z"]) for point in trajectory["route"])
        for name, trajectory in trajectories.items()
    }

    assert len(set(routes.values())) == len(routes)
    for name, trajectory in trajectories.items():
        route = routes[name]
        directions = [
            (second[0] - first[0], second[1] - first[1]) for first, second in zip(route, route[1:])
        ]
        turn_count = sum(first != second for first, second in zip(directions, directions[1:]))

        assert trajectory["duration_sec"] >= 604, name
        assert route[0] == route[-1] == (0, -14), name
        assert trajectory["planning"]["closed"] is True, name
        assert trajectory["planning"]["obstacle_clearance"] == 2, name
        assert len(set(route)) >= 350, name
        assert turn_count >= 200, name


def test_waypoint_actions_insert_pause_and_look_events() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["light_closeup_tour"]
    trajectory = _build_configured("light_closeup_tour", spec)

    pause_events = [event for event in trajectory["events"] if event.get("pause") is True]
    look_events = [
        event
        for event in trajectory["events"]
        if event.get("mouse_dy") == 20 and event.get("duration") == 0.35
    ]

    assert len(pause_events) == len(spec["waypoint_actions"])
    assert len(look_events) == len(spec["waypoint_actions"])


def test_lookdev_showcase_walks_and_holds_horizontal_water_views() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["lookdev_showcase_60s"]
    trajectory = _build_configured("lookdev_showcase_60s", spec)
    events = trajectory["events"]
    water_turns = [event for event in events if abs(event.get("mouse_dy", 0)) == 20]
    held_views = [event for event in events if event.get("look_hold") is True]
    first_forward = next(
        event for event in events if event.get("key") == "w" and event.get("action") == "down"
    )

    assert trajectory["type"] == "astar_walk"
    assert trajectory["initial_heading_deg"] == 90
    assert 58 <= trajectory["duration_sec"] <= 59.2
    assert len(trajectory["route"]) >= 100
    assert not any(event.get("mouse_dx") for event in events if event["t"] < first_forward["t"])
    assert [event["mouse_dx"] for event in water_turns] == [600, -600, 600, -600]
    assert [event["look_moment"] for event in held_views] == [
        "material_closeup",
        "water_reflection_alt",
        "water_reflection",
        "scene_wide",
        "gallery_wide",
        "material_closeup_alt",
    ]
    assert all(isinstance(event.get("route_index"), int) for event in held_views)


def test_l2_jump_showcase_uses_complete_running_holds_on_a_safe_route() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    base = _build_configured("lookdev_showcase_60s", strategies["lookdev_showcase_60s"])
    trajectory = _build_configured(
        "curriculum_l2_jump_showcase_60s",
        strategies["curriculum_l2_jump_showcase_60s"],
    )
    jump_events = [
        event for event in trajectory["events"] if event.get("semantic_action") == "deliberate_jump"
    ]
    jumps = [event for event in jump_events if event.get("semantic_phase") == "press"]

    assert planned_action_contract(trajectory) == {
        "taxonomy_version": 1,
        "planned_level": 2,
        "planned_capabilities": ["navigation", "deliberate_jump"],
        "bucket": "l1_l2",
    }
    assert trajectory["duration_sec"] == 58.973
    assert 58 <= trajectory["duration_sec"] < 60
    assert trajectory["route"] != base["route"]
    assert [event["route_index"] for event in jumps] == [22, 34, 56, 71]
    assert [event["semantic_phase"] for event in jump_events] == [
        "press",
        "release",
    ] * 4
    assert all(event["key"] == "space" and event["hold_duration_sec"] == 0.16 for event in jump_events)
    assert not any(event.get("semantic_action") for event in base["events"])
    assert simulate_track(trajectory)


def test_running_jump_config_has_a_strict_field_set() -> None:
    spec = {
        "type": "astar_walk",
        "start": [0, 0],
        "goals": [[0, 4]],
        "bounds": [-1, 1, 0, 4],
        "running_jumps": [
            {
                "at": [0, 2],
                "jump_id": "jump_bad",
                "hold_duration_sec": 0.16,
                "unbound": True,
            }
        ],
    }

    with pytest.raises(RuntimeError, match="contain exactly"):
        build_trajectory("bad_jump", spec)


def test_action_curriculum_route_keeps_two_cell_clearance_from_known_pillar() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    trajectory = _build_configured(
        "curriculum_l2_jump_showcase_60s",
        strategies["curriculum_l2_jump_showcase_60s"],
    )
    route = [(point["x"], point["z"]) for point in trajectory["route"]]
    pillar_xz = (5, 9)  # scene block [5,64,9], source of the rejected GPU-run collision
    jump_indices = {
        event["route_index"]
        for event in trajectory["events"]
        if event.get("semantic_action") == "deliberate_jump"
    }

    assert min(
        max(abs(x - pillar_xz[0]), abs(z - pillar_xz[1])) for x, z in route
    ) == 3
    assert all(
        max(abs(route[index][0] - pillar_xz[0]), abs(route[index][1] - pillar_xz[1]))
        > 2
        for index in jump_indices
    )


def test_l3_place_showcase_keeps_l2_route_and_uses_real_aim_restore_events() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    l2 = _build_configured(
        "curriculum_l2_jump_showcase_60s",
        strategies["curriculum_l2_jump_showcase_60s"],
    )
    l3 = _build_configured(
        "curriculum_l3_place_showcase_60s",
        strategies["curriculum_l3_place_showcase_60s"],
    )
    placements = [
        event
        for event in l3["events"]
        if event.get("semantic_action") == "deterministic_block_placement"
    ]
    jumps = [
        event
        for event in l3["events"]
        if event.get("semantic_action") == "deliberate_jump"
        and event.get("semantic_phase") == "press"
    ]

    assert planned_action_contract(l3) == {
        "taxonomy_version": 1,
        "planned_level": 3,
        "planned_capabilities": [
            "navigation",
            "deliberate_jump",
            "deterministic_block_placement",
        ],
        "bucket": "l1_l2_l3",
    }
    assert l3["duration_sec"] == l2["duration_sec"] == 58.973
    assert l3["route"] == l2["route"]
    assert [event["route_index"] for event in jumps] == [22, 34, 56, 71]
    assert [event["route_index"] for event in placements] == [26, 80]
    assert [event["placement"]["hotbar_slot"] for event in placements] == [1, 2]
    assert [event["placement"]["face"] for event in placements] == ["south", "west"]
    route_cells = {(point["x"], point["z"]) for point in l3["route"]}
    assert all(
        (event["placement"]["target"][0], event["placement"]["target"][2]) not in route_cells
        for event in placements
    )
    assert all(
        min(
            abs(event["placement"][key][0] - x)
            + abs(event["placement"][key][2] - z)
            for x, z in route_cells
        )
        >= 2
        for event in placements
        for key in ("target", "support")
    )
    for placement in placements:
        index = l3["events"].index(placement)
        aim = l3["events"][index - 1]
        restore = l3["events"][index + 1]
        assert aim["placement_aim"] is True
        assert restore["placement_aim_restore"] is True
        assert (restore["mouse_dx"], restore["mouse_dy"]) == (
            -aim["mouse_dx"],
            -aim["mouse_dy"],
        )


def test_l4_combat_showcase_is_cumulative_and_keeps_the_same_60s_route() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    l3 = _build_configured(
        "curriculum_l3_place_showcase_60s",
        strategies["curriculum_l3_place_showcase_60s"],
    )
    l4 = _build_configured(
        "curriculum_l4_combat_showcase_60s",
        strategies["curriculum_l4_combat_showcase_60s"],
    )
    combats = [
        event
        for event in l4["events"]
        if event.get("semantic_action") == "controlled_combat"
    ]
    placements = [
        event
        for event in l4["events"]
        if event.get("semantic_action") == "deterministic_block_placement"
    ]
    jumps = [
        event
        for event in l4["events"]
        if event.get("semantic_action") == "deliberate_jump"
        and event.get("semantic_phase") == "press"
    ]

    assert planned_action_contract(l4) == {
        "taxonomy_version": 1,
        "planned_level": 4,
        "planned_capabilities": [
            "navigation",
            "deliberate_jump",
            "deterministic_block_placement",
            "controlled_combat",
        ],
        "bucket": "l1_l2_l3_l4",
    }
    assert l4["route"] == l3["route"]
    assert l4["duration_sec"] == l3["duration_sec"] == 58.973
    assert [event["route_index"] for event in jumps] == [22, 34, 56, 71]
    assert [event["route_index"] for event in placements] == [26, 80]
    assert len(combats) == 1 and combats[0]["route_index"] == 48
    spec = combats[0]["combat"]
    assert spec["target_entity"] == "minecraft:iron_golem"
    assert spec["target_uuid"] == "4d434441-5441-4c34-8000-000000000004"
    assert spec["spawn"] == [4.5, 64.0, 12.5]
    assert spec["hotbar_slot"] == 3
    assert spec["weapon"] == "minecraft:wooden_sword"
    route_cells = {(point["x"], point["z"]) for point in l4["route"]}
    assert (spec["spawn"][0], spec["spawn"][2]) not in route_cells
    assert min(
        abs(spec["spawn"][0] - x) + abs(spec["spawn"][2] - z)
        for x, z in route_cells
    ) == 3
    combat_index = l4["events"].index(combats[0])
    aim = l4["events"][combat_index - 1]
    restore = l4["events"][combat_index + 1]
    assert aim["combat_aim"] is True
    assert restore["combat_aim_restore"] is True
    assert (aim["mouse_dx"], aim["mouse_dy"]) == (0, -20)
    assert (restore["mouse_dx"], restore["mouse_dy"]) == (0, 20)


def test_l4_checked_in_trajectory_and_documented_copy_are_identical() -> None:
    golden = (ROOT / "tests/golden/curriculum_l4_combat_showcase_60s.json").read_bytes()
    documented = (
        ROOT / "docs/trajectories/curriculum_l4_combat_showcase_60s.json"
    ).read_bytes()

    assert documented == golden
    assert hashlib.sha256(golden).hexdigest() == (
        "219af517550950d0f0a71a9a6a2bdcd5b3cc52c4588b1cd18b017e6608d8d0f2"
    )


def test_turn_calibration_probe_is_eight_600px_turns() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["turn_calibration_probe"]
    trajectory = build_trajectory("turn_calibration_probe", spec)

    assert trajectory["type"] == "scripted"
    assert trajectory["duration_sec"] == 24
    assert len(trajectory["events"]) == 8
    assert [event["t"] for event in trajectory["events"]] == [
        2.5,
        5.0,
        7.5,
        10.0,
        12.5,
        15.0,
        17.5,
        20.0,
    ]
    assert all(event["mouse_dx"] == 600 for event in trajectory["events"])
    assert all(event["mouse_dy"] == 0 for event in trajectory["events"])
    assert all(event["duration"] == 0.35 for event in trajectory["events"])


def test_walk_calibration_probe_uses_varied_holds_on_corridor() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["walk_calibration_probe"]
    trajectory = build_trajectory("walk_calibration_probe", spec)
    events = trajectory["events"]

    holds = [
        (down["t"], up["t"])
        for down, up in zip(events, events[1:])
        if down.get("key") == "w" and down.get("action") == "down"
    ]
    turns = [event for event in events if "mouse_dx" in event]

    assert trajectory["type"] == "scripted"
    assert trajectory["duration_sec"] == 32
    assert holds == [(4.0, 5.0), (9.6, 11.1), (15.7, 17.7), (22.3, 24.8)]
    assert [(round(up - down, 3)) for down, up in holds] == [1.0, 1.5, 2.0, 2.5]
    assert [event["mouse_dx"] for event in turns] == [-600, 600, 600, 600, 600, 600, 600]
    assert all(event["mouse_dy"] == 0 for event in turns)
    assert all(event["duration"] == 0.35 for event in turns)


def _assert_route_stays_inside_walkable_area(
    trajectory: dict[str, Any],
    spec: dict[str, Any],
) -> None:
    min_x, max_x, min_z, max_z = spec["bounds"]
    covered = _covered_cells(spec)

    for point in trajectory["route"]:
        x = point["x"]
        z = point["z"]
        assert min_x <= x <= max_x
        assert min_z <= z <= max_z
        assert (x, z) not in covered


def _covered_cells(spec: dict[str, Any]) -> set[tuple[int, int]]:
    covered = {tuple(cell) for cell in spec.get("blocked") or []}
    for rect_min_x, rect_min_z, rect_max_x, rect_max_z in spec.get("blocked_rects") or []:
        covered.update(
            (x, z)
            for x in range(rect_min_x, rect_max_x + 1)
            for z in range(rect_min_z, rect_max_z + 1)
        )
    return covered


def _scene_obstacles() -> set[tuple[int, int]]:
    return walk_obstacles(load_scene(ROOT / "configs"))


def _build_configured(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    return build_trajectory(name, dict(spec), scene_obstacles=_scene_obstacles())


def _manhattan(first: tuple[int, int], second: tuple[int, int]) -> int:
    return abs(first[0] - second[0]) + abs(first[1] - second[1])
