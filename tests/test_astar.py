import pytest

from mcdata.actions.strategies import _astar, _points_in_rect


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
