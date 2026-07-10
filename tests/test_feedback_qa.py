import json
from pathlib import Path

from mcdata.qa.feedback import check_feedback_route
from mcdata.qa.route import route_reference_report


def _trajectory() -> dict:
    return {
        "type": "feedback_roam",
        "route": [
            {"x": 0, "z": 0},
            {"x": 1, "z": 0},
            {"x": 2, "z": 0},
            {"x": 1, "z": 0},
            {"x": 0, "z": 0},
        ],
        "navigation": {
            "waypoint_center_offset": 0.5,
            "hard_deviation_blocks": 2.0,
            "move_yaw_limit_deg": 12.0,
            "y_min": 63.0,
            "y_max": 66.0,
        },
    }


def _positions() -> list[dict]:
    return [
        {"idx": 0, "t_rel": 0.0, "x": 0.5, "y": 64.0, "z": 0.5, "yaw": -90.0},
        {"idx": 1, "t_rel": 0.5, "x": 1.0, "y": 64.0, "z": 0.5, "yaw": -90.0},
        {"idx": 2, "t_rel": 1.0, "x": 1.5, "y": 64.0, "z": 0.5, "yaw": -90.0},
        {"idx": 3, "t_rel": 1.5, "x": 2.0, "y": 64.0, "z": 0.5, "yaw": -90.0},
        {"idx": 4, "t_rel": 2.0, "x": 2.5, "y": 64.0, "z": 0.5, "yaw": 90.0},
    ]


def _navigation() -> list[dict]:
    return [
        {"event": "start", "mono": 1.0},
        {"event": "control", "t_rel": 0.0, "moving": True, "yaw_error": 1.0},
        {"event": "waypoint", "t_rel": 1.0, "cycle": 0, "route_index": 2},
        {"event": "control", "t_rel": 2.0, "moving": True, "yaw_error": -2.0},
        {"event": "stop", "t_rel": 2.0},
    ]


def test_feedback_route_qa_passes_real_movement_and_feedback_coverage() -> None:
    result = check_feedback_route(
        _positions(),
        _trajectory(),
        _navigation(),
        expected_duration_sec=2.0,
    )

    assert result["passed"] is True
    assert result["movement_distance_blocks"] == 2.0
    assert result["unique_occupied_cells"] == 3
    assert result["waypoints_reached"] == 1
    assert result["navigation_duration_ratio"] == 1.0
    assert result["ordered_route_progress_blocks"] == 2
    assert result["moving_control_ratio"] == 1.0
    assert result["minimum_moving_control_ratio"] == 0.5
    assert result["maximum_recovery_count"] == 3
    assert result["terminal_stop"] is True


def test_feedback_route_qa_rejects_stationary_or_failed_navigation() -> None:
    positions = [dict(item, x=0.5, z=0.5) for item in _positions()]
    navigation = [*_navigation(), {"event": "failure", "t_rel": 1.5}]

    result = check_feedback_route(
        positions,
        _trajectory(),
        navigation,
        expected_duration_sec=2.0,
    )

    assert result["passed"] is False
    assert result["movement_distance_blocks"] == 0.0
    assert result["failure_count"] == 1


def test_feedback_route_qa_rejects_unordered_prefix_loops_and_missing_stop() -> None:
    navigation = [item for item in _navigation() if item["event"] not in {"waypoint", "stop"}]

    result = check_feedback_route(
        _positions(),
        _trajectory(),
        navigation,
        expected_duration_sec=2.0,
    )

    assert result["passed"] is False
    assert result["route_progress_ordered"] is False
    assert result["terminal_stop"] is False


def test_route_reference_dispatches_feedback_trajectory(tmp_path: Path) -> None:
    (tmp_path / "trajectory.json").write_text(
        json.dumps(_trajectory()) + "\n",
        encoding="utf-8",
    )
    for filename, rows in (
        ("positions.jsonl", _positions()),
        ("navigation_log.jsonl", _navigation()),
    ):
        (tmp_path / filename).write_text(
            "".join(json.dumps(item) + "\n" for item in rows),
            encoding="utf-8",
        )

    result = route_reference_report(tmp_path, expected_duration_sec=2.0)

    assert result is not None
    assert result["mode"] == "online_position_yaw_feedback"
    assert result["passed"] is True


