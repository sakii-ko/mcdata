from __future__ import annotations

import math
import re
import uuid
from pathlib import Path
from typing import Any

from mcdata.action_placement import (
    PlacementEvidenceError,
    validate_receipts,
    validate_server_log_binding,
)

COMBAT_SEMANTIC = "controlled_combat"
COMBAT_AIM_DURATION_SEC = 0.35
COMBAT_SCORE_OBJECTIVE = "mcdata_l4"
COMBAT_SCORE_SCALE = 100
COMBAT_KNOCKBACK_SCALE = 1000
COMBAT_RESET_PHASES = (
    "target_absent",
    "mob_spawning_disabled",
    "target_snapshot",
    "knockback_fixed",
    "weapon_ready",
)
COMBAT_FINAL_PHASES = ("target_damaged", "target_removed", "cleanup_complete")
COMBAT_SPEC_FIELDS = {
    "action_id",
    "target_entity",
    "target_tag",
    "target_uuid",
    "spawn",
    "rotation",
    "initial_health",
    "knockback_resistance",
    "weapon",
    "hotbar_slot",
    "item_count",
    "aim_dx_px",
    "aim_dy_px",
    "input_settle_sec",
    "attack_probe_delay_sec",
    "input_duration_sec",
    "receipt_timeout_sec",
}
CONTROLLED_TARGET = "minecraft:iron_golem"
_ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_TAG_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_RESOURCE_ID = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


class CombatEvidenceError(ValueError):
    """Raised when a controlled-combat plan or its evidence is invalid."""


