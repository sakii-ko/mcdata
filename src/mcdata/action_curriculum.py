from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

TAXONOMY_VERSION = 1

CAPABILITIES_BY_LEVEL: dict[int, tuple[str, ...]] = {
    1: ("navigation",),
    2: ("navigation", "deliberate_jump"),
    3: ("navigation", "deliberate_jump", "deterministic_block_placement"),
    4: (
        "navigation",
        "deliberate_jump",
        "deterministic_block_placement",
        "controlled_combat",
    ),
}
BUCKET_BY_LEVEL = {
    1: "l1",
    2: "l1_l2",
    3: "l1_l2_l3",
    4: "l1_l2_l3_l4",
}
SEMANTIC_ACTION_LEVEL = {
    "navigation_move": 1,
    "navigation_camera": 1,
    "deliberate_jump": 2,
    "deterministic_block_placement": 3,
    "controlled_combat": 4,
}
SEMANTIC_ACTIONS = tuple(SEMANTIC_ACTION_LEVEL)
RECOVERY_ACTIONS = ("attempts", "jump_taps", "reverse_moves")


class ActionCurriculumError(ValueError):
    """Raised when action evidence cannot support its declared curriculum bucket."""


def planned_action_contract(trajectory: dict[str, Any]) -> dict[str, Any]:
    raw = trajectory.get("action_curriculum")
    if raw is None:
        level = 1
        capabilities = list(CAPABILITIES_BY_LEVEL[level])
    else:
        if not isinstance(raw, dict) or set(raw) != {
            "taxonomy_version",
            "planned_level",
            "capabilities",
        }:
            raise ActionCurriculumError(
                "trajectory.action_curriculum must contain exactly taxonomy_version, "
                "planned_level, and capabilities"
            )
        if raw.get("taxonomy_version") != TAXONOMY_VERSION:
            raise ActionCurriculumError(
                f"action taxonomy_version must be {TAXONOMY_VERSION}"
            )
        level = raw.get("planned_level")
        capabilities = raw.get("capabilities")
    if not isinstance(level, int) or isinstance(level, bool) or level not in CAPABILITIES_BY_LEVEL:
        raise ActionCurriculumError("planned action level must be an integer from 1 through 4")
    expected = list(CAPABILITIES_BY_LEVEL[level])
    if capabilities != expected:
        raise ActionCurriculumError(
            f"planned capabilities must be cumulative and ordered: expected {expected!r}"
        )
    _validate_trajectory_semantics(trajectory, level)
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "planned_level": level,
        "planned_capabilities": expected,
        "bucket": BUCKET_BY_LEVEL[level],
    }


def summarize_action_run(
    trajectory_path: Path,
    evidence_path: Path | None,
    *,
    execution_mode: str,
    require_evidence: bool = True,
    allow_legacy_execution_status_missing: bool = False,
) -> dict[str, Any]:
    trajectory = _load_object(trajectory_path, "trajectory")
    planned = planned_action_contract(trajectory)
    counts = _zero_semantic_counts()
    recoveries = _zero_recovery_counts()
    evidence = None
    if evidence_path is not None and evidence_path.is_file() and not evidence_path.is_symlink():
        records = _load_jsonl(evidence_path)
        if execution_mode == "open_loop_event_replay":
            counts = _open_loop_counts(
                trajectory,
                records,
                allow_legacy_execution_status_missing=(
                    allow_legacy_execution_status_missing
                ),
            )
            kind = "replay_log"
        elif execution_mode == "online_position_yaw_feedback":
            counts, recoveries = _feedback_counts(trajectory, records)
            kind = "navigation_log"
        else:
            raise ActionCurriculumError(f"unknown action execution mode: {execution_mode!r}")
        evidence = {
            "kind": kind,
            "path": str(evidence_path),
            "sha256": _file_sha256(evidence_path),
            "size_bytes": evidence_path.stat().st_size,
            "record_count": len(records),
        }
    summary = {
        **planned,
        "observed_semantic_action_counts": counts,
        "observed_level": _observed_level(counts),
        "controller_recovery_counts": recoveries,
        "evidence": evidence,
    }
    validate_action_summary(summary, require_evidence=require_evidence)
    return summary


