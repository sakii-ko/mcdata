from mcdata.actions.strategies import _route_segments, _shortest_turn, _turn_steps


def test_route_segments_merge_same_heading() -> None:
    route = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (2, 1)]

    assert _route_segments(route) == [(0, 2), (270, 2), (180, 1)]


def test_shortest_turn_wraps_across_zero() -> None:
    assert _shortest_turn(350, 10) == 20
    assert _shortest_turn(10, 350) == -20


def test_turn_steps_splits_large_turns() -> None:
    assert _turn_steps(90) == [90]
    assert _turn_steps(120) == [60, 60]
    assert _turn_steps(-180) == [-90, -90]
