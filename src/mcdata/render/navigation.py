from __future__ import annotations

import json
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from mcdata.actions.replay import InputController, StartEvent, StopEvent
from mcdata.render.pose import Pose, PoseSource, ServerLogPoseSource

GridPoint = tuple[int, int]
WorldPoint = tuple[float, float]


@dataclass(frozen=True)
class NavigationConfig:
    control_interval_sec: float
    waypoint_tolerance_blocks: float
    waypoint_center_offset: float
    yaw_tolerance_deg: float
    move_yaw_limit_deg: float
    soft_deviation_blocks: float
    hard_deviation_blocks: float
    position_stale_sec: float
    stuck_window_sec: float
    stuck_distance_blocks: float
    max_recovery_attempts: int
    recovery_hold_sec: float
    turn_px_per_degree: float
    turn_gain: float
    turn_confirmation_samples: int
    turn_response_timeout_sec: float
    turn_min_improvement_deg: float
    max_turn_px: int
    progress_timeout_sec: float
    progress_min_distance_blocks: float
    y_min: float
    y_max: float

    @classmethod
    def from_trajectory(cls, data: dict) -> NavigationConfig:
        raw = data.get("navigation")
        if not isinstance(raw, dict):
            raise RuntimeError("feedback_roam trajectory is missing navigation settings")
        config = cls(
            control_interval_sec=float(raw["control_interval_sec"]),
            waypoint_tolerance_blocks=float(raw["waypoint_tolerance_blocks"]),
            waypoint_center_offset=float(raw["waypoint_center_offset"]),
            yaw_tolerance_deg=float(raw["yaw_tolerance_deg"]),
            move_yaw_limit_deg=float(raw.get("move_yaw_limit_deg", 12.0)),
            soft_deviation_blocks=float(raw["soft_deviation_blocks"]),
            hard_deviation_blocks=float(raw["hard_deviation_blocks"]),
            position_stale_sec=float(raw["position_stale_sec"]),
            stuck_window_sec=float(raw["stuck_window_sec"]),
            stuck_distance_blocks=float(raw["stuck_distance_blocks"]),
            max_recovery_attempts=int(raw["max_recovery_attempts"]),
            recovery_hold_sec=float(raw.get("recovery_hold_sec", 0.2)),
            turn_px_per_degree=float(raw.get("turn_px_per_degree", 6.6667)),
            turn_gain=float(raw.get("turn_gain", 0.35)),
            turn_confirmation_samples=int(raw.get("turn_confirmation_samples", 1)),
            turn_response_timeout_sec=float(raw.get("turn_response_timeout_sec", 1.0)),
            turn_min_improvement_deg=float(raw.get("turn_min_improvement_deg", 2.0)),
            max_turn_px=int(raw.get("max_turn_px", 100)),
            progress_timeout_sec=float(raw.get("progress_timeout_sec", 3.0)),
            progress_min_distance_blocks=float(raw.get("progress_min_distance_blocks", 0.25)),
            y_min=float(raw["y_min"]),
            y_max=float(raw["y_max"]),
        )
        config.validate()
        return config

    def validate(self) -> None:
        positive = {
            "control_interval_sec": self.control_interval_sec,
            "waypoint_tolerance_blocks": self.waypoint_tolerance_blocks,
            "yaw_tolerance_deg": self.yaw_tolerance_deg,
            "move_yaw_limit_deg": self.move_yaw_limit_deg,
            "soft_deviation_blocks": self.soft_deviation_blocks,
            "hard_deviation_blocks": self.hard_deviation_blocks,
            "position_stale_sec": self.position_stale_sec,
            "stuck_window_sec": self.stuck_window_sec,
            "max_recovery_attempts": self.max_recovery_attempts,
            "recovery_hold_sec": self.recovery_hold_sec,
            "turn_px_per_degree": self.turn_px_per_degree,
            "turn_gain": self.turn_gain,
            "turn_response_timeout_sec": self.turn_response_timeout_sec,
            "turn_min_improvement_deg": self.turn_min_improvement_deg,
            "max_turn_px": self.max_turn_px,
            "progress_timeout_sec": self.progress_timeout_sec,
            "progress_min_distance_blocks": self.progress_min_distance_blocks,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise RuntimeError(f"feedback navigation settings must be positive: {invalid}")
        if not 0 <= self.waypoint_center_offset < 1:
            raise RuntimeError("feedback navigation waypoint_center_offset must be in [0, 1)")
        if self.y_min > self.y_max:
            raise RuntimeError("feedback navigation y_min must not exceed y_max")
        if self.hard_deviation_blocks <= self.soft_deviation_blocks:
            raise RuntimeError("feedback navigation hard deviation must exceed soft deviation")
        if self.move_yaw_limit_deg < self.yaw_tolerance_deg:
            raise RuntimeError("feedback navigation move yaw limit must cover yaw tolerance")
        if self.turn_gain > 1:
            raise RuntimeError("feedback navigation turn_gain must not exceed 1")
        if self.turn_confirmation_samples < 0:
            raise RuntimeError("feedback navigation turn_confirmation_samples must not be negative")


@dataclass
class _NavigationState:
    waypoint_index: int = 1
    cycle: int = 0
    recoveries: int = 0
    last_sequence: int = -1
    first_pose: bool = True
    movement_samples: deque[tuple[float, float, float]] = field(default_factory=deque)
    last_pose_seen: float = field(default_factory=time.monotonic)
    next_turn_sequence: int = 0
    turn_anchor_mono: float | None = None
    turn_anchor_error: float | None = None
    progress_anchor_mono: float | None = None
    progress_anchor_distance: float | None = None


class _NavigationLog:
    def __init__(self, path: Path, *, start_mono: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")
        self._start_mono = start_mono
        self._write({"event": "start", "mono": start_mono})

    def write(self, event: str, **fields: object) -> None:
        now = time.monotonic()
        self._write(
            {
                "event": event,
                "ts": datetime.now(timezone.utc).isoformat(),
                "t_rel": now - self._start_mono,
                **fields,
            }
        )

    def close(self) -> None:
        self._fh.close()

    def _write(self, record: dict) -> None:
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()


def run_feedback_navigation(
    trajectory_path: Path,
    *,
    server_log_path: Path,
    username: str,
    start_event: StartEvent,
    stop_event: StopEvent,
    run_dir: Path,
    position_query_sent_at: list[float] | None = None,
    window_name: str = "Minecraft",
) -> None:
    data = json.loads(trajectory_path.read_text(encoding="utf-8"))
    if data.get("type") != "feedback_roam":
        raise RuntimeError("online navigator requires a feedback_roam trajectory")
    route = _trajectory_route(data)
    config = NavigationConfig.from_trajectory(data)
    if not _wait_for_start(start_event, stop_event):
        return
    start_mono = time.monotonic()
    nav_log = _NavigationLog(run_dir / "navigation_log.jsonl", start_mono=start_mono)
    source = ServerLogPoseSource(
        server_log_path,
        username=username,
        query_sent_at=position_query_sent_at,
    )
    try:
        with InputController(window_name=window_name, stop_event=stop_event) as controller:
            if controller.inherited_keys:
                nav_log.write("inherited_stuck_keys", keys=controller.inherited_keys)
            _navigate(route, config, source, controller, stop_event, nav_log)
    except Exception as exc:
        nav_log.write("failure", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        nav_log.close()


def _navigate(
    route: list[GridPoint],
    config: NavigationConfig,
    source: PoseSource,
    controller: InputController,
    stop_event: StopEvent,
    nav_log: _NavigationLog,
) -> None:
    waypoints = turning_waypoints(route)
    if len(waypoints) < 2:
        raise RuntimeError("feedback navigation route has no movement")
    centered = [
        (index, _center(point, config.waypoint_center_offset)) for index, point in waypoints
    ]
    state = _NavigationState()
    while not stop_event.is_set():
        pose = source.latest()
        now = time.monotonic()
        if stop_event.is_set():
            break
        if pose is None or pose.sequence == state.last_sequence:
            _check_stale_feedback(now, state.last_pose_seen, config)
            _sleep_interruptible(config.control_interval_sec, stop_event)
            continue
        state.last_sequence = pose.sequence
        state.last_pose_seen = pose.observed_mono
        _validate_pose(pose, centered[0][1], config, state)
        previous = centered[state.waypoint_index - 1][1]
        route_index, target = centered[state.waypoint_index]
        deviation = point_segment_distance((pose.x, pose.z), previous, target)
        if deviation > config.hard_deviation_blocks:
            raise RuntimeError(
                f"route deviation {deviation:.3f} exceeds {config.hard_deviation_blocks}"
            )
        distance = _distance((pose.x, pose.z), target)
        if distance <= config.waypoint_tolerance_blocks:
            _advance_waypoint(pose, distance, route_index, centered, controller, nav_log, state)
            continue
        _control_toward_waypoint(
            pose,
            target=target,
            route_index=route_index,
            distance=distance,
            deviation=deviation,
            now=now,
            config=config,
            state=state,
            controller=controller,
            stop_event=stop_event,
            nav_log=nav_log,
        )
        _sleep_interruptible(config.control_interval_sec, stop_event)
    nav_log.write(
        "stop",
        reason="stop_requested",
        cycle=state.cycle,
        waypoint_index=state.waypoint_index,
    )


def _check_stale_feedback(
    now: float,
    last_pose_seen: float,
    config: NavigationConfig,
) -> None:
    if now - last_pose_seen > config.position_stale_sec:
        raise RuntimeError(f"position feedback stale for {now - last_pose_seen:.3f}s")


def _validate_pose(
    pose: Pose,
    route_origin: WorldPoint,
    config: NavigationConfig,
    state: _NavigationState,
) -> None:
    if not config.y_min <= pose.y <= config.y_max:
        raise RuntimeError(f"navigation y={pose.y:.3f} outside [{config.y_min}, {config.y_max}]")
    if not state.first_pose:
        return
    start_distance = _distance((pose.x, pose.z), route_origin)
    if start_distance > config.hard_deviation_blocks:
        raise RuntimeError(f"navigation started {start_distance:.3f} blocks from route origin")
    state.first_pose = False


def _advance_waypoint(
    pose: Pose,
    distance: float,
    route_index: int,
    centered: list[tuple[int, WorldPoint]],
    controller: InputController,
    nav_log: _NavigationLog,
    state: _NavigationState,
) -> None:
    controller.key_up("w")
    nav_log.write(
        "waypoint",
        route_index=route_index,
        waypoint_index=state.waypoint_index,
        x=pose.x,
        y=pose.y,
        z=pose.z,
        distance=distance,
        cycle=state.cycle,
    )
    state.waypoint_index += 1
    state.recoveries = 0
    state.movement_samples.clear()
    state.turn_anchor_mono = None
    state.turn_anchor_error = None
    state.progress_anchor_mono = None
    state.progress_anchor_distance = None
    if state.waypoint_index >= len(centered):
        state.cycle += 1
        state.waypoint_index = 1
        nav_log.write("cycle", cycle=state.cycle)


def _control_toward_waypoint(
    pose: Pose,
    *,
    target: WorldPoint,
    route_index: int,
    distance: float,
    deviation: float,
    now: float,
    config: NavigationConfig,
    state: _NavigationState,
    controller: InputController,
    stop_event: StopEvent,
    nav_log: _NavigationLog,
) -> None:
    desired_yaw = yaw_to_target((pose.x, pose.z), target)
    yaw_error = shortest_yaw_delta(pose.yaw, desired_yaw)
    mouse_dx = _turn_pixels(yaw_error, config)
    turning = abs(yaw_error) > config.yaw_tolerance_deg
    turn_sent = turning and pose.sequence >= state.next_turn_sequence
    if turn_sent:
        controller.move_mouse(mouse_dx)
        state.next_turn_sequence = pose.sequence + config.turn_confirmation_samples + 1
    _check_turn_response(
        abs(yaw_error),
        turning=turning,
        turn_sent=turn_sent,
        now=now,
        config=config,
        state=state,
    )
    moving = abs(yaw_error) <= config.move_yaw_limit_deg
    if moving:
        controller.key_down("w")
        state.movement_samples.append((now, pose.x, pose.z))
    else:
        controller.key_up("w")
        state.movement_samples.clear()
    _log_control(
        nav_log,
        pose=pose,
        target_state=(route_index, distance, deviation, desired_yaw, yaw_error),
        moving=moving,
        mouse_dx=mouse_dx if turn_sent else 0,
        config=config,
        state=state,
    )
    if moving and _is_stuck(state.movement_samples, config, now):
        _recover_or_raise(
            route_index,
            distance,
            config,
            state,
            controller,
            stop_event,
            nav_log,
            reason="position_stuck",
        )
        _reset_progress_watchdog(state, now, distance)
    elif _progress_stalled(state, now, distance, config):
        _recover_or_raise(
            route_index,
            distance,
            config,
            state,
            controller,
            stop_event,
            nav_log,
            reason="route_progress_timeout",
        )
        _reset_progress_watchdog(state, now, distance)


def _log_control(
    nav_log: _NavigationLog,
    *,
    pose: Pose,
    target_state: tuple[int, float, float, float, float],
    moving: bool,
    mouse_dx: int,
    config: NavigationConfig,
    state: _NavigationState,
) -> None:
    route_index, distance, deviation, desired_yaw, yaw_error = target_state
    nav_log.write(
        "control",
        route_index=route_index,
        waypoint_index=state.waypoint_index,
        x=pose.x,
        y=pose.y,
        z=pose.z,
        yaw=pose.yaw,
        desired_yaw=desired_yaw,
        yaw_error=yaw_error,
        distance=distance,
        deviation=deviation,
        soft_deviation=deviation > config.soft_deviation_blocks,
        moving=moving,
        mouse_dx=mouse_dx,
        cycle=state.cycle,
    )


def _recover_or_raise(
    route_index: int,
    distance: float,
    config: NavigationConfig,
    state: _NavigationState,
    controller: InputController,
    stop_event: StopEvent,
    nav_log: _NavigationLog,
    *,
    reason: str,
) -> None:
    state.recoveries += 1
    if state.recoveries > config.max_recovery_attempts:
        raise RuntimeError(f"stuck recovery budget exhausted at route index {route_index}")
    _recover(controller, stop_event, config)
    nav_log.write(
        "recovery",
        attempt=state.recoveries,
        route_index=route_index,
        distance=distance,
        reason=reason,
    )
    state.movement_samples.clear()


def _trajectory_route(data: dict) -> list[GridPoint]:
    raw = data.get("route")
    if not isinstance(raw, list) or len(raw) < 2:
        raise RuntimeError("feedback_roam trajectory must contain at least two route points")
    route: list[GridPoint] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) < {"x", "z"}:
            raise RuntimeError("feedback_roam route points require x and z")
        route.append((int(item["x"]), int(item["z"])))
    if route[0] != route[-1]:
        raise RuntimeError("feedback_roam navigation route must be closed")
    turning_waypoints(route)
    return route


def turning_waypoints(route: list[GridPoint]) -> list[tuple[int, GridPoint]]:
    if len(route) < 2:
        return [(0, route[0])] if route else []
    result = [(0, route[0])]
    previous_direction: GridPoint | None = None
    for index, (current, nxt) in enumerate(zip(route, route[1:]), 1):
        direction = (nxt[0] - current[0], nxt[1] - current[1])
        if abs(direction[0]) + abs(direction[1]) != 1:
            raise RuntimeError(f"route step {index} is not cardinal and unit length")
        if previous_direction is not None and direction != previous_direction:
            result.append((index - 1, current))
        previous_direction = direction
    final = (len(route) - 1, route[-1])
    if result[-1] != final:
        result.append(final)
    return result


def yaw_to_target(current: WorldPoint, target: WorldPoint) -> float:
    dx = target[0] - current[0]
    dz = target[1] - current[1]
    return math.degrees(math.atan2(-dx, dz))


def shortest_yaw_delta(current: float, desired: float) -> float:
    return (desired - current + 180.0) % 360.0 - 180.0


def point_segment_distance(point: WorldPoint, start: WorldPoint, end: WorldPoint) -> float:
    dx = end[0] - start[0]
    dz = end[1] - start[1]
    length_squared = dx * dx + dz * dz
    if length_squared == 0:
        return _distance(point, start)
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dz) / length_squared
    ratio = min(1.0, max(0.0, projection))
    nearest = (start[0] + ratio * dx, start[1] + ratio * dz)
    return _distance(point, nearest)


def _turn_pixels(yaw_error: float, config: NavigationConfig) -> int:
    pixels = round(yaw_error * config.turn_gain * config.turn_px_per_degree)
    return max(-config.max_turn_px, min(config.max_turn_px, pixels))


def _check_turn_response(
    yaw_error: float,
    *,
    turning: bool,
    turn_sent: bool,
    now: float,
    config: NavigationConfig,
    state: _NavigationState,
) -> None:
    if not turning:
        state.turn_anchor_mono = None
        state.turn_anchor_error = None
        return
    if state.turn_anchor_error is None:
        if turn_sent:
            state.turn_anchor_mono = now
            state.turn_anchor_error = yaw_error
        return
    if yaw_error <= state.turn_anchor_error - config.turn_min_improvement_deg:
        state.turn_anchor_mono = now
        state.turn_anchor_error = yaw_error
        return
    if state.turn_anchor_mono is not None and now - state.turn_anchor_mono > (
        config.turn_response_timeout_sec
    ):
        raise RuntimeError(
            f"yaw input did not improve by {config.turn_min_improvement_deg:.1f} degrees "
            f"within {config.turn_response_timeout_sec:.1f}s"
        )


def _progress_stalled(
    state: _NavigationState,
    now: float,
    distance: float,
    config: NavigationConfig,
) -> bool:
    if state.progress_anchor_distance is None or state.progress_anchor_mono is None:
        _reset_progress_watchdog(state, now, distance)
        return False
    if distance <= state.progress_anchor_distance - config.progress_min_distance_blocks:
        _reset_progress_watchdog(state, now, distance)
        state.recoveries = 0
        return False
    return now - state.progress_anchor_mono > config.progress_timeout_sec


def _reset_progress_watchdog(
    state: _NavigationState,
    now: float,
    distance: float,
) -> None:
    state.progress_anchor_mono = now
    state.progress_anchor_distance = distance


def _is_stuck(
    samples: deque[tuple[float, float, float]],
    config: NavigationConfig,
    now: float,
) -> bool:
    while len(samples) > 1 and samples[1][0] <= now - config.stuck_window_sec:
        samples.popleft()
    if not samples or now - samples[0][0] < config.stuck_window_sec:
        return False
    displacement = _distance((samples[0][1], samples[0][2]), (samples[-1][1], samples[-1][2]))
    return displacement < config.stuck_distance_blocks


def _recover(
    controller: InputController,
    stop_event: StopEvent,
    config: NavigationConfig,
) -> None:
    controller.key_up("w")
    controller.tap("space")
    controller.key_down("s")
    _sleep_interruptible(config.recovery_hold_sec, stop_event)
    controller.key_up("s")


def _center(point: GridPoint, offset: float) -> WorldPoint:
    return point[0] + offset, point[1] + offset


def _distance(first: WorldPoint, second: WorldPoint) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _wait_for_start(start_event: StartEvent, stop_event: StopEvent) -> bool:
    while not stop_event.is_set():
        if start_event.wait(0.25):
            return True
    return False


def _sleep_interruptible(seconds: float, stop_event: StopEvent) -> None:
    end = time.monotonic() + max(0.0, seconds)
    while not stop_event.is_set():
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))