def validate_action_summary(summary: Any, *, require_evidence: bool = True) -> None:
    if not isinstance(summary, dict) or set(summary) != {
        "taxonomy_version",
        "planned_level",
        "planned_capabilities",
        "observed_semantic_action_counts",
        "observed_level",
        "controller_recovery_counts",
        "bucket",
        "evidence",
    }:
        raise ActionCurriculumError("action curriculum summary has an unstable field set")
    if summary.get("taxonomy_version") != TAXONOMY_VERSION:
        raise ActionCurriculumError(f"action taxonomy_version must be {TAXONOMY_VERSION}")
    level = summary.get("planned_level")
    if not isinstance(level, int) or isinstance(level, bool) or level not in CAPABILITIES_BY_LEVEL:
        raise ActionCurriculumError("planned action level must be an integer from 1 through 4")
    expected_capabilities = list(CAPABILITIES_BY_LEVEL[level])
    if summary.get("planned_capabilities") != expected_capabilities:
        raise ActionCurriculumError("planned action capabilities are not cumulative")
    if summary.get("bucket") != BUCKET_BY_LEVEL[level]:
        raise ActionCurriculumError("action bucket does not match planned level")
    counts = _validate_count_mapping(
        summary.get("observed_semantic_action_counts"),
        SEMANTIC_ACTIONS,
        "semantic action",
    )
    recoveries = _validate_count_mapping(
        summary.get("controller_recovery_counts"),
        RECOVERY_ACTIONS,
        "controller recovery",
    )
    if not (
        recoveries["attempts"]
        == recoveries["jump_taps"]
        == recoveries["reverse_moves"]
    ):
        raise ActionCurriculumError("controller recovery component counts disagree")
    observed_level = _observed_level(counts)
    if summary.get("observed_level") != observed_level:
        raise ActionCurriculumError("observed action level does not match semantic counts")
    if require_evidence and summary.get("evidence") is None:
        raise ActionCurriculumError("action replay evidence is missing")
    if summary.get("evidence") is not None:
        _validate_evidence(summary["evidence"])
    if not require_evidence:
        return
    if counts["navigation_move"] <= 0:
        raise ActionCurriculumError("action evidence has no observed L1 movement")
    undeclared = [
        name
        for name, count in counts.items()
        if count and SEMANTIC_ACTION_LEVEL[name] > level
    ]
    if undeclared:
        raise ActionCurriculumError(
            f"observed undeclared semantic actions for planned L{level}: {undeclared!r}"
        )
    if level > 1:
        highest = CAPABILITIES_BY_LEVEL[level][-1]
        if counts[highest] <= 0:
            raise ActionCurriculumError(
                f"planned L{level} has no observed highest-level action {highest!r}"
            )


def action_buckets(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"taxonomy_version": TAXONOMY_VERSION}
    for bucket in BUCKET_BY_LEVEL.values():
        episode_ids = sorted(
            item["episode_id"]
            for item in episodes
            if item.get("action_curriculum", {}).get("bucket") == bucket
        )
        result[bucket] = {"episode_count": len(episode_ids), "episode_ids": episode_ids}
    assigned = sum(result[name]["episode_count"] for name in BUCKET_BY_LEVEL.values())
    if assigned != len(episodes):
        raise ActionCurriculumError("not every episode has exactly one action bucket")
    return result


def _validate_trajectory_semantics(trajectory: dict[str, Any], planned_level: int) -> None:
    events = trajectory.get("events", [])
    if not isinstance(events, list):
        raise ActionCurriculumError("trajectory events must be a list")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ActionCurriculumError(f"trajectory event {index} is not an object")
        semantic = event.get("semantic_action")
        if semantic is None:
            continue
        if semantic not in SEMANTIC_ACTION_LEVEL:
            raise ActionCurriculumError(
                f"trajectory event {index} has unknown semantic_action {semantic!r}"
            )
        if SEMANTIC_ACTION_LEVEL[semantic] > planned_level:
            raise ActionCurriculumError(
                f"trajectory event {index} declares {semantic!r} above planned L{planned_level}"
            )
        if semantic == "deliberate_jump" and not (
            event.get("key") == "space" and event.get("action") == "tap"
        ):
            raise ActionCurriculumError(
                "deliberate_jump must be a single explicit space tap"
            )


def _open_loop_counts(
    trajectory: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    allow_legacy_execution_status_missing: bool,
) -> dict[str, int]:
    if not records or records[0].get("event") != "start":
        raise ActionCurriculumError("replay log has no start record")
    observed_events: list[dict[str, Any]] = []
    counts = _zero_semantic_counts()
    for index, record in enumerate(records[1:], 1):
        event = record.get("event")
        if not isinstance(event, dict):
            raise ActionCurriculumError(f"replay record {index} has no event object")
        if "replay_control" in event:
            continue
        scheduled_t = record.get("scheduled_t")
        actual_t = record.get("actual_t")
        if any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for value in (scheduled_t, actual_t)
        ):
            raise ActionCurriculumError(f"replay record {index} has no dispatch timing")
        expected_status = _expected_execution_status(event)
        observed_status = record.get("execution_status")
        if observed_status is None and allow_legacy_execution_status_missing:
            observed_status = expected_status
        if observed_status != expected_status:
            raise ActionCurriculumError(
                f"replay record {index} execution status does not match its input primitive"
            )
        event_t = event.get("t", 0)
        if (
            not isinstance(event_t, (int, float))
            or isinstance(event_t, bool)
            or not math.isfinite(float(event_t))
            or float(scheduled_t) != float(event_t)
            or float(actual_t) < 0
        ):
            raise ActionCurriculumError(
                f"replay record {index} timing does not match its trajectory event"
            )
        observed_events.append(event)
        if expected_status == "executed":
            _count_executed_event(counts, event)
    planned_events = trajectory.get("events", [])
    if observed_events != planned_events:
        raise ActionCurriculumError("replay evidence does not exactly match trajectory events")
    return counts


