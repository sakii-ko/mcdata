from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

PLACEMENT_SEMANTIC = "deterministic_block_placement"
PLACEMENT_AIM_DURATION_SEC = 0.35
PLACEMENT_RECEIPT_PHASES = ("block_placed", "cleanup_complete")
EPISODE_RESET_BASE_PHASES = (
    "inventory_empty",
    "dropped_items_empty",
    "non_player_entities_empty",
)
FACE_OFFSETS = {
    "down": (0, -1, 0),
    "up": (0, 1, 0),
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
}
PLACEMENT_SPEC_FIELDS = {
    "action_id",
    "block",
    "support_block",
    "hotbar_slot",
    "item_count",
    "target",
    "support",
    "face",
    "aim_dx_px",
    "aim_dy_px",
    "input_settle_sec",
    "input_duration_sec",
    "receipt_timeout_sec",
}
_ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_PHASE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
_BLOCK_ID = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_SERVER_RECEIPT_PREFIX = "[Server] "


class PlacementEvidenceError(ValueError):
    """Raised when a placement plan or its server-bound evidence is invalid."""


def placement_spec(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or event.get("semantic_action") != PLACEMENT_SEMANTIC:
        raise PlacementEvidenceError("event is not a deterministic block placement")
    value = event.get("placement")
    if not isinstance(value, dict) or set(value) != PLACEMENT_SPEC_FIELDS:
        raise PlacementEvidenceError("placement spec has an unstable field set")
    action_id = value.get("action_id")
    if not isinstance(action_id, str) or not _ACTION_ID.fullmatch(action_id):
        raise PlacementEvidenceError("placement action_id is invalid")
    for field in ("block", "support_block"):
        block = value.get(field)
        if not isinstance(block, str) or not _BLOCK_ID.fullmatch(block) or block == "minecraft:air":
            raise PlacementEvidenceError(f"placement {field} is invalid")
    slot = value.get("hotbar_slot")
    if type(slot) is not int or not 1 <= slot <= 9:
        raise PlacementEvidenceError("placement hotbar_slot must be an integer from 1 through 9")
    count = value.get("item_count")
    if type(count) is not int or not 2 <= count <= 64:
        raise PlacementEvidenceError("placement item_count must be an integer from 2 through 64")
    target = _coordinate(value.get("target"), "target")
    support = _coordinate(value.get("support"), "support")
    face = value.get("face")
    if face not in FACE_OFFSETS:
        raise PlacementEvidenceError("placement face is invalid")
    offset = FACE_OFFSETS[face]
    if target != tuple(left + right for left, right in zip(support, offset, strict=True)):
        raise PlacementEvidenceError(
            "placement target is not adjacent to the declared support face"
        )
    for field in ("aim_dx_px", "aim_dy_px"):
        if type(value.get(field)) is not int:
            raise PlacementEvidenceError(f"placement {field} must be an integer")
    if value["aim_dx_px"] == value["aim_dy_px"] == 0:
        raise PlacementEvidenceError("placement aim must contain a real camera input")
    for field in ("input_settle_sec", "input_duration_sec", "receipt_timeout_sec"):
        number = value.get(field)
        if (
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or not math.isfinite(float(number))
            or float(number) <= 0
        ):
            raise PlacementEvidenceError(f"placement {field} must be finite and positive")
    return value


def placement_specs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    specs = [
        placement_spec(event)
        for event in events
        if event.get("semantic_action") == PLACEMENT_SEMANTIC
    ]
    action_ids = [str(spec["action_id"]) for spec in specs]
    slots = [int(spec["hotbar_slot"]) for spec in specs]
    blocks = [str(spec["block"]) for spec in specs]
    occupied = [tuple(spec[key]) for spec in specs for key in ("target", "support")]
    if len(action_ids) != len(set(action_ids)):
        raise PlacementEvidenceError("placement action_ids must be unique within an episode")
    if len(slots) != len(set(slots)):
        raise PlacementEvidenceError("placement hotbar slots must be unique within an episode")
    if len(blocks) != len(set(blocks)):
        raise PlacementEvidenceError("placement blocks must be unique within an episode")
    if len(occupied) != len(set(occupied)):
        raise PlacementEvidenceError("placement arena coordinates must be disjoint")
    return specs


def validate_placement_event_sequences(events: list[dict[str, Any]]) -> None:
    """Bind every placement to one exact camera aim and its immediate inverse."""
    placement_indices = [
        index
        for index, event in enumerate(events)
        if event.get("semantic_action") == PLACEMENT_SEMANTIC
    ]
    expected_aim_indices: set[int] = set()
    expected_restore_indices: set[int] = set()
    for index in placement_indices:
        if index == 0 or index + 1 >= len(events):
            raise PlacementEvidenceError(
                "placement must be immediately surrounded by camera aim and restore events"
            )
        event = events[index]
        spec = placement_spec(event)
        aim = events[index - 1]
        restore = events[index + 1]
        expected_aim_indices.add(index - 1)
        expected_restore_indices.add(index + 1)
        route_index = event.get("route_index")
        if type(route_index) is not int or route_index < 0:
            raise PlacementEvidenceError("placement route_index must be a non-negative integer")
        if set(event) != {
            "t",
            "duration",
            "semantic_action",
            "placement",
            "route_index",
        }:
            raise PlacementEvidenceError("placement event has an unstable field set")
        if not _finite_number(event.get("t")):
            raise PlacementEvidenceError("placement event timestamp must be finite")
        _validate_camera_event(
            aim,
            tag="placement_aim",
            route_index=route_index,
            mouse_dx=int(spec["aim_dx_px"]),
            mouse_dy=int(spec["aim_dy_px"]),
        )
        _validate_camera_event(
            restore,
            tag="placement_aim_restore",
            route_index=route_index,
            mouse_dx=-int(spec["aim_dx_px"]),
            mouse_dy=-int(spec["aim_dy_px"]),
        )
        if not _same_number(event.get("duration"), spec["input_duration_sec"]):
            raise PlacementEvidenceError(
                "placement event duration does not match input_duration_sec"
            )
        if not _same_number(
            restore.get("t"),
            float(event["t"]) + float(spec["input_duration_sec"]),
        ):
            raise PlacementEvidenceError(
                "placement restore timestamp does not follow the declared input duration"
            )

    actual_aim_indices = {
        index for index, event in enumerate(events) if "placement_aim" in event
    }
    actual_restore_indices = {
        index for index, event in enumerate(events) if "placement_aim_restore" in event
    }
    if actual_aim_indices != expected_aim_indices or actual_restore_indices != expected_restore_indices:
        raise PlacementEvidenceError("placement camera aim/restore event is unbound or missing")


def expected_input_events(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": str(spec["hotbar_slot"]), "action": "tap"},
        {"mouse_button": 3, "action": "click"},
    ]


def receipt_marker(action_id: str, phase: str) -> str:
    if not _ACTION_ID.fullmatch(action_id):
        raise PlacementEvidenceError("placement receipt action_id is invalid")
    if not _PHASE_ID.fullmatch(phase):
        raise PlacementEvidenceError("placement receipt phase is invalid")
    return f"[MCDATA_ACTION_RECEIPT:{action_id}:{phase}]"


def validate_episode_reset_evidence(
    value: Any,
    placement_events: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    specs = placement_specs(placement_events)
    action_ids = [str(spec["action_id"]) for spec in specs]
    expected_phases = [
        *EPISODE_RESET_BASE_PHASES,
        *(f"arena_{action_id}" for action_id in action_ids),
        *(f"inventory_{action_id}" for action_id in action_ids),
    ]
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "action_ids",
        "reset_command_count",
        "probe_command_count",
        "receipts",
        "server_log",
    }:
        raise PlacementEvidenceError("L3 episode reset evidence has an unstable field set")
    if value.get("kind") != "l3_episode_reset" or value.get("action_ids") != action_ids:
        raise PlacementEvidenceError("L3 episode reset action set does not match trajectory")
    if value.get("reset_command_count") != 3 + 3 * len(specs):
        raise PlacementEvidenceError("L3 episode reset command count is invalid")
    _validate_receipts(value.get("receipts"), "episode_reset", expected_phases)
    probe_count = value.get("probe_command_count")
    if type(probe_count) is not int or probe_count < len(expected_phases):
        raise PlacementEvidenceError("L3 episode reset probe command count is invalid")
    _validate_server_log_binding(
        value.get("server_log"), value["receipts"], replay_log_path=replay_log_path
    )


def validate_placement_input_evidence(value: Any, event: dict[str, Any]) -> None:
    spec = placement_spec(event)
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "action_id",
        "block",
        "hotbar_slot",
        "target",
        "support",
        "face",
        "input_events",
    }:
        raise PlacementEvidenceError("placement input evidence has an unstable field set")
    projection = {
        key: spec[key] for key in ("action_id", "block", "hotbar_slot", "target", "support", "face")
    }
    if (
        value.get("kind") != "deterministic_block_placement_input"
        or {key: value.get(key) for key in projection} != projection
    ):
        raise PlacementEvidenceError("placement input evidence does not match trajectory")
    if value.get("input_events") != expected_input_events(spec):
        raise PlacementEvidenceError("placement input dispatch evidence is invalid")


