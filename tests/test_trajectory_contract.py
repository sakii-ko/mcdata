from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mcdata.actions.strategies import build_trajectory
from mcdata.config import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def _configured_strategy_specs() -> Iterator[tuple[str, dict[str, Any]]]:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    for name, spec in sorted(strategies.items()):
        if spec.get("type") != "external":
            yield name, dict(spec)


def test_configured_trajectories_are_deterministic_and_ordered() -> None:
    for name, spec in _configured_strategy_specs():
        first = build_trajectory(name, spec)
        second = build_trajectory(name, spec)

        assert first == second
        events = first.get("events", [])
        assert events == sorted(events, key=lambda event: event.get("t", 0))


def test_configured_key_events_are_paired() -> None:
    for name, spec in _configured_strategy_specs():
        trajectory = build_trajectory(name, spec)
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
    trajectory = build_trajectory("ground_astar_loop", spec)
    _assert_route_stays_inside_walkable_area(trajectory, spec)


def test_astar_loop_routes_stay_inside_walkable_area() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]
    for name, spec in strategies.items():
        if spec.get("type") != "astar_walk":
            continue
        trajectory = build_trajectory(name, spec)
        _assert_route_stays_inside_walkable_area(trajectory, spec)


def test_waypoint_actions_insert_pause_and_look_events() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["light_closeup_tour"]
    trajectory = build_trajectory("light_closeup_tour", spec)

    pause_events = [event for event in trajectory["events"] if event.get("pause") is True]
    look_events = [
        event
        for event in trajectory["events"]
        if event.get("mouse_dy") == 20 and event.get("duration") == 0.35
    ]

    assert len(pause_events) == len(spec["waypoint_actions"])
    assert len(look_events) == len(spec["waypoint_actions"])


def test_turn_calibration_probe_is_eight_600px_turns() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"]["turn_calibration_probe"]
    trajectory = build_trajectory("turn_calibration_probe", spec)

    assert trajectory["type"] == "scripted"
    assert trajectory["duration_sec"] == 24
    assert len(trajectory["events"]) == 8
    assert [event["t"] for event in trajectory["events"]] == [2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0]
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
    blocked_rects = list(spec.get("blocked_rects") or [])

    for point in trajectory["route"]:
        x = point["x"]
        z = point["z"]
        assert min_x <= x <= max_x
        assert min_z <= z <= max_z
        for rect_min_x, rect_min_z, rect_max_x, rect_max_z in blocked_rects:
            assert not (rect_min_x <= x <= rect_max_x and rect_min_z <= z <= rect_max_z)