def combat_spec(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or event.get("semantic_action") != COMBAT_SEMANTIC:
        raise CombatEvidenceError("event is not controlled combat")
    value = event.get("combat")
    if not isinstance(value, dict) or set(value) != COMBAT_SPEC_FIELDS:
        raise CombatEvidenceError("combat spec has an unstable field set")
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not _ACTION_ID.fullmatch(action_id):
        raise CombatEvidenceError("combat action_id is invalid")
    entity = value.get("target_entity")
    if entity != CONTROLLED_TARGET:
        raise CombatEvidenceError(
            "taxonomy v1 controlled combat requires an iron-golem sparring target"
        )
    target_tag = value.get("target_tag")
    if not isinstance(target_tag, str) or not _TAG_ID.fullmatch(target_tag):
        raise CombatEvidenceError("combat target_tag is invalid")
    target_uuid = value.get("target_uuid")
    try:
        parsed_uuid = uuid.UUID(str(target_uuid))
    except (ValueError, AttributeError) as exc:
        raise CombatEvidenceError("combat target_uuid is invalid") from exc
    if str(parsed_uuid) != target_uuid:
        raise CombatEvidenceError("combat target_uuid must use canonical lowercase form")
    _vector(value.get("spawn"), "spawn", length=3)
    rotation = _vector(value.get("rotation"), "rotation", length=2)
    if not -180.0 <= rotation[0] <= 180.0 or not -90.0 <= rotation[1] <= 90.0:
        raise CombatEvidenceError("combat rotation is outside Minecraft bounds")
    initial_health = _positive_number(value.get("initial_health"), "initial_health")
    if not math.isclose(initial_health, 20.0, rel_tol=0.0, abs_tol=1e-9):
        raise CombatEvidenceError("taxonomy v1 controlled combat requires initial_health 20.0")
    resistance = _positive_number(
        value.get("knockback_resistance"), "knockback_resistance"
    )
    if not math.isclose(resistance, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise CombatEvidenceError("controlled combat requires knockback_resistance 1.0")
    weapon = value.get("weapon")
    if not isinstance(weapon, str) or not _RESOURCE_ID.fullmatch(weapon):
        raise CombatEvidenceError("combat weapon is invalid")
    slot = value.get("hotbar_slot")
    if type(slot) is not int or not 1 <= slot <= 9:
        raise CombatEvidenceError("combat hotbar_slot must be an integer from 1 through 9")
    if value.get("item_count") != 1:
        raise CombatEvidenceError("controlled combat weapon item_count must be exactly one")
    for field in ("aim_dx_px", "aim_dy_px"):
        if type(value.get(field)) is not int:
            raise CombatEvidenceError(f"combat {field} must be an integer")
    if value["aim_dx_px"] == value["aim_dy_px"] == 0:
        raise CombatEvidenceError("combat aim must contain a real camera input")
    for field in (
        "input_settle_sec",
        "attack_probe_delay_sec",
        "input_duration_sec",
        "receipt_timeout_sec",
    ):
        _positive_number(value.get(field), field)
    if float(value["input_duration_sec"]) <= (
        float(value["input_settle_sec"]) + float(value["attack_probe_delay_sec"])
    ):
        raise CombatEvidenceError(
            "combat input_duration_sec must cover settle and attacker-probe delays"
        )
    return value


def combat_specs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        combat_spec(event)
        for event in events
        if event.get("semantic_action") == COMBAT_SEMANTIC
    ]
    if len(specs) > 1:
        raise CombatEvidenceError("taxonomy v1 permits exactly one controlled combat encounter")
    return specs


def validate_combat_event_sequences(events: list[dict[str, Any]]) -> None:
    combat_indices = [
        index
        for index, event in enumerate(events)
        if event.get("semantic_action") == COMBAT_SEMANTIC
    ]
    expected_aim_indices: set[int] = set()
    expected_restore_indices: set[int] = set()
    for index in combat_indices:
        if index == 0 or index + 1 >= len(events):
            raise CombatEvidenceError(
                "combat must be immediately surrounded by camera aim and restore events"
            )
        event = events[index]
        spec = combat_spec(event)
        route_index = event.get("route_index")
        if type(route_index) is not int or route_index < 0:
            raise CombatEvidenceError("combat route_index must be a non-negative integer")
        if set(event) != {"t", "duration", "semantic_action", "combat", "route_index"}:
            raise CombatEvidenceError("combat event has an unstable field set")
        if not _finite_number(event.get("t")):
            raise CombatEvidenceError("combat event timestamp must be finite")
        expected_aim_indices.add(index - 1)
        expected_restore_indices.add(index + 1)
        _validate_camera_event(
            events[index - 1],
            tag="combat_aim",
            route_index=route_index,
            mouse_dx=int(spec["aim_dx_px"]),
            mouse_dy=int(spec["aim_dy_px"]),
        )
        _validate_camera_event(
            events[index + 1],
            tag="combat_aim_restore",
            route_index=route_index,
            mouse_dx=-int(spec["aim_dx_px"]),
            mouse_dy=-int(spec["aim_dy_px"]),
        )
        if not _same_number(event.get("duration"), spec["input_duration_sec"]):
            raise CombatEvidenceError(
                "combat event duration does not match input_duration_sec"
            )
        if not _same_number(
            events[index + 1].get("t"),
            float(event["t"]) + float(spec["input_duration_sec"]),
        ):
            raise CombatEvidenceError(
                "combat restore timestamp does not follow the declared input duration"
            )
    actual_aim_indices = {
        index for index, event in enumerate(events) if "combat_aim" in event
    }
    actual_restore_indices = {
        index for index, event in enumerate(events) if "combat_aim_restore" in event
    }
    if actual_aim_indices != expected_aim_indices or actual_restore_indices != expected_restore_indices:
        raise CombatEvidenceError("combat camera aim/restore event is unbound or missing")


def expected_combat_input_events(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": str(spec["hotbar_slot"]), "action": "tap"},
        {"mouse_button": 1, "action": "click"},
    ]


def validate_combat_input_evidence(
    value: Any,
    event: dict[str, Any],
    *,
    replay_log_path: Path,
) -> None:
    spec = combat_spec(event)
    fields = {
        "kind",
        "action_id",
        "target_entity",
        "target_tag",
        "target_uuid",
        "spawn",
        "weapon",
        "hotbar_slot",
        "input_events",
        "probe_command_count",
        "attacker_receipt",
        "attacker_score_query",
        "server_log",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CombatEvidenceError("combat input evidence has an unstable field set")
    projection = {
        key: spec[key]
        for key in (
            "action_id",
            "target_entity",
            "target_tag",
            "target_uuid",
            "spawn",
            "weapon",
            "hotbar_slot",
        )
    }
    if (
        value.get("kind") != "controlled_combat_input"
        or {key: value.get(key) for key in projection} != projection
        or value.get("input_events") != expected_combat_input_events(spec)
    ):
        raise CombatEvidenceError("combat input dispatch evidence does not match trajectory")
    queries = _validate_score_queries(
        [value.get("attacker_score_query")],
        [("#attacker_ok", 1)],
    )
    receipts = _wrapped_evidence_call(
        validate_receipts,
        [value.get("attacker_receipt")],
        str(spec["action_id"]),
        ["player_attacker"],
    )
    _validate_probe_count(
        value.get("probe_command_count"),
        receipts,
        queries,
        fixed_commands=2,
    )
    _wrapped_evidence_call(
        validate_server_log_binding,
        value.get("server_log"),
        [*receipts, *queries],
        replay_log_path=replay_log_path,
    )


def validate_combat_reset_evidence(
    value: Any,
    combat_events: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    spec = _one_combat_spec(combat_events)
    fields = {
        "kind",
        "action_id",
        "target_entity",
        "target_tag",
        "target_uuid",
        "spawn",
        "rotation",
        "weapon",
        "hotbar_slot",
        "initial_health_score",
        "knockback_score",
        "mob_spawning_score",
        "reset_command_count",
        "probe_command_count",
        "objective_created",
        "receipts",
        "score_queries",
        "server_log",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CombatEvidenceError("L4 combat reset evidence has an unstable field set")
    _validate_projection(value, spec)
    if value.get("kind") != "l4_combat_reset" or value.get("reset_command_count") != 4:
        raise CombatEvidenceError("L4 combat reset command count is invalid")
    initial_score = health_score(spec)
    if value.get("initial_health_score") != initial_score:
        raise CombatEvidenceError("L4 combat initial-health snapshot is invalid")
    if value.get("knockback_score") != COMBAT_KNOCKBACK_SCALE:
        raise CombatEvidenceError("L4 combat knockback snapshot is invalid")
    if value.get("mob_spawning_score") != 0:
        raise CombatEvidenceError("L4 combat mob-spawning snapshot is invalid")
    queries = _validate_score_queries(
        value.get("score_queries"),
        [
            ("#target_count", 1),
            ("#health_before", initial_score),
            ("#knockback", COMBAT_KNOCKBACK_SCALE),
            ("#mob_spawning", 0),
        ],
    )
    receipts = _wrapped_evidence_call(
        validate_receipts,
        value.get("receipts"),
        str(spec["action_id"]),
        list(COMBAT_RESET_PHASES),
    )
    _validate_probe_count(
        value.get("probe_command_count"),
        receipts,
        queries,
        fixed_commands=4,
    )
    objective_created = _validate_objective_mutation(
        value.get("objective_created"), "Created new objective"
    )
    _wrapped_evidence_call(
        validate_server_log_binding,
        value.get("server_log"),
        [objective_created, *receipts, *queries],
        replay_log_path=replay_log_path,
    )


def validate_combat_post_capture_evidence(
    value: Any,
    combat_events: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    spec = _one_combat_spec(combat_events)
    fields = {
        "kind",
        "action_id",
        "target_entity",
        "target_tag",
        "target_uuid",
        "spawn",
        "rotation",
        "weapon",
        "hotbar_slot",
        "initial_health_score",
        "remaining_health_score",
        "probe_command_count",
        "cleanup_command_count",
        "objective_removed",
        "receipts",
        "score_queries",
        "server_log",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise CombatEvidenceError("L4 combat post-capture evidence has an unstable field set")
    _validate_projection(value, spec)
    if value.get("kind") != "l4_combat_post_capture_verification":
        raise CombatEvidenceError("L4 combat post-capture kind is invalid")
    initial_score = health_score(spec)
    remaining = value.get("remaining_health_score")
    if (
        value.get("initial_health_score") != initial_score
        or type(remaining) is not int
        or not 0 < remaining < initial_score
    ):
        raise CombatEvidenceError("L4 combat evidence does not prove health decrease")
    if value.get("cleanup_command_count") != 4:
        raise CombatEvidenceError("L4 combat cleanup command count is invalid")
    queries = _validate_score_queries(
        value.get("score_queries"),
        [
            ("#target_count_after", 1),
            ("#health_after", remaining),
            ("#spawn_mobs_final", 0),
        ],
    )
    receipts = _wrapped_evidence_call(
        validate_receipts,
        value.get("receipts"),
        str(spec["action_id"]),
        list(COMBAT_FINAL_PHASES),
    )
    _validate_probe_count(
        value.get("probe_command_count"),
        receipts,
        queries,
        fixed_commands=3,
    )
    objective_removed = _validate_objective_mutation(
        value.get("objective_removed"), "Removed objective"
    )
    _wrapped_evidence_call(
        validate_server_log_binding,
        value.get("server_log"),
        [*receipts, *queries, objective_removed],
        replay_log_path=replay_log_path,
    )


def health_score(spec: dict[str, Any]) -> int:
    return round(float(spec["initial_health"]) * COMBAT_SCORE_SCALE)


def uuid_int_array(value: str) -> tuple[int, int, int, int]:
    raw = uuid.UUID(value).int
    result = []
    for shift in (96, 64, 32, 0):
        item = (raw >> shift) & 0xFFFFFFFF
        result.append(item - 0x100000000 if item >= 0x80000000 else item)
    return result[0], result[1], result[2], result[3]


def _one_combat_spec(events: list[dict[str, Any]]) -> dict[str, Any]:
    specs = combat_specs(events)
    if len(specs) != 1:
        raise CombatEvidenceError("L4 evidence requires exactly one combat encounter")
    return specs[0]


def _validate_projection(value: dict[str, Any], spec: dict[str, Any]) -> None:
    keys = (
        "action_id",
        "target_entity",
        "target_tag",
        "target_uuid",
        "spawn",
        "rotation",
        "weapon",
        "hotbar_slot",
    )
    if {key: value.get(key) for key in keys} != {key: spec[key] for key in keys}:
        raise CombatEvidenceError("L4 combat evidence does not match trajectory")


def _validate_score_queries(
    value: Any,
    expected: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(expected):
        raise CombatEvidenceError("combat score-query count is invalid")
    for query, (holder, score) in zip(value, expected, strict=True):
        if not isinstance(query, dict) or set(query) != {
            "holder",
            "objective",
            "value",
            "line",
            "probe_attempts",
        }:
            raise CombatEvidenceError("combat score query has an unstable field set")
        line = query.get("line")
        if (
            query.get("holder") != holder
            or query.get("objective") != COMBAT_SCORE_OBJECTIVE
            or query.get("value") != score
            or type(query.get("probe_attempts")) is not int
            or query["probe_attempts"] <= 0
            or not isinstance(line, str)
            or f"{holder} has {score} [{COMBAT_SCORE_OBJECTIVE}]" not in line
        ):
            raise CombatEvidenceError("combat score query is invalid")
    return value


def _validate_objective_mutation(value: Any, prefix: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"objective", "line"}:
        raise CombatEvidenceError("combat objective mutation has an unstable field set")
    line = value.get("line")
    if (
        value.get("objective") != COMBAT_SCORE_OBJECTIVE
        or not isinstance(line, str)
        or f"{prefix} [{COMBAT_SCORE_OBJECTIVE}]" not in line
    ):
        raise CombatEvidenceError("combat objective mutation evidence is invalid")
    return value


def _validate_probe_count(
    value: Any,
    receipts: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    fixed_commands: int = 0,
) -> None:
    minimum = (
        fixed_commands
        + len(receipts)
        + sum(int(item["probe_attempts"]) for item in queries)
    )
    if type(value) is not int or value < minimum:
        raise CombatEvidenceError("combat probe command count is invalid")


def _wrapped_evidence_call(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except PlacementEvidenceError as exc:
        raise CombatEvidenceError(str(exc).replace("placement", "combat")) from exc


def _validate_camera_event(
    value: Any,
    *,
    tag: str,
    route_index: int,
    mouse_dx: int,
    mouse_dy: int,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "t",
        "mouse_dx",
        "mouse_dy",
        "duration",
        tag,
        "route_index",
    }:
        raise CombatEvidenceError(f"combat {tag} event has an unstable field set")
    if (
        value.get(tag) is not True
        or value.get("route_index") != route_index
        or value.get("mouse_dx") != mouse_dx
        or value.get("mouse_dy") != mouse_dy
        or not _same_number(value.get("duration"), COMBAT_AIM_DURATION_SEC)
        or not _finite_number(value.get("t"))
    ):
        raise CombatEvidenceError(f"combat {tag} event does not match its combat spec")


def _vector(value: Any, label: str, *, length: int) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length or any(
        not _finite_number(item) for item in value
    ):
        raise CombatEvidenceError(f"combat {label} must contain {length} finite numbers")
    return tuple(float(item) for item in value)


def _positive_number(value: Any, label: str) -> float:
    if not _finite_number(value) or float(value) <= 0:
        raise CombatEvidenceError(f"combat {label} must be finite and positive")
    return float(value)


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _same_number(left: Any, right: Any) -> bool:
    return _finite_number(left) and _finite_number(right) and math.isclose(
        float(left), float(right), rel_tol=0.0, abs_tol=1e-9
    )
