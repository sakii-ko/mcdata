from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcdata.dataset_support.core import DatasetValidationError, validate_index_schema
from mcdata.dataset_support.curriculum import CurriculumPlanError, build_curriculum_plan


def write_curriculum_plan(
    dataset_index_path: Path,
    out: Path,
    *,
    stage_name: str,
    ratios: Mapping[str, object],
    epoch: int,
    sample_count: int,
    seed: int,
    allowed_pair_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Validate an on-disk accepted index and atomically write its derived plan."""
    if dataset_index_path.is_symlink():
        raise CurriculumPlanError(f"Dataset index is missing or unsafe: {dataset_index_path}")
    source = dataset_index_path.resolve()
    destination = out.resolve()
    if source == destination:
        raise CurriculumPlanError("Curriculum plan output must not overwrite dataset_index.json")
    if not source.is_file():
        raise CurriculumPlanError(f"Dataset index is missing or unsafe: {source}")
    try:
        raw = source.read_bytes()
        dataset_index = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CurriculumPlanError(f"Could not read valid dataset index JSON: {source}") from exc
    if not isinstance(dataset_index, dict):
        raise CurriculumPlanError(f"Dataset index must be a JSON object: {source}")
    try:
        validate_index_schema(dataset_index)
    except DatasetValidationError as exc:
        raise CurriculumPlanError(str(exc)) from exc
    plan = build_curriculum_plan(
        dataset_index,
        source_index_sha256=hashlib.sha256(raw).hexdigest(),
        stage_name=stage_name,
        ratios=ratios,
        epoch=epoch,
        sample_count=sample_count,
        seed=seed,
        allowed_pair_ids=allowed_pair_ids,
    )
    temporary = destination.with_name(f"{destination.name}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
        temporary.write_text(
            json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    except OSError as exc:
        raise CurriculumPlanError(f"Could not write curriculum plan: {destination}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return plan
