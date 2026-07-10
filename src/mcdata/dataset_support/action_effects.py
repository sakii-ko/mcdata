from __future__ import annotations

from pathlib import Path
from typing import Any

from mcdata.action_effect import (
    ActionEffectError,
    REPORT_FILENAME,
    action_effect_manifest_evidence,
    validate_action_effect_report,
)
from mcdata.dataset_support.core import (
    DatasetValidationError,
    artifact,
    require_mapping,
)


def accepted_action_effect(
    manifest: dict[str, Any], root: Path, run_dir: Path, action: dict[str, Any]
) -> dict[str, Any] | None:
    level = action["planned_level"]
    claimed = manifest.get("action_effect")
    report_path = run_dir / REPORT_FILENAME
    if level == 1:
        if claimed is not None or report_path.exists() or report_path.is_symlink():
            raise DatasetValidationError(
                f"L1 episode has unexpected physical action-effect evidence: {run_dir.name}"
            )
        return None
    if claimed is None:
        raise DatasetValidationError(
            f"Planned L{level} requires a physical action-effect report: {run_dir.name}"
        )
    try:
        report = validate_action_effect_report(run_dir)
        expected_claim = action_effect_manifest_evidence(report_path, report)
    except ActionEffectError as exc:
        raise DatasetValidationError(
            f"Physical action-effect validation failed for {run_dir.name}: {exc}"
        ) from exc
    claimed_normalized = dict(require_mapping(claimed, "manifest.action_effect"))
    claimed_normalized["path"] = expected_claim["path"]
    if claimed_normalized != expected_claim:
        raise DatasetValidationError(
            f"Manifest physical action-effect claim is stale for {run_dir.name}"
        )
    if not _report_matches_action(report, action):
        raise DatasetValidationError(
            f"Physical deliberate-jump gate did not pass for {run_dir.name}"
        )
    return {
        **artifact(root, report_path),
        "kind": report["kind"],
        "schema_version": report["schema_version"],
        "report_id": report["report_id"],
        "planned_jump_count": report["planned_jump_count"],
        "verified_jump_count": report["verified_jump_count"],
        "accepted": True,
    }


def _report_matches_action(report: dict[str, Any], action: dict[str, Any]) -> bool:
    return bool(
        report["accepted"] is True
        and report["planned_level"] == action["planned_level"]
        and report["planned_jump_count"] == report["verified_jump_count"]
        and report["planned_jump_count"]
        == action["observed_semantic_action_counts"]["deliberate_jump"]
    )
