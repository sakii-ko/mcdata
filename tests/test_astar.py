import pytest

from mcdata.actions.strategies import _astar, _points_in_rect, build_trajectory


def test_points_in_rect_includes_boundaries() -> None:
    points = _points_in_rect([-1, -2, 1, 0])

    assert (-1, -2) in points
    assert (1, 0) in points
    assert len(points) == 9


def test_astar_routes_around_blocked_points() -> None:
    path = _astar((0, 0), (2, 0), bounds=(-1, 2, -1, 1), blocked={(1, 0)})

    assert path[0] == (0, 0)
    assert path[-1] == (2, 0)
    assert (1, 0) not in path


def test_astar_does_not_walk_outside_bounds() -> None:
    path = _astar((0, 0), (0, 2), bounds=(0, 1, 0, 2), blocked={(0, 1)})

    assert all(0 <= x <= 1 and 0 <= z <= 2 for x, z in path)
    assert (0, 1) not in path


def test_astar_raises_when_unreachable() -> None:
    with pytest.raises(RuntimeError, match="could not route"):
        _astar((0, 0), (0, 2), bounds=(0, 0, 0, 2), blocked={(0, 1)})


def test_astar_walk_adds_startup_compensation_to_forward_holds() -> None:
    trajectory = build_trajectory(
        "unit_walk_comp",
        {
            "type": "astar_walk",
            "start": [0, 0],
            "goals": [[0, 2]],
            "bounds": [-1, 1, 0, 2],
            "blocked": [],
            "seconds_per_block": 0.5,
            "walk_startup_comp_sec": 0.25,
            "initial_pause_sec": 0,
            "scan_pause_sec": 0,
        },
    )

    forward_events = [event for event in trajectory["events"] if event.get("key") == "w"]

    assert forward_events == [
        {"t": 0.0, "key": "w", "action": "down"},
        {"t": 1.25, "key": "w", "action": "up"},
    ]


def test_astar_walk_repeats_closed_goal_loop() -> None:
    spec = {
        "type": "astar_walk",
        "start": [0, 0],
        "goals": [[0, 2], [0, 0]],
        "bounds": [-1, 1, 0, 2],
        "blocked": [],
        "seconds_per_block": 0.5,
        "initial_pause_sec": 0,
        "scan_pause_sec": 0,
    }

    single = build_trajectory("unit_walk_single", spec)
    repeated = build_trajectory("unit_walk_repeated", {**spec, "loops": 2})

    assert repeated["loops"] == 2
    assert repeated["route"] == single["route"] + single["route"][1:]
    assert repeated["duration_sec"] > single["duration_sec"]
    single_forward = [event for event in single["events"] if event.get("key") == "w"]
    repeated_forward = [event for event in repeated["events"] if event.get("key") == "w"]
    assert len(repeated_forward) == 2 * len(single_forward)
    assert [event["action"] for event in repeated_forward] == ["down", "up"] * 4


def test_astar_walk_rejects_non_positive_loops() -> None:
    with pytest.raises(RuntimeError, match="loops must be at least 1"):
        build_trajectory(
            "unit_walk_invalid_loops",
            {
                "type": "astar_walk",
                "start": [0, 0],
                "goals": [[0, 1]],
                "bounds": [0, 0, 0, 1],
                "loops": 0,
            },
        )


def test_roam_requires_explicit_seed() -> None:
    with pytest.raises(RuntimeError, match="requires an explicit seed"):
        build_trajectory(
            "unit_roam",
            {
                "type": "roam",
                "start": [0, 0],
                "bounds": [0, 2, 0, 2],
            },
        )


def test_roam_raises_after_goal_sampling_limit() -> None:
    with pytest.raises(RuntimeError, match="after 100 attempts"):
        build_trajectory(
            "unit_roam",
            {
                "type": "roam",
                "seed": 7,
                "start": [0, 0],
                "bounds": [0, 1, 0, 1],
                "num_goals": 1,
                "min_goal_dist": 3,
            },
        )
