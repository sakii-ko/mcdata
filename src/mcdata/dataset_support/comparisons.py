from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from mcdata.dataset_support.core import (
    DatasetValidationError,
    artifact,
    load_json,
    require_mapping,
    require_nonempty_string,
    validate_report_evidence,
    value_sha256,
)


def cohorts(
    episodes: list[dict[str, Any]],
    manifests: Sequence[dict[str, Any]],
    primary_profile: str,
) -> tuple[list[dict[str, Any]], str]:
    state_by_profile = {
        manifest["profile"]["name"]: manifest["world"]["state"] for manifest in manifests
    }
    primary_state_hash = value_sha256(state_by_profile[primary_profile])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for episode in episodes:
        state_hash = value_sha256(state_by_profile[episode["profile_name"]])
        episode["cohort_id"] = f"cohort-{state_hash[:16]}"
        grouped.setdefault(state_hash, []).append(episode)
    results = []
    for state_hash, members in sorted(grouped.items()):
        members.sort(key=lambda item: item["profile_name"])
        cohort_id = f"cohort-{state_hash[:16]}"
        results.append(
            {
                "cohort_id": cohort_id,
                "role": (
                    "strict_rendering_matrix"
                    if state_hash == primary_state_hash
                    else "world_state_variant"
                ),
                "world_state_sha256": state_hash,
                "world_state": state_by_profile[members[0]["profile_name"]],
                "episode_ids": [item["episode_id"] for item in members],
                "profile_names": [item["profile_name"] for item in members],
            }
        )
    return results, f"cohort-{primary_state_hash[:16]}"


def _comparison_members(
    report: dict[str, Any],
    report_path: Path,
    known_run_dirs: dict[str, dict[str, Any]],
    expected_members: set[str] | None,
) -> tuple[list[str], set[str]]:
    inputs = report.get("inputs")
    if not isinstance(inputs, list):
        raise DatasetValidationError(f"Comparison report has no inputs list: {report_path}")
    basenames = [Path(str(item)).name for item in inputs]
    if len(basenames) != len(set(basenames)) or any(
        name not in known_run_dirs for name in basenames
    ):
        raise DatasetValidationError(
            f"Comparison report has unknown/duplicate members: {report_path}"
        )
    member_ids = {known_run_dirs[name]["episode_id"] for name in basenames}
    if expected_members is not None and member_ids != expected_members:
        raise DatasetValidationError(
            f"Comparison members do not match required cohort: {report_path}"
        )
    return basenames, member_ids


def _validate_comparison_evidence(
    report: dict[str, Any],
    report_path: Path,
    basenames: list[str],
    known_run_dirs: dict[str, dict[str, Any]],
) -> None:
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != len(basenames):
        raise DatasetValidationError(f"Comparison evidence count mismatch: {report_path}")
    seen_evidence = set()
    for item_value in evidence:
        item = require_mapping(item_value, "comparison evidence entry")
        name = Path(require_nonempty_string(item.get("input"), "comparison evidence input")).name
        if name not in known_run_dirs or name in seen_evidence:
            raise DatasetValidationError(
                f"Comparison has unknown/duplicate evidence: {report_path}"
            )
        episode = known_run_dirs[name]
        validate_report_evidence(
            {key: value for key, value in item.items() if key != "input"},
            {key: episode[key] for key in ("manifest", "video", "trajectory", "positions")},
            f"Comparison report for {name}",
        )
        seen_evidence.add(name)
    if seen_evidence != set(basenames):
        raise DatasetValidationError(f"Comparison evidence members mismatch: {report_path}")


def _alignment_summary(
    report: dict[str, Any], report_path: Path, basenames: list[str]
) -> dict[str, Any]:
    alignment = require_mapping(report.get("position_alignment"), "position_alignment")
    if alignment.get("passed") is not True:
        raise DatasetValidationError(f"Position alignment did not pass: {report_path}")
    threshold = alignment.get("threshold_blocks")
    maximum = alignment.get("max_distance_blocks")
    if (
        not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or threshold <= 0
        or threshold > 2.0
        or not isinstance(maximum, (int, float))
        or not math.isfinite(float(maximum))
        or maximum > threshold
    ):
        raise DatasetValidationError(f"Invalid comparison threshold/result: {report_path}")
    _validate_alignment_pairs(alignment, report_path, basenames)
    return {
        key: alignment.get(key)
        for key in ("passed", "threshold_blocks", "max_distance_blocks", "mean_distance_blocks")
    }


