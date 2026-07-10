from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

TAXONOMY_VERSION = 1
ACTION_SOURCES = (
    "scripted_astar",
    "feedback_planner",
    "human_demo",
    "learned_visual_policy",
    "llm_skill_agent",
)
NATIVE_TRACE_REQUIRED_SOURCES = {
    "human_demo",
    "learned_visual_policy",
    "llm_skill_agent",
}

_LEGACY_SCRIPTED_TYPES = {
    "astar_walk",
    "grid_patrol",
    "look_scan",
    "random",
    "roam",
    "scene_probe",
    "scripted",
}


class ActionSourceError(ValueError):
    """Raised when action-producer provenance is absent or contradictory."""


def declared_action_source(source_id: str) -> dict[str, Any]:
    """Build the stable action-source record embedded by new trajectory producers."""
    _require_source_id(source_id)
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "id": source_id,
        "provenance": "declared",
    }


def resolve_manifest_action_source(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve an explicit source or mechanically migrate a known legacy controller."""
    trajectory = manifest.get("trajectory")
    if not isinstance(trajectory, Mapping):
        raise ActionSourceError("manifest.trajectory is missing")
    declared = trajectory.get("action_source")
    native_trace = trajectory.get("native_trace")
    if declared is not None:
        normalized = validate_action_source_record(declared)
        trace_ref = validate_native_trace_ref(native_trace) if native_trace is not None else None
        if (
            normalized["id"] in NATIVE_TRACE_REQUIRED_SOURCES
            and normalized["provenance"] != "declared"
        ):
            raise ActionSourceError(
                f"action_source={normalized['id']!r} cannot be derived from a legacy trajectory"
            )
        if normalized["id"] in NATIVE_TRACE_REQUIRED_SOURCES and trace_ref is None:
            raise ActionSourceError(
                f"action_source={normalized['id']!r} requires a canonical native_trace"
            )
        if normalized["id"] in NATIVE_TRACE_REQUIRED_SOURCES:
            binding = validate_curriculum_binding(trajectory.get("curriculum_binding"))
            if binding["status"] != "l1_candidate":
                raise ActionSourceError(
                    "external native trace still requires semantic effect validation"
                )
        return normalized
    if native_trace is not None:
        raise ActionSourceError("trajectory.native_trace has no declared action_source")
    return _legacy_action_source(trajectory)


def validate_action_source_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "taxonomy_version",
        "id",
        "provenance",
    }:
        raise ActionSourceError(
            "action_source must contain exactly taxonomy_version, id, and provenance"
        )
    if value.get("taxonomy_version") != TAXONOMY_VERSION:
        raise ActionSourceError(f"action_source taxonomy_version must be {TAXONOMY_VERSION}")
    source_id = value.get("id")
    _require_source_id(source_id)
    if value.get("provenance") not in {"declared", "derived_legacy_trajectory"}:
        raise ActionSourceError("invalid action_source provenance")
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "id": source_id,
        "provenance": value["provenance"],
    }


def validate_native_trace_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "sha256",
        "tick_rate_hz",
    }:
        raise ActionSourceError(
            "native_trace must contain exactly schema_version, sha256, and tick_rate_hz"
        )
    if value.get("schema_version") != 1:
        raise ActionSourceError("native_trace schema_version must be 1")
    digest = value.get("sha256")
    if not _is_sha256(digest):
        raise ActionSourceError("native_trace sha256 must be 64 lowercase hex characters")
    if value.get("tick_rate_hz") != 20:
        raise ActionSourceError("native_trace tick_rate_hz must be 20")
    return {"schema_version": 1, "sha256": digest, "tick_rate_hz": 20}


def validate_curriculum_binding(value: Any) -> dict[str, Any]:
    fields = {"status", "has_jump_input", "has_use_input", "has_attack_input"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ActionSourceError("external native trace has no stable curriculum_binding")
    flags = {field: value[field] for field in fields - {"status"}}
    if any(not isinstance(item, bool) for item in flags.values()):
        raise ActionSourceError("native trace curriculum input flags must be boolean")
    expected = "requires_semantic_effect_validation" if any(flags.values()) else "l1_candidate"
    if value.get("status") != expected:
        raise ActionSourceError("native trace curriculum status disagrees with its input flags")
    return {
        "status": expected,
        "has_jump_input": flags["has_jump_input"],
        "has_use_input": flags["has_use_input"],
        "has_attack_input": flags["has_attack_input"],
    }


def validate_external_rollout_binding(value: Any) -> dict[str, Any]:
    """Validate the source/target boundary emitted by the neutral Phase 2 importer."""
    fields = {
        "rollout_schema_version",
        "rollout_sha256",
        "source_minecraft_version",
        "target_minecraft_version",
        "target_client_profile",
        "camera_calibration_sha256",
        "compatibility_status",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ActionSourceError("external_rollout_binding has an unstable field set")
    if value["rollout_schema_version"] != 1:
        raise ActionSourceError("external rollout schema_version must be 1")
    for field in ("rollout_sha256", "camera_calibration_sha256"):
        if not _is_sha256(value[field]):
            raise ActionSourceError(f"external rollout {field} must be a SHA-256")
    if value["source_minecraft_version"] != "1.16.5":
        raise ActionSourceError("neutral rollout source Minecraft version must be 1.16.5")
    if value["target_minecraft_version"] != "26.2":
        raise ActionSourceError("neutral rollout target Minecraft version must be 26.2")
    profile = value["target_client_profile"]
    if not isinstance(profile, str) or not profile.strip():
        raise ActionSourceError("external rollout target_client_profile must be non-empty")
    if value["compatibility_status"] != "target_replay_not_yet_validated":
        raise ActionSourceError("unknown external rollout compatibility status")
    return dict(value)


def attach_episode_action_sources(
    episodes: Sequence[dict[str, Any]], manifests: Sequence[Mapping[str, Any]]
) -> None:
    """Bind source records and trace hashes to dataset episodes in place."""
    manifest_by_id = {item.get("run_id"): item for item in manifests}
    if len(manifest_by_id) != len(manifests):
        raise ActionSourceError("manifest run IDs are missing or duplicated")
    for episode in episodes:
        episode_id = episode.get("episode_id")
        manifest = manifest_by_id.get(episode_id)
        if manifest is None:
            raise ActionSourceError(f"episode {episode_id!r} has no matching manifest")
        source = resolve_manifest_action_source(manifest)
        trajectory = manifest["trajectory"]
        native_value = trajectory.get("native_trace")
        native_trace = validate_native_trace_ref(native_value) if native_value is not None else None
        episode["action_source"] = source
        episode["native_trace_sha256"] = native_trace["sha256"] if native_trace else None


def action_source_index(
    episodes: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build source-wise episode/pair counts and IDs for training configuration."""
    result: dict[str, Any] = {"taxonomy_version": TAXONOMY_VERSION}
    assigned_episodes: list[str] = []
    assigned_pairs: list[str] = []
    for source_id in ACTION_SOURCES:
        episode_ids = sorted(
            _episode_id(item)
            for item in episodes
            if _episode_source_id(item) == source_id
        )
        pair_ids = sorted(
            _pair_id(item) for item in pairs if _pair_source_id(item) == source_id
        )
        result[source_id] = {
            "episode_count": len(episode_ids),
            "episode_ids": episode_ids,
            "pair_count": len(pair_ids),
            "pair_ids": pair_ids,
        }
        assigned_episodes.extend(episode_ids)
        assigned_pairs.extend(pair_ids)
    all_episode_ids = sorted(_episode_id(item) for item in episodes)
    if sorted(assigned_episodes) != all_episode_ids:
        raise ActionSourceError("not every episode has exactly one valid action_source")
    all_pair_ids = sorted(_pair_id(item) for item in pairs)
    if sorted(assigned_pairs) != all_pair_ids:
        raise ActionSourceError("not every pair has exactly one valid action_source")
    return result


def sample_episode_ids_by_action_source(
    episodes: Sequence[Mapping[str, Any]],
    source_counts: Mapping[str, int],
    *,
    seed: int,
) -> list[str]:
    """Sample an exact, deterministic without-replacement training source mix."""
    if not source_counts:
        raise ActionSourceError("source_counts must not be empty")
    unknown = sorted(set(source_counts) - set(ACTION_SOURCES))
    if unknown:
        raise ActionSourceError(f"unknown action sources in sampling request: {unknown!r}")
    by_source: dict[str, list[str]] = {source_id: [] for source_id in ACTION_SOURCES}
    for episode in episodes:
        by_source[_episode_source_id(episode)].append(_episode_id(episode))
    rng = random.Random(seed)
    sampled: list[str] = []
    for source_id in ACTION_SOURCES:
        requested = source_counts.get(source_id, 0)
        if not isinstance(requested, int) or isinstance(requested, bool) or requested < 0:
            raise ActionSourceError(f"sample count for {source_id!r} must be a nonnegative integer")
        available = sorted(by_source[source_id])
        if requested > len(available):
            raise ActionSourceError(
                f"requested {requested} {source_id!r} episodes but only {len(available)} exist"
            )
        sampled.extend(rng.sample(available, requested))
    rng.shuffle(sampled)
    return sampled


def sample_pair_ids_by_action_source(
    pairs: Sequence[Mapping[str, Any]],
    source_counts: Mapping[str, int],
    *,
    seed: int,
) -> list[str]:
    """Sample an exact deterministic pair mix, the preferred v2v training unit."""
    records = [
        {
            "episode_id": _pair_id(pair),
            "action_source": {
                "taxonomy_version": TAXONOMY_VERSION,
                "id": _pair_source_id(pair),
                "provenance": "declared",
            },
        }
        for pair in pairs
    ]
    return sample_episode_ids_by_action_source(records, source_counts, seed=seed)


def _legacy_action_source(trajectory: Mapping[str, Any]) -> dict[str, Any]:
    execution_mode = trajectory.get("execution_mode", "open_loop_event_replay")
    trajectory_type = trajectory.get("type")
    if execution_mode == "online_position_yaw_feedback" and trajectory_type == "feedback_roam":
        source_id = "feedback_planner"
    elif execution_mode == "open_loop_event_replay" and trajectory_type in _LEGACY_SCRIPTED_TYPES:
        source_id = "scripted_astar"
    else:
        raise ActionSourceError(
            "legacy action source cannot be inferred without a known scripted or feedback "
            f"trajectory contract (type={trajectory_type!r}, mode={execution_mode!r})"
        )
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "id": source_id,
        "provenance": "derived_legacy_trajectory",
    }


def _episode_id(episode: Mapping[str, Any]) -> str:
    episode_id = episode.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        raise ActionSourceError("episode_id must be a non-empty string")
    return episode_id


def _episode_source_id(episode: Mapping[str, Any]) -> str:
    source = validate_action_source_record(episode.get("action_source"))
    return str(source["id"])


def _pair_id(pair: Mapping[str, Any]) -> str:
    pair_id = pair.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id:
        raise ActionSourceError("pair_id must be a non-empty string")
    return pair_id


def _pair_source_id(pair: Mapping[str, Any]) -> str:
    invariants = pair.get("invariants")
    if not isinstance(invariants, Mapping):
        raise ActionSourceError(f"pair {_pair_id(pair)!r} has no invariants")
    source_id = invariants.get("action_source")
    _require_source_id(source_id)
    return str(source_id)


def _require_source_id(value: Any) -> None:
    if value not in ACTION_SOURCES:
        raise ActionSourceError(f"unknown action_source id: {value!r}")


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