def validate_post_capture_evidence(
    value: Any,
    placement_events: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    specs = placement_specs(placement_events)
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "action_ids",
        "probe_command_count",
        "cleanup_command_count",
        "placements",
        "server_log",
    }:
        raise PlacementEvidenceError("L3 post-capture evidence has an unstable field set")
    action_ids = [str(spec["action_id"]) for spec in specs]
    if value.get("kind") != "l3_post_capture_verification" or value.get("action_ids") != action_ids:
        raise PlacementEvidenceError("L3 post-capture action set does not match trajectory")
    if value.get("cleanup_command_count") != 3 * len(specs):
        raise PlacementEvidenceError("L3 post-capture cleanup command count is invalid")
    probe_count = value.get("probe_command_count")
    if type(probe_count) is not int or probe_count < 2 * len(specs):
        raise PlacementEvidenceError("L3 post-capture probe command count is invalid")
    placements = value.get("placements")
    if not isinstance(placements, list) or len(placements) != len(specs):
        raise PlacementEvidenceError("L3 post-capture placement count is invalid")
    all_receipts: list[dict[str, Any]] = []
    for observed, spec in zip(placements, specs, strict=True):
        if not isinstance(observed, dict) or set(observed) != {
            "action_id",
            "block",
            "target",
            "support",
            "face",
            "receipts",
        }:
            raise PlacementEvidenceError("post-capture placement has an unstable field set")
        projection = {key: spec[key] for key in ("action_id", "block", "target", "support", "face")}
        if {key: observed.get(key) for key in projection} != projection:
            raise PlacementEvidenceError("post-capture placement does not match trajectory")
        _validate_receipts(
            observed.get("receipts"), str(spec["action_id"]), list(PLACEMENT_RECEIPT_PHASES)
        )
        all_receipts.extend(observed["receipts"])
    _validate_server_log_binding(
        value.get("server_log"), all_receipts, replay_log_path=replay_log_path
    )


