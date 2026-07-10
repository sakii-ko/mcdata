from __future__ import annotations

import pytest

from mcdata.action_source import (
    ACTION_SOURCES,
    ActionSourceError,
    action_source_index,
    attach_episode_action_sources,
    declared_action_source,
    resolve_manifest_action_source,
    sample_episode_ids_by_action_source,
    sample_pair_ids_by_action_source,
)


def _manifest(run_id: str, trajectory_type: str, execution_mode: str) -> dict:
    return {
        "run_id": run_id,
        "trajectory": {"type": trajectory_type, "execution_mode": execution_mode},
    }


def test_legacy_action_sources_are_derived_only_from_known_contracts() -> None:
    scripted = resolve_manifest_action_source(
        _manifest("scripted", "astar_walk", "open_loop_event_replay")
    )
    feedback = resolve_manifest_action_source(
        _manifest("feedback", "feedback_roam", "online_position_yaw_feedback")
    )

    assert scripted == {
        "taxonomy_version": 1,
        "id": "scripted_astar",
        "provenance": "derived_legacy_trajectory",
    }
    assert feedback["id"] == "feedback_planner"
    with pytest.raises(ActionSourceError, match="cannot be inferred"):
        resolve_manifest_action_source(
            _manifest("unknown", "external", "open_loop_event_replay")
        )


def test_external_sources_require_declared_native_trace() -> None:
    manifest = _manifest("policy", "native_action_trace_replay", "open_loop_event_replay")
    manifest["trajectory"]["action_source"] = declared_action_source(
        "learned_visual_policy"
    )
    with pytest.raises(ActionSourceError, match="requires a canonical native_trace"):
        resolve_manifest_action_source(manifest)

    manifest["trajectory"]["native_trace"] = {
        "schema_version": 1,
        "sha256": "a" * 64,
        "tick_rate_hz": 20,
    }
    manifest["trajectory"]["curriculum_binding"] = _l1_binding()
    assert resolve_manifest_action_source(manifest)["id"] == "learned_visual_policy"


def test_episode_source_stats_and_sampling_are_exact_and_deterministic() -> None:
    episodes = [{"episode_id": f"episode-{index}"} for index in range(6)]
    manifests = [
        _manifest("episode-0", "astar_walk", "open_loop_event_replay"),
        _manifest("episode-1", "roam", "open_loop_event_replay"),
        _manifest("episode-2", "feedback_roam", "online_position_yaw_feedback"),
        _external_manifest("episode-3", "human_demo", "b" * 64),
        _external_manifest("episode-4", "learned_visual_policy", "c" * 64),
        _external_manifest("episode-5", "llm_skill_agent", "d" * 64),
    ]
    attach_episode_action_sources(episodes, manifests)
    index = action_source_index(episodes)

    assert set(index) == {"taxonomy_version", *ACTION_SOURCES}
    assert index["scripted_astar"] == {
        "episode_count": 2,
        "episode_ids": ["episode-0", "episode-1"],
        "pair_count": 0,
        "pair_ids": [],
    }
    assert index["feedback_planner"]["episode_ids"] == ["episode-2"]
    assert episodes[3]["native_trace_sha256"] == "b" * 64
    request = {
        "scripted_astar": 1,
        "human_demo": 1,
        "learned_visual_policy": 1,
    }
    first = sample_episode_ids_by_action_source(episodes, request, seed=73)
    second = sample_episode_ids_by_action_source(episodes, request, seed=73)
    assert first == second
    assert len(first) == len(set(first)) == 3
    assert "episode-3" in first and "episode-4" in first


def test_pair_source_stats_and_sampling_use_pair_invariants() -> None:
    episodes = [
        {
            "episode_id": "e0",
            "action_source": {
                "taxonomy_version": 1,
                "id": "human_demo",
                "provenance": "declared",
            },
        }
    ]
    pairs = [
        {"pair_id": "p0", "invariants": {"action_source": "human_demo"}},
        {"pair_id": "p1", "invariants": {"action_source": "human_demo"}},
    ]
    index = action_source_index(episodes, pairs)

    assert index["human_demo"]["pair_ids"] == ["p0", "p1"]
    assert sample_pair_ids_by_action_source(
        pairs, {"human_demo": 1}, seed=91
    ) == sample_pair_ids_by_action_source(pairs, {"human_demo": 1}, seed=91)


def test_source_sampling_rejects_empty_unknown_and_oversubscribed_requests() -> None:
    episode = {
        "episode_id": "only",
        "action_source": {
            "taxonomy_version": 1,
            "id": "scripted_astar",
            "provenance": "derived_legacy_trajectory",
        },
    }
    with pytest.raises(ActionSourceError, match="must not be empty"):
        sample_episode_ids_by_action_source([episode], {}, seed=1)
    with pytest.raises(ActionSourceError, match="unknown action sources"):
        sample_episode_ids_by_action_source([episode], {"mystery": 1}, seed=1)
    with pytest.raises(ActionSourceError, match="only 1 exist"):
        sample_episode_ids_by_action_source([episode], {"scripted_astar": 2}, seed=1)


def _external_manifest(run_id: str, source_id: str, digest: str) -> dict:
    manifest = _manifest(run_id, "native_action_trace_replay", "open_loop_event_replay")
    manifest["trajectory"].update(
        {
            "action_source": declared_action_source(source_id),
            "native_trace": {
                "schema_version": 1,
                "sha256": digest,
                "tick_rate_hz": 20,
            },
            "curriculum_binding": _l1_binding(),
        }
    )
    return manifest


def _l1_binding() -> dict:
    return {
        "status": "l1_candidate",
        "has_jump_input": False,
        "has_use_input": False,
        "has_attack_input": False,
    }
