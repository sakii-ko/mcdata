from __future__ import annotations

from typing import Any

from mcdata.action_placement import PLACEMENT_AIM_DURATION_SEC


def append_block_placement(
    events: list[dict[str, Any]],
    t: float,
    placement: Any,
    *,
    route_index: int,
    scan_pause_sec: float,
) -> float:
    if placement is None:
        return t
    if not isinstance(placement, dict):
        raise RuntimeError("waypoint block_placement must be a mapping")
    aim_dx = int(placement.get("aim_dx_px", 0))
    aim_dy = int(placement.get("aim_dy_px", 0))
    events.append(
        {
            "t": round(t, 3),
            "mouse_dx": aim_dx,
            "mouse_dy": aim_dy,
            "duration": PLACEMENT_AIM_DURATION_SEC,
            "placement_aim": True,
            "route_index": route_index,
        }
    )
    t += PLACEMENT_AIM_DURATION_SEC + scan_pause_sec
    input_duration = float(placement.get("input_duration_sec", 0))
    events.append(
        {
            "t": round(t, 3),
            "duration": round(input_duration, 3),
            "semantic_action": "deterministic_block_placement",
            "placement": dict(placement),
            "route_index": route_index,
        }
    )
    t += input_duration
    events.append(
        {
            "t": round(t, 3),
            "mouse_dx": -aim_dx,
            "mouse_dy": -aim_dy,
            "duration": PLACEMENT_AIM_DURATION_SEC,
            "placement_aim_restore": True,
            "route_index": route_index,
        }
    )
    return t + PLACEMENT_AIM_DURATION_SEC + scan_pause_sec


def append_deliberate_jump(
    events: list[dict[str, Any]],
    t: float,
    value: Any,
    *,
    route_index: int,
) -> None:
    if not isinstance(value, bool):
        raise RuntimeError("waypoint deliberate_jump must be a boolean")
    if value:
        events.append(
            {
                "t": round(t, 3),
                "key": "space",
                "action": "tap",
                "semantic_action": "deliberate_jump",
                "route_index": route_index,
            }
        )