def server_log_binding(log_path: Path) -> dict[str, Any]:
    try:
        payload = log_path.read_bytes()
    except OSError as exc:
        raise PlacementEvidenceError(f"could not bind placement server log: {log_path}") from exc
    return {
        "path": log_path.name,
        "prefix_size_bytes": len(payload),
        "prefix_sha256": hashlib.sha256(payload).hexdigest(),
    }


def validate_receipts(
    value: Any,
    action_id: str,
    phases: list[str],
) -> list[dict[str, Any]]:
    """Validate exact server say receipts and return the narrowed list."""
    _validate_receipts(value, action_id, phases)
    return value


def validate_server_log_binding(
    value: Any,
    receipts: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    """Validate a bound server-log prefix and every evidence line inside it."""
    _validate_server_log_binding(
        value,
        receipts,
        replay_log_path=replay_log_path,
    )


def _validate_receipts(value: Any, action_id: str, phases: list[str]) -> None:
    if not isinstance(value, list) or len(value) != len(phases):
        raise PlacementEvidenceError("placement receipt count is invalid")
    for receipt, phase in zip(value, phases, strict=True):
        marker = receipt_marker(action_id, phase)
        if not isinstance(receipt, dict) or set(receipt) != {"phase", "marker", "line"}:
            raise PlacementEvidenceError("placement receipt has an unstable field set")
        line = receipt.get("line")
        if receipt.get("phase") != phase or receipt.get("marker") != marker:
            raise PlacementEvidenceError("placement receipt phase/marker is invalid")
        if not isinstance(line, str) or f"{_SERVER_RECEIPT_PREFIX}{marker}" not in line:
            raise PlacementEvidenceError("placement receipt line is not a server say success line")


def _validate_server_log_binding(
    value: Any,
    receipts: list[dict[str, Any]],
    *,
    replay_log_path: Path,
) -> None:
    if not isinstance(value, dict) or set(value) != {
        "path",
        "prefix_size_bytes",
        "prefix_sha256",
    }:
        raise PlacementEvidenceError("placement server-log binding has an unstable field set")
    if value.get("path") != "server.log":
        raise PlacementEvidenceError("placement server-log binding must name sibling server.log")
    size = value.get("prefix_size_bytes")
    digest = value.get("prefix_sha256")
    if type(size) is not int or size <= 0 or not _is_sha256(digest):
        raise PlacementEvidenceError("placement server-log prefix binding is invalid")
    log_path = replay_log_path.with_name("server.log")
    if log_path.is_symlink() or not log_path.is_file():
        raise PlacementEvidenceError("placement server log is missing or unsafe")
    with log_path.open("rb") as handle:
        payload = handle.read(size)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise PlacementEvidenceError("placement server-log prefix hash does not match")
    text = payload.decode("utf-8", errors="replace")
    if any(receipt["line"] not in text for receipt in receipts):
        raise PlacementEvidenceError("placement receipt line is absent from bound server log")


def _coordinate(value: Any, label: str) -> tuple[int, int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(item) is not int for item in value)
    ):
        raise PlacementEvidenceError(f"placement {label} must contain exactly three integers")
    return value[0], value[1], value[2]


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
        raise PlacementEvidenceError(f"placement {tag} event has an unstable field set")
    if (
        value.get(tag) is not True
        or value.get("route_index") != route_index
        or value.get("mouse_dx") != mouse_dx
        or value.get("mouse_dy") != mouse_dy
        or not _same_number(value.get("duration"), PLACEMENT_AIM_DURATION_SEC)
        or not _finite_number(value.get("t"))
    ):
        raise PlacementEvidenceError(f"placement {tag} event does not match its placement spec")


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


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
