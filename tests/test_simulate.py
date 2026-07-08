import pytest

from mcdata.qa.route import interpolate_track, simulate_track


def test_simulate_track_moves_along_route_during_forward_spans() -> None:
    trajectory = {
        "route": [
            {"x": 0, "z": 0},
            {"x": 0, "z": 1},
            {"x": 0, "z": 2},
            {"x": 2, "z": 2},
        ],
        "events": [
            {"t": 1.0, "key": "w", "action": "down"},
            {"t": 3.0, "key": "w", "action": "up"},
            {"t": 3.5, "mouse_dx": 90, "duration": 0.5},
            {"t": 4.5, "key": "w", "action": "down"},
            {"t": 6.5, "key": "w", "action": "up"},
        ],
    }

    track = simulate_track(trajectory)

    assert track == [
        {"t": 0.0, "x": 0.0, "z": 0.0},
        {"t": 1.0, "x": 0.0, "z": 0.0},
        {"t": 3.0, "x": 0.0, "z": 2.0},
        {"t": 4.5, "x": 0.0, "z": 2.0},
        {"t": 6.5, "x": 2.0, "z": 2.0},
    ]
    assert interpolate_track(track, 2.0) == {"t": 2.0, "x": 0.0, "z": 1.0}
    assert interpolate_track(track, 4.0) == {"t": 4.0, "x": 0.0, "z": 2.0}
    assert interpolate_track(track, 5.5) == {"t": 5.5, "x": 1.0, "z": 2.0}


def test_simulate_track_requires_route_spans_to_match_forward_spans() -> None:
    trajectory = {
        "route": [{"x": 0, "z": 0}, {"x": 1, "z": 0}, {"x": 2, "z": 0}],
        "events": [],
    }

    with pytest.raises(ValueError, match="route span count"):
        simulate_track(trajectory)