def _validate_alignment_pairs(
    alignment: dict[str, Any], report_path: Path, basenames: list[str]
) -> None:
    pairs = alignment.get("pairs")
    expected_pairs = {tuple(sorted(pair)) for pair in itertools.combinations(basenames, 2)}
    if not isinstance(pairs, list) or len(pairs) != len(expected_pairs):
        raise DatasetValidationError(f"Comparison pair count mismatch: {report_path}")
    actual_pairs = set()
    for pair_value in pairs:
        pair = require_mapping(pair_value, "position alignment pair")
        names = tuple(sorted((Path(str(pair.get("left"))).name, Path(str(pair.get("right"))).name)))
        count = pair.get("count")
        if pair.get("passed") is not True or not isinstance(count, int) or count <= 0:
            raise DatasetValidationError(f"Comparison pair did not pass: {report_path}")
        actual_pairs.add(names)
    if actual_pairs != expected_pairs:
        raise DatasetValidationError(f"Comparison pair members mismatch: {report_path}")


def comparison(
    root: Path,
    report_path: Path,
    *,
    role: str,
    known_run_dirs: dict[str, dict[str, Any]],
    expected_members: set[str] | None = None,
) -> dict[str, Any]:
    report = load_json(report_path)
    basenames, member_ids = _comparison_members(
        report, report_path, known_run_dirs, expected_members
    )
    _validate_comparison_evidence(report, report_path, basenames, known_run_dirs)
    alignment = _alignment_summary(report, report_path, basenames)
    return {
        **artifact(root, report_path),
        "role": role,
        "member_episode_ids": sorted(member_ids),
        "position_alignment": alignment,
    }


def manual_review(
    root: Path,
    visual_review: Path | None,
    expected_profiles: set[str],
) -> dict[str, Any] | None:
    if visual_review is None:
        return None
    review = load_json(visual_review)
    if review.get("schema_version") != 1 or review.get("status") != "pass":
        raise DatasetValidationError(f"Manual visual review did not pass: {visual_review}")
    profiles = review.get("reviewed_profiles")
    if not isinstance(profiles, list) or set(profiles) != expected_profiles:
        raise DatasetValidationError("Manual visual review does not cover the expected profile set")
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise DatasetValidationError("Manual visual review has no evidence files")
    evidence_paths = [(root / str(path)).resolve() for path in evidence]
    if len(evidence_paths) != len(set(evidence_paths)) or visual_review.resolve() in evidence_paths:
        raise DatasetValidationError(
            "Manual visual review evidence is duplicate or self-referential"
        )
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    if not any(path.suffix.lower() in image_suffixes for path in evidence_paths):
        raise DatasetValidationError("Manual visual review requires at least one image artifact")
    notes = review.get("notes")
    if not isinstance(notes, list) or not notes or any(not isinstance(item, str) for item in notes):
        raise DatasetValidationError("Manual visual review requires non-empty textual notes")
    evidence_artifacts = [artifact(root, path) for path in sorted(evidence_paths)]
    return {
        **artifact(root, visual_review),
        "status": "pass",
        "reviewed_profiles": sorted(profiles),
        "evidence": evidence_artifacts,
        "notes": notes,
    }


def comparisons(
    root: Path,
    strict_report: Path,
    diagnostic_reports: Iterable[Path],
    episodes: Sequence[dict[str, Any]],
    primary_cohort_id: str,
) -> list[dict[str, Any]]:
    primary_members = {
        item["episode_id"] for item in episodes if item["cohort_id"] == primary_cohort_id
    }
    known_run_dirs = {Path(item["run_dir"]).name: item for item in episodes}
    results = [
        comparison(
            root,
            strict_report.resolve(),
            role="cohort_gate",
            known_run_dirs=known_run_dirs,
            expected_members=primary_members,
        )
    ]
    results.extend(
        comparison(
            root,
            path.resolve(),
            role="all_dataset_diagnostic",
            known_run_dirs=known_run_dirs,
            expected_members={item["episode_id"] for item in episodes},
        )
        for path in diagnostic_reports
    )
    return results
