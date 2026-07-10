import pytest

from mcdata.actions.feedback import build_feedback_roam


def _spec(**overrides) -> dict:
    return {
        "type": "feedback_roam",
        "seed": 7,
        "start": [0, 0],
        "bounds": [-4, 4, -4, 4],
        "target_duration_sec": 5.0,
        "seconds_per_block": 0.25,
        "min_goal_dist": 2,
        "min_goals": 2,
        "max_goals": 50,
        "recent_goal_window": 2,
        "obstacle_clearance": 2,
        **overrides,
    }


def test_feedback_roam_is_deterministic_closed_and_long_enough() -> None:
    first = build_feedback_roam(_spec())
    second = build_feedback_roam(_spec())

    assert first == second
    assert first["type"] == "feedback_roam"
    assert first["duration_sec"] >= 5.0
    assert first["route"][0] == {"x": 0, "z": 0}
    assert first["route"][-1] == first["route"][0]
    assert first["goals"][-1] == first["route"][0]
    assert first["planning"]["closed"] is True
    assert first["planning"]["route_distance_blocks"] == len(first["route"]) - 1
    assert first["navigation"] == {
        "control_interval_sec": 0.1,
        "waypoint_tolerance_blocks": 0.35,
        "waypoint_center_offset": 0.5,
        "lookahead_blocks": 2,
            "yaw_tolerance_deg": 2.0,
            "move_yaw_limit_deg": 12.0,
        "soft_deviation_blocks": 0.75,
        "hard_deviation_blocks": 2.0,
        "position_stale_sec": 0.75,
        "stuck_window_sec": 1.0,
        "stuck_distance_blocks": 0.15,
            "max_recovery_attempts": 3,
            "recovery_hold_sec": 0.2,
            "turn_px_per_degree": 6.6667,
            "max_turn_px": 180,
        "y_min": 63.0,
        "y_max": 66.0,
        "seconds_per_block": 0.25,
    }
    assert first["events"] == []


def test_feedback_roam_route_uses_cardinal_grid_steps() -> None:
    trajectory = build_feedback_roam(_spec())
    route = [(item["x"], item["z"]) for item in trajectory["route"]]

    assert all(
        abs(left[0] - right[0]) + abs(left[1] - right[1]) == 1
        for left, right in zip(route, route[1:])
    )


def test_feedback_roam_seed_changes_the_route() -> None:
    first = build_feedback_roam(_spec(seed=7))
    second = build_feedback_roam(_spec(seed=8))

    assert first["route"] != second["route"]


def test_feedback_roam_requires_explicit_seed() -> None:
    spec = _spec()
    del spec["seed"]

    with pytest.raises(RuntimeError, match="requires an explicit seed"):
        build_feedback_roam(spec)


def test_feedback_roam_requires_two_block_clearance() -> None:
    with pytest.raises(RuntimeError, match="clearance must be at least 2"):
        build_feedback_roam(_spec(obstacle_clearance=1))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hard_deviation_blocks": 0.5}, "must exceed soft_deviation_blocks"),
        ({"position_stale_sec": 0.05}, "must cover a control interval"),
        ({"move_yaw_limit_deg": 1.0}, "must cover yaw_tolerance_deg"),
        ({"y_min": 67, "y_max": 66}, "y_min must not exceed y_max"),
    ],
)
def test_feedback_roam_rejects_invalid_navigation_controls(
    overrides: dict,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        build_feedback_roam(_spec(**overrides))


def test_feedback_roam_fails_when_goal_budget_cannot_cover_duration() -> None:
    with pytest.raises(RuntimeError, match="could not reach target duration"):
        build_feedback_roam(
            _spec(
                target_duration_sec=1000,
                min_goals=1,
                max_goals=1,
            )
        )
