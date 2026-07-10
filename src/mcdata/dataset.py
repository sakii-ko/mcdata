from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from mcdata.action_curriculum import ActionCurriculumError, action_buckets
from mcdata.action_source import (
    ActionSourceError,
    action_source_index,
    attach_episode_action_sources,
)
from mcdata.dataset_support.comparisons import cohorts, comparisons, manual_review
from mcdata.dataset_support.core import (
    DatasetValidationError,
    collect_runtime_logs as _collect_runtime_logs,
    require_hash,
    validate_index_schema,
    value_sha256,
    write_dataset_outputs,
)
from mcdata.dataset_support.episodes import global_invariants, load_episodes
from mcdata.dataset_support.pairs import edit_pairs

SCHEMA_VERSION = 2

__all__ = ["DatasetValidationError", "collect_runtime_logs", "write_dataset_index"]


def _validate_capture_expectations(
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> None:
    if (
        not isinstance(width, int)
        or width <= 0
        or not isinstance(height, int)
        or height <= 0
        or not isinstance(fps, (int, float))
        or not math.isfinite(fps)
        or fps <= 0
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise DatasetValidationError(
            "Expected capture dimensions, fps, and duration must be positive"
        )


def _validate_profile_set(episodes: Sequence[dict[str, Any]], expected: Sequence[str]) -> None:
    actual_profiles = [episode["profile_name"] for episode in episodes]
    if len(actual_profiles) != len(set(actual_profiles)) or set(actual_profiles) != set(expected):
        raise DatasetValidationError(
            f"Profile set mismatch: expected={sorted(expected)!r}, actual={sorted(actual_profiles)!r}"
        )
    episode_ids = [episode["episode_id"] for episode in episodes]
    if any(not isinstance(item, str) or not item for item in episode_ids) or len(
        episode_ids
    ) != len(set(episode_ids)):
        raise DatasetValidationError("Episode run IDs must be non-empty and unique")


def _build_index(
    *,
    root: Path,
    episodes: list[dict[str, Any]],
    manifests: list[dict[str, Any]],
    expected: list[str],
    generator_commit: str,
    primary_profile: str,
    pair_manifest: Path,
    strict_compare_report: Path,
    diagnostic_compare_reports: Iterable[Path],
    visual_review: Path | None,
    width: int,
    height: int,
    fps: float,
    duration: float,
) -> dict[str, Any]:
    try:
        attach_episode_action_sources(episodes, manifests)
    except ActionSourceError as exc:
        raise DatasetValidationError(str(exc)) from exc
    episodes.sort(key=lambda item: item["profile_name"])
    invariants = global_invariants(
        manifests, width=width, height=height, fps=fps, duration=duration
    )
    cohort_items, primary_cohort_id = cohorts(episodes, manifests, primary_profile)
    pair_manifest_artifact, pair_items = edit_pairs(root, pair_manifest, episodes, manifests)
    comparison_items = comparisons(
        root,
        strict_compare_report,
        diagnostic_compare_reports,
        episodes,
        primary_cohort_id,
    )
    review = manual_review(root, visual_review.resolve() if visual_review else None, set(expected))
    try:
        bucket_index = action_buckets(episodes)
    except ActionCurriculumError as exc:
        raise DatasetValidationError(str(exc)) from exc
    index = {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": "mcdata.dataset-index", "git_commit": generator_commit},
        "status": "accepted" if review is not None else "automated_pass",
        "primary_cohort_id": primary_cohort_id,
        "invariants": invariants,
        "cohorts": cohort_items,
        "episodes": episodes,
        "action_buckets": bucket_index,
        "action_sources": action_source_index(episodes, pair_items),
        "pair_manifest": pair_manifest_artifact,
        "pairs": pair_items,
        "comparisons": comparison_items,
        "manual_review": review,
        "checksum_manifest": "SHA256SUMS",
    }
    index["dataset_id"] = f"sha256:{value_sha256(index)}"
    return index


def collect_runtime_logs(
    dataset_root: Path,
    *,
    expected_profiles: Sequence[str],
) -> list[Path]:
    """Snapshot each episode's client log into its run directory for portable auditing."""
    return _collect_runtime_logs(dataset_root, expected_profiles=expected_profiles)


def write_dataset_index(
    dataset_root: Path,
    *,
    expected_profiles: Sequence[str],
    primary_profile: str,
    generator_commit: str,
    pair_manifest: Path,
    strict_compare_report: Path,
    diagnostic_compare_reports: Iterable[Path] = (),
    visual_review: Path | None = None,
    expected_width: int = 1280,
    expected_height: int = 720,
    expected_fps: float = 24.0,
    expected_duration: float = 60.0,
) -> dict[str, Any]:
    root = dataset_root.resolve()
    if not root.is_dir():
        raise DatasetValidationError(f"Dataset root is not a directory: {root}")
    _validate_capture_expectations(expected_width, expected_height, expected_fps, expected_duration)
    expected = list(expected_profiles)
    diagnostic_reports = tuple(diagnostic_compare_reports)
    if not expected or len(expected) != len(set(expected)):
        raise DatasetValidationError("Expected profiles must be a non-empty unique list")
    if primary_profile not in expected:
        raise DatasetValidationError("Primary profile is not in the expected profile set")
    generator_commit = require_hash(generator_commit, 40, "dataset-index generator commit")
    if not diagnostic_reports:
        raise DatasetValidationError("At least one all-dataset diagnostic comparison is required")
    episodes, manifests = load_episodes(
        root,
        expected_width=expected_width,
        expected_height=expected_height,
        expected_fps=expected_fps,
        expected_duration=expected_duration,
    )
    _validate_profile_set(episodes, expected)
    index = _build_index(
        root=root,
        episodes=episodes,
        manifests=manifests,
        expected=expected,
        generator_commit=generator_commit,
        primary_profile=primary_profile,
        pair_manifest=pair_manifest,
        strict_compare_report=strict_compare_report,
        diagnostic_compare_reports=diagnostic_reports,
        visual_review=visual_review,
        width=expected_width,
        height=expected_height,
        fps=expected_fps,
        duration=expected_duration,
    )
    validate_index_schema(index)
    write_dataset_outputs(root, index)
    return index