def _long_run_fixture() -> tuple[list[dict], dict, list[dict]]:
    route = [{"x": x, "z": 0} for x in [*range(1301), *range(1299, -1, -1)]]
    trajectory = {
        "type": "feedback_roam",
        "duration_sec": 610.0,
        "route": route,
        "navigation": {
            "waypoint_center_offset": 0.5,
            "hard_deviation_blocks": 2.0,
            "move_yaw_limit_deg": 12.0,
            "min_moving_control_ratio": 0.5,
            "max_recovery_count": 3,
            "long_run_gate_duration_sec": 600.0,
            "min_long_run_progress_blocks": 1200.0,
            "min_10s_movement_blocks": 10.0,
            "y_min": 63.0,
            "y_max": 66.0,
        },
    }
    positions = [
        {
            "idx": index,
            "t_rel": index * 0.5,
            "x": index + 0.5,
            "y": 64.0,
            "z": 0.5,
            "yaw": -90.0,
        }
        for index in range(1201)
    ]
    controls = [
        {
            "event": "control",
            "t_rel": index * 6.0,
            "moving": index < 50,
            "yaw_error": 0.0,
        }
        for index in range(100)
    ]
    navigation = [
        {"event": "start", "mono": 1.0},
        *controls,
        {"event": "waypoint", "t_rel": 599.0, "cycle": 0, "route_index": 1200},
        {"event": "stop", "t_rel": 600.0},
    ]
    return positions, trajectory, navigation


def test_six_hundred_second_gate_accepts_only_substantial_feedback_progress() -> None:
    positions, trajectory, navigation = _long_run_fixture()

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["passed"] is True
    assert result["long_run_gate_active"] is True
    assert result["movement_distance_blocks"] == 1200.0
    assert result["minimum_movement_distance_blocks"] == 1200.0
    assert result["ordered_route_progress_blocks"] == 1200
    assert result["minimum_route_progress_blocks"] == 1200.0
    assert result["moving_control_ratio"] == 0.5
    assert result["minimum_10s_movement_blocks"] == 20.0


def test_six_hundred_second_gate_rejects_low_moving_ratio() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    controls = [item for item in navigation if item.get("event") == "control"]
    controls[49]["moving"] = False

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["moving_control_ratio"] == 0.49
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_less_than_twelve_hundred_blocks() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    waypoint = next(item for item in navigation if item.get("event") == "waypoint")
    waypoint["route_index"] = 1199
    for item in positions:
        item["x"] = 0.5 + float(item["idx"]) * 1199.0 / 1200.0

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["movement_distance_blocks"] < 1200.0
    assert result["ordered_route_progress_blocks"] == 1199
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_excess_recovery() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    navigation[-1:-1] = [
        {"event": "recovery", "t_rel": 100.0 + index, "attempt": index + 1} for index in range(4)
    ]

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["recovery_count"] == 4
    assert result["maximum_recovery_count"] == 3
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_a_stagnant_ten_second_window() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    for item in positions[400:421]:
        item["x"] = positions[399]["x"]

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["minimum_10s_movement_blocks"] == 0.0
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_any_out_of_range_height() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    positions[500]["y"] = 67.0

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["y_out_of_range_count"] == 1
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_a_large_position_step() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    positions[500]["x"] += 4.0

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["max_position_step_blocks"] == 5.0
    assert result["max_position_step_threshold_blocks"] == 3.0
    assert result["passed"] is False


def test_six_hundred_second_gate_rejects_route_deviation() -> None:
    positions, trajectory, navigation = _long_run_fixture()
    z_values = [1.5, 2.5, 3.5, 3.5, 2.5, 1.5]
    for item, z in zip(positions[500:506], z_values):
        item["z"] = z

    result = check_feedback_route(
        positions,
        trajectory,
        navigation,
        expected_duration_sec=600.0,
    )

    assert result["max_deviation_blocks"] == 3.0
    assert result["threshold_blocks"] == 2.75
    assert result["max_position_step_blocks"] < 3.0
    assert result["passed"] is False
