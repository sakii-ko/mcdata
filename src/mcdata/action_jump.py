from __future__ import annotations

import math
import re
from typing import Any

DELIBERATE_JUMP_SEMANTIC = "deliberate_jump"
DELIBERATE_JUMP_HOLD_SEC = 0.16
MIN_JUMP_HOLD_SEC = 0.12
MAX_JUMP_HOLD_SEC = 0.18
MIN_RUNNING_MARGIN_SEC = 0.30

_JUMP_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_JUMP_EVENT_FIELDS = {
    "t",
    "key",
    "action",
    "semantic_action",
    "semantic_phase",
    "jump_id",
    "route_index",
    "hold_duration_sec",
}


class JumpEvidenceError(ValueError):
    """Raised when a deliberate-jump plan is not a complete running key hold."""


def append_running_jump(
    events: list[dict[str, Any]],
    press_t: float,
    *,
    jump_id: str,
    route_index: int,
    hold_duration_sec: float = DELIBERATE_JUMP_HOLD_SEC,
) -> None:
    hold = float(hold_duration_sec)
    common = {
        "key": "space",
        "semantic_action": DELIBERATE_JUMP_SEMANTIC,
        "jump_id": str(jump_id),
        "route_index": route_index,
        "hold_duration_sec": round(hold, 3),
    }
    events.append(
        {
            "t": round(press_t, 3),
            "action": "down",
            "semantic_phase": "press",
            **common,
        }
    )
    events.append(
        {
            "t": round(press_t + hold, 3),
            "action": "up",
            "semantic_phase": "release",
            **common,
        }
    )


