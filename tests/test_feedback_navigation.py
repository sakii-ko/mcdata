import math
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcdata.render.navigation import (
    NavigationConfig,
    ServerLogPoseSource,
    _NavigationState,
    _check_turn_response,
    _progress_stalled,
    _turn_pixels,
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


def _navigation_config() -> NavigationConfig:
    return NavigationConfig(
        control_interval_sec=0.1,
        waypoint_tolerance_blocks=0.35,
        waypoint_center_offset=0.5,
        yaw_tolerance_deg=2.0,
        move_yaw_limit_deg=12.0,
        soft_deviation_blocks=0.75,
        hard_deviation_blocks=2.0,
        position_stale_sec=0.75,
        stuck_window_sec=1.0,
        stuck_distance_blocks=0.15,
        max_recovery_attempts=3,
        recovery_hold_sec=0.2,
        turn_px_per_degree=6.6667,
        turn_gain=1.0,
        turn_confirmation_samples=1,
        turn_response_timeout_sec=1.0,
        turn_min_improvement_deg=2.0,
        max_turn_px=360,
        progress_timeout_sec=3.0,
        progress_min_distance_blocks=0.25,
        y_min=63.0,
        y_max=66.0,
    )


@pytest.mark.parametrize(("target_yaw", "expected_sequence"), [(90.0, 5), (179.0, 9)])
def test_proportional_turn_controller_converges_with_two_pose_delay(
    target_yaw: float, expected_sequence: int
) -> None:
    config = _navigation_config()
    state = _NavigationState()
    yaw = 0.0
    pending: list[tuple[int, float]] = []

    for sequence in range(1, 41):
        yaw += sum(delta for apply_at, delta in pending if apply_at == sequence)
        pending = [item for item in pending if item[0] > sequence]
        error = shortest_yaw_delta(yaw, target_yaw)
        if abs(error) <= config.yaw_tolerance_deg:
            break
        turn_sent = sequence >= state.next_turn_sequence
        if turn_sent:
            pixels = _turn_pixels(error, config)
            pending.append((sequence + 2, pixels * 0.15))
            state.next_turn_sequence = sequence + config.turn_confirmation_samples + 1
        _check_turn_response(
            abs(error),
            turning=True,
            turn_sent=turn_sent,
            now=sequence * 0.1,
            config=config,
            state=state,
        )

    assert abs(shortest_yaw_delta(yaw, target_yaw)) <= config.yaw_tolerance_deg
    assert sequence == expected_sequence


def test_turn_rate_matches_normal_gameplay_without_dropping_confirmation() -> None:
    config = _navigation_config()

    degrees_per_step = config.max_turn_px / config.turn_px_per_degree
    seconds_per_step = config.control_interval_sec * (config.turn_confirmation_samples + 1)
    capped_rate = degrees_per_step / seconds_per_step

    assert 200.0 <= capped_rate <= 300.0
    assert config.turn_confirmation_samples == 1


def test_turn_response_watchdog_rejects_ignored_mouse_input() -> None:
    config = _navigation_config()
    state = _NavigationState()

    with pytest.raises(RuntimeError, match="yaw input did not improve"):
        for sequence in range(1, 30):
            turn_sent = sequence >= state.next_turn_sequence
            if turn_sent:
                state.next_turn_sequence = sequence + config.turn_confirmation_samples + 1
            _check_turn_response(
                90.0,
                turning=True,
                turn_sent=turn_sent,
                now=sequence * 0.1,
                config=config,
                state=state,
            )


def test_route_progress_watchdog_is_independent_of_moving_state() -> None:
    config = _navigation_config()
    state = _NavigationState()

    assert _progress_stalled(state, 0.0, 10.0, config) is False
    assert _progress_stalled(state, 2.9, 10.0, config) is False
    assert _progress_stalled(state, 3.01, 10.0, config) is True


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


def test_server_log_pose_source_uses_query_time_for_staleness(tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text(
        "[INFO]: bot has the following entity data: [0.5d, 64.0d, -13.5d]\n"
        "[INFO]: bot has the following entity data: [90.0f, 10.0f]\n",
        encoding="utf-8",
    )

    pose = ServerLogPoseSource(log, username="bot", query_sent_at=[123.5]).latest()

    assert pose is not None
    assert pose.observed_mono == 123.5


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
        pipeline._replay_phase(
            plan, pipeline.RunState(), SimpleNamespace(log=lambda *_a, **_k: None)
        )


def test_join_replay_worker_fails_if_thread_does_not_stop() -> None:
    class StuckThread:
        def join(self, timeout):
            assert timeout == 5

        def is_alive(self):
            return True

    records = []
    state = pipeline.RunState(replay_thread=StuckThread())
    runlog = SimpleNamespace(log=lambda *args, **kwargs: records.append((args, kwargs)))

    failure = pipeline._join_replay_worker(state, runlog)

    assert failure == "action/navigation worker remained alive after 5s teardown timeout"
    assert records == [(("replay", "thread_joined"), {"alive": True})]
