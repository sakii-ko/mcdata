import math
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.render.navigation import (
    ServerLogPoseSource,
    point_segment_distance,
    shortest_yaw_delta,
    turning_waypoints,
    yaw_to_target,
)
from mcdata.render import pipeline


def test_turning_waypoints_compresses_straight_route_at_corners() -> None:
    route = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2)]

    assert turning_waypoints(route) == [
        (0, (0, 0)),
        (2, (2, 0)),
        (4, (2, 2)),
        (5, (1, 2)),
    ]


def test_turning_waypoints_rejects_diagonal_or_skipped_steps() -> None:
    with pytest.raises(RuntimeError, match="not cardinal"):
        turning_waypoints([(0, 0), (2, 1)])


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ((0.0, 1.0), 0.0),
        ((-1.0, 0.0), 90.0),
        ((0.0, -1.0), -180.0),
        ((1.0, 0.0), -90.0),
    ],
)
def test_yaw_to_target_uses_minecraft_heading_convention(target, expected) -> None:
    assert math.isclose(yaw_to_target((0.0, 0.0), target), expected)


def test_shortest_yaw_delta_wraps_across_180() -> None:
    assert shortest_yaw_delta(170.0, -170.0) == 20.0
    assert shortest_yaw_delta(-170.0, 170.0) == -20.0


def test_point_segment_distance_clamps_to_segment_ends() -> None:
    assert point_segment_distance((1.0, 2.0), (0.0, 0.0), (2.0, 0.0)) == 2.0
    assert point_segment_distance((4.0, 0.0), (0.0, 0.0), (2.0, 0.0)) == 2.0


def test_server_log_pose_source_pairs_incremental_position_and_rotation(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[INFO]: bot has the following entity data: [0.5d, 64.0d, -13.5d]\n"
        "[INFO]: bot has the following entity data: [-90.0f, 10.0f]\n",
        encoding="utf-8",
    )
    source = ServerLogPoseSource(log, username="bot")

    first = source.latest()

    assert first is not None
    assert (first.x, first.y, first.z, first.yaw, first.sequence) == (0.5, 64.0, -13.5, -90.0, 1)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("[INFO]: bot has the following entity data: [1.25d, 64.0d, -13.5d]\n")
    assert source.latest() == first
    with log.open("a", encoding="utf-8") as fh:
        fh.write("[INFO]: bot has the following entity data: [-89.0f, 10.0f]\n")

    second = source.latest()

    assert second is not None
    assert (second.x, second.z, second.yaw, second.sequence) == (1.25, -13.5, -89.0, 2)


def test_feedback_trajectory_uses_control_rate_for_position_probe(tmp_path: Path) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text(
        '{"type":"feedback_roam","navigation":{"control_interval_sec":0.1}}\n',
        encoding="utf-8",
    )
    plan = SimpleNamespace(
        trajectory_info={"type": "feedback_roam"},
        run_trajectory_path=trajectory,
        probe_interval=5.0,
    )

    assert pipeline._effective_probe_interval(plan) == 0.1


def test_capture_fails_closed_when_navigation_worker_fails(tmp_path: Path) -> None:
    class FakeGame:
        args = ["minecraft"]

        def poll(self):
            return None

    class FakeCapture:
        args = ["ffmpeg"]
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            return 255

    capture = FakeCapture()
    stop = threading.Event()
    failure = ValueError("lost feedback")

    with pytest.raises(RuntimeError, match="capture worker failed") as raised:
        pipeline._wait_for_capture(
            FakeGame(),
            capture,
            tmp_path / "exitcode",
            stop_event=stop,
            worker_error=lambda: failure,
        )

    assert raised.value.__cause__ is failure
    assert capture.terminated is True
    assert stop.is_set()


def test_replay_phase_selects_feedback_navigator(tmp_path: Path, monkeypatch) -> None:
    trajectory = tmp_path / "trajectory.json"
    trajectory.write_text('{"type":"feedback_roam"}\n', encoding="utf-8")
    plan = SimpleNamespace(
        replay_actions=True,
        run_trajectory_path=trajectory,
        dry_run=False,
        trajectory_info={"type": "feedback_roam"},
        with_server=True,
        capture=True,
    )
    state = pipeline.RunState()
    started = object()
    monkeypatch.setattr(pipeline, "_start_navigation_thread", lambda _plan, _state: started)

    class RunLog:
        records = []

        def log(self, *args, **kwargs):
            self.records.append((args, kwargs))

    runlog = RunLog()

    pipeline._replay_phase(plan, state, runlog)

    assert state.replay_thread is started
    assert runlog.records[0][0] == ("navigation", "thread_started")


def test_feedback_replay_requires_capture_and_managed_server(tmp_path: Path) -> None:
    plan = SimpleNamespace(
        replay_actions=True,
        run_trajectory_path=tmp_path / "trajectory.json",
        dry_run=False,
        trajectory_info={"type": "feedback_roam"},
        with_server=True,
        capture=False,
    )

    with pytest.raises(RuntimeError, match="requires capture with a managed server"):
        pipeline._replay_phase(plan, pipeline.RunState(), SimpleNamespace(log=lambda *_a, **_k: None))