def running_jump_plan(
    route: list[tuple[int, int]], spec: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    jump_ids: set[str] = set()
    for raw in spec.get("running_jumps", []) or []:
        if not isinstance(raw, dict) or set(raw) != {"at", "jump_id", "hold_duration_sec"}:
            raise RuntimeError(
                "running_jumps entries must contain exactly at, jump_id, hold_duration_sec"
            )
        point = int(raw["at"][0]), int(raw["at"][1])
        indices = [index for index, route_point in enumerate(route) if route_point == point]
        if len(indices) != 1:
            raise RuntimeError(
                f"running jump point {point} must occur exactly once on the route"
            )
        route_index = indices[0]
        jump_id = str(raw["jump_id"])
        if jump_id in jump_ids:
            raise RuntimeError(f"duplicate running jump_id: {jump_id}")
        if route_index in result:
            raise RuntimeError(f"duplicate running jump route point: {point}")
        jump_ids.add(jump_id)
        result[route_index] = {
            "jump_id": jump_id,
            "hold_duration_sec": float(raw["hold_duration_sec"]),
        }
    return result


def append_forward_run_with_jumps(
    events: list[dict[str, Any]],
    t: float,
    *,
    duration: float,
    seconds_per_block: float,
    walk_startup_comp_sec: float,
    segment_start: int,
    segment_end: int,
    running_jumps: dict[int, dict[str, Any]],
) -> float:
    events.append({"t": round(t, 3), "key": "w", "action": "down"})
    for route_index in sorted(running_jumps):
        if not segment_start < route_index < segment_end:
            continue
        jump = running_jumps[route_index]
        offset_blocks = route_index - segment_start
        press_t = t + walk_startup_comp_sec + offset_blocks * seconds_per_block
        append_running_jump(
            events,
            press_t,
            jump_id=str(jump["jump_id"]),
            route_index=route_index,
            hold_duration_sec=float(jump["hold_duration_sec"]),
        )
    events.append({"t": round(t + duration, 3), "key": "w", "action": "up"})
    return t + duration


def validate_all_running_jumps_scheduled(
    events: list[dict[str, Any]], running_jumps: dict[int, dict[str, Any]]
) -> None:
    scheduled = {
        str(event["jump_id"])
        for event in events
        if event.get("semantic_action") == DELIBERATE_JUMP_SEMANTIC
        and event.get("semantic_phase") == "press"
    }
    configured = {str(item["jump_id"]) for item in running_jumps.values()}
    if scheduled != configured:
        raise RuntimeError(
            "every running jump must be inside a non-stopping straight route segment"
        )


def validate_deliberate_jump_sequences(events: list[dict[str, Any]]) -> int:
    """Validate exact press/release pairs and prove that W spans each full hold."""
    jump_events = [
        (index, event)
        for index, event in enumerate(events)
        if event.get("semantic_action") == DELIBERATE_JUMP_SEMANTIC
    ]
    if len(jump_events) % 2:
        raise JumpEvidenceError("deliberate jump is missing a press or release event")

    jump_ids: set[str] = set()
    for pair_index in range(0, len(jump_events), 2):
        press_index, press = jump_events[pair_index]
        release_index, release = jump_events[pair_index + 1]
        _validate_jump_event(press, phase="press", action="down")
        _validate_jump_event(release, phase="release", action="up")
        jump_id = str(press["jump_id"])
        if jump_id in jump_ids:
            raise JumpEvidenceError("deliberate jump_id values must be unique")
        jump_ids.add(jump_id)
        for field in ("jump_id", "route_index", "hold_duration_sec"):
            if release[field] != press[field]:
                raise JumpEvidenceError(
                    "deliberate jump release does not match its press event"
                )
        hold = float(press["hold_duration_sec"])
        if not math.isclose(
            float(release["t"]) - float(press["t"]),
            hold,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise JumpEvidenceError(
                "deliberate jump release timestamp does not match hold_duration_sec"
            )
        if any(
            event.get("key") == "space"
            for event in events[press_index + 1 : release_index]
        ):
            raise JumpEvidenceError(
                "deliberate jump press/release contains an unbound Space input"
            )
        _validate_running_span(
            events,
            press_index=press_index,
            release_index=release_index,
        )
    return len(jump_ids)


def _validate_jump_event(event: dict[str, Any], *, phase: str, action: str) -> None:
    if set(event) != _JUMP_EVENT_FIELDS:
        raise JumpEvidenceError("deliberate jump event has an unstable field set")
    if (
        event.get("key") != "space"
        or event.get("action") != action
        or event.get("semantic_phase") != phase
    ):
        raise JumpEvidenceError(
            "deliberate jump must be an explicit Space down/hold/up sequence"
        )
    jump_id = event.get("jump_id")
    if not isinstance(jump_id, str) or not _JUMP_ID.fullmatch(jump_id):
        raise JumpEvidenceError("deliberate jump_id is invalid")
    route_index = event.get("route_index")
    if type(route_index) is not int or route_index < 0:
        raise JumpEvidenceError("deliberate jump route_index must be non-negative")
    for field in ("t", "hold_duration_sec"):
        value = event.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise JumpEvidenceError(f"deliberate jump {field} must be finite")
    hold = float(event["hold_duration_sec"])
    if not MIN_JUMP_HOLD_SEC <= hold <= MAX_JUMP_HOLD_SEC:
        raise JumpEvidenceError(
            f"deliberate jump hold must be {MIN_JUMP_HOLD_SEC:.2f}–"
            f"{MAX_JUMP_HOLD_SEC:.2f} seconds"
        )


def _validate_running_span(
    events: list[dict[str, Any]],
    *,
    press_index: int,
    release_index: int,
) -> None:
    press_t = float(events[press_index]["t"])
    release_t = float(events[release_index]["t"])
    forward_down_t: float | None = None
    forward_up_t: float | None = None
    forward_held = False
    for index, event in enumerate(events):
        if event.get("key") != "w":
            continue
        action = event.get("action")
        if action == "down":
            forward_held = True
            forward_down_t = float(event.get("t", 0))
            forward_up_t = None
        elif action == "up" and forward_held:
            if index < press_index:
                forward_held = False
                forward_down_t = None
                continue
            forward_up_t = float(event.get("t", 0))
            break
    if forward_down_t is None or forward_up_t is None:
        raise JumpEvidenceError("deliberate jump is not contained in a forward run")
    if (
        press_t - forward_down_t < MIN_RUNNING_MARGIN_SEC
        or forward_up_t - release_t < MIN_RUNNING_MARGIN_SEC
    ):
        raise JumpEvidenceError(
            "deliberate jump needs running lead-in and landing clearance"
        )