def _feedback_counts(
    trajectory: dict[str, Any], records: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[str, int]]:
    if trajectory.get("type") != "feedback_roam" or trajectory.get("events") != []:
        raise ActionCurriculumError("feedback action evidence requires an event-free feedback_roam")
    if not records or records[0].get("event") != "start":
        raise ActionCurriculumError("navigation log has no start record")
    counts = _zero_semantic_counts()
    recoveries = _zero_recovery_counts()
    control_count = 0
    for index, record in enumerate(records[1:], 1):
        event = record.get("event")
        if event == "control":
            control_count += 1
            if record.get("moving") is True:
                counts["navigation_move"] += 1
            mouse_dx = record.get("mouse_dx", 0)
            if (
                not isinstance(mouse_dx, (int, float))
                or isinstance(mouse_dx, bool)
                or not math.isfinite(float(mouse_dx))
            ):
                raise ActionCurriculumError(f"navigation control {index} has invalid mouse_dx")
            if mouse_dx:
                counts["navigation_camera"] += 1
        elif event == "recovery":
            if not isinstance(record.get("attempt"), int) or record["attempt"] <= 0:
                raise ActionCurriculumError(f"navigation recovery {index} has invalid attempt")
            recoveries["attempts"] += 1
            recoveries["jump_taps"] += 1
            recoveries["reverse_moves"] += 1
        elif record.get("semantic_action") is not None:
            raise ActionCurriculumError(
                "feedback navigator emitted an unsupported semantic action record"
            )
    if control_count <= 0:
        raise ActionCurriculumError("navigation log has no controller decisions")
    return counts, recoveries


def _count_executed_event(counts: dict[str, int], event: dict[str, Any]) -> None:
    key = event.get("key")
    action = event.get("action", "tap")
    if key in {"w", "a", "s", "d"} and action in {"down", "tap"}:
        counts["navigation_move"] += 1
    if (event.get("mouse_dx", 0) or event.get("mouse_dy", 0)) and (
        "mouse_dx" in event or "mouse_dy" in event
    ):
        counts["navigation_camera"] += 1
    semantic = event.get("semantic_action")
    if semantic == "deliberate_jump":
        counts[semantic] += 1


def _expected_execution_status(event: dict[str, Any]) -> str:
    if event.get("semantic_action") in {
        "deterministic_block_placement",
        "controlled_combat",
    }:
        return "unsupported_contract_only"
    if "key" in event or "mouse_dx" in event or "mouse_dy" in event:
        return "executed"
    return "non_input"


def _observed_level(counts: dict[str, int]) -> int:
    return max(
        (SEMANTIC_ACTION_LEVEL[name] for name, count in counts.items() if count > 0),
        default=0,
    )


def _validate_count_mapping(value: Any, keys: tuple[str, ...], label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ActionCurriculumError(f"{label} counts must use the stable taxonomy")
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in value.values()
    ):
        raise ActionCurriculumError(f"{label} counts must be non-negative integers")
    return value


def _validate_evidence(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != {
        "kind",
        "path",
        "sha256",
        "size_bytes",
        "record_count",
    }:
        raise ActionCurriculumError("action evidence has an unstable field set")
    if value.get("kind") not in {"replay_log", "navigation_log"}:
        raise ActionCurriculumError("action evidence kind is invalid")
    if not isinstance(value.get("path"), str) or not value["path"]:
        raise ActionCurriculumError("action evidence path is missing")
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ActionCurriculumError("action evidence sha256 is invalid")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ActionCurriculumError("action evidence sha256 is invalid") from exc
    for key in ("size_bytes", "record_count"):
        if not isinstance(value.get(key), int) or isinstance(value[key], bool) or value[key] < 0:
            raise ActionCurriculumError(f"action evidence {key} is invalid")


def _zero_semantic_counts() -> dict[str, int]:
    return {name: 0 for name in SEMANTIC_ACTIONS}


def _zero_recovery_counts() -> dict[str, int]:
    return {name: 0 for name in RECOVERY_ACTIONS}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActionCurriculumError(f"could not read {label} JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ActionCurriculumError(f"{label} must be a JSON object")
    return value


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ActionCurriculumError(f"could not read action evidence: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ActionCurriculumError(f"blank action evidence line {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ActionCurriculumError(
                f"invalid action evidence JSON at line {line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise ActionCurriculumError(
                f"action evidence line {line_number} is not an object"
            )
        records.append(value)
    return records


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
