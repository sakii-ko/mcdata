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
