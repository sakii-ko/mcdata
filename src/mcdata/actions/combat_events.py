from __future__ import annotations

from typing import Any

from mcdata.action_combat import COMBAT_AIM_DURATION_SEC


def append_controlled_combat(
    events: list[dict[str, Any]],
    t: float,
    combat: Any,
    *,
    route_index: int,
    scan_pause_sec: float,
) -> float:
    if combat is None:
        return t
    if not isinstance(combat, dict):
        raise RuntimeError("waypoint controlled_combat must be a mapping")
    aim_dx = int(combat.get("aim_dx_px", 0))
    aim_dy = int(combat.get("aim_dy_px", 0))
    events.append(
        {
            "t": round(t, 3),
            "mouse_dx": aim_dx,
            "mouse_dy": aim_dy,
            "duration": COMBAT_AIM_DURATION_SEC,
            "combat_aim": True,
            "route_index": route_index,
        }
    )
    t += COMBAT_AIM_DURATION_SEC + scan_pause_sec
    input_duration = float(combat.get("input_duration_sec", 0))
    events.append(
        {
            "t": round(t, 3),
            "duration": round(input_duration, 3),
            "semantic_action": "controlled_combat",
            "combat": dict(combat),
            "route_index": route_index,
        }
    )
    t += input_duration
    events.append(
        {
            "t": round(t, 3),
            "mouse_dx": -aim_dx,
            "mouse_dy": -aim_dy,
            "duration": COMBAT_AIM_DURATION_SEC,
            "combat_aim_restore": True,
            "route_index": route_index,
        }
    )
    return t + COMBAT_AIM_DURATION_SEC + scan_pause_sec
