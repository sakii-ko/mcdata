from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any

from mcdata.action_curriculum import BUCKET_BY_LEVEL, TAXONOMY_VERSION
from mcdata.dataset_support.core import (
    DatasetValidationError,
    canonical_bytes,
    value_sha256,
)

SCHEMA_VERSION = 1
POLICY_NAME = "stratified_edit_pair_schedule_v1"
BUCKETS = tuple(BUCKET_BY_LEVEL[level] for level in sorted(BUCKET_BY_LEVEL))
_PAIR_ID_RE = re.compile(r"^pair-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATASET_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SIGNED_64_MIN = -(2**63)
_SIGNED_64_MAX = 2**63 - 1


class CurriculumPlanError(ValueError):
    """Raised when an accepted dataset cannot produce a curriculum schedule."""


def parse_ratio_assignments(assignments: Sequence[str]) -> dict[str, float]:
    """Parse four explicit ``bucket=value`` CLI assignments."""
    parsed: dict[str, Decimal] = {}
    for assignment in assignments:
        if not isinstance(assignment, str) or assignment.count("=") != 1:
            raise CurriculumPlanError(
                "Each --ratio must use bucket=value (for example l1=0.75)"
            )
        bucket, raw_value = (part.strip() for part in assignment.split("=", 1))
        if bucket not in BUCKETS:
            raise CurriculumPlanError(
                f"Unknown action bucket {bucket!r}; expected {list(BUCKETS)!r}"
            )
        if bucket in parsed:
            raise CurriculumPlanError(f"Duplicate ratio for action bucket {bucket!r}")
        try:
            value = Decimal(raw_value)
        except InvalidOperation as exc:
            raise CurriculumPlanError(f"Ratio for {bucket!r} is not numeric") from exc
        parsed[bucket] = value
    _decimals, normalised = _normalise_ratios(parsed)
    return normalised


def build_curriculum_plan(
    dataset_index: Mapping[str, Any],
    *,
    source_index_sha256: str,
    stage_name: str,
    ratios: Mapping[str, object],
    epoch: int,
    sample_count: int,
    seed: int,
    allowed_pair_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, content-addressed edit-pair curriculum plan."""
    _validate_stage(stage_name)
    _validate_int(epoch, "epoch", minimum=0)
    _validate_int(sample_count, "sample_count", minimum=1)
    _validate_int(seed, "seed", minimum=_SIGNED_64_MIN, maximum=_SIGNED_64_MAX)
    if not isinstance(source_index_sha256, str) or not _SHA256_RE.fullmatch(
        source_index_sha256
    ):
        raise CurriculumPlanError("source index SHA-256 must be 64 lowercase hex characters")
    dataset_id, pair_ids_by_bucket = _validate_source_index(dataset_index)
    ratio_decimals, normalised_ratios = _normalise_ratios(ratios)
    eligible, pair_filter = _filter_pairs(pair_ids_by_bucket, allowed_pair_ids)
    for bucket in BUCKETS:
        if ratio_decimals[bucket] > 0 and not eligible[bucket]:
            raise CurriculumPlanError(
                f"Positive ratio for {bucket!r} references an empty eligible pair bucket"
            )
    bucket_counts = _apportion_counts(ratio_decimals, sample_count)
    schedule = _expand_schedule(
        dataset_id=dataset_id,
        stage_name=stage_name,
        epoch=epoch,
        seed=seed,
        bucket_counts=bucket_counts,
        eligible_pair_ids=eligible,
    )
    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": "mcdata.curriculum-plan", "policy_version": 1},
        "stage_name": stage_name,
        "source_index": {
            "schema_version": 2,
            "dataset_id": dataset_id,
            "sha256": source_index_sha256,
            "status": "accepted",
        },
        "action_taxonomy_version": TAXONOMY_VERSION,
        "sampling_unit": "edit_pair",
        "epoch": epoch,
        "sample_count": sample_count,
        "seed": seed,
        "policy": {
            "name": POLICY_NAME,
            "ratios": normalised_ratios,
            "apportionment": "largest_remainder_fixed_bucket_order",
            "replacement": "sha256_cycle_after_exhaustion",
            "tie_break_order": list(BUCKETS),
            "pair_filter": pair_filter,
        },
        "bucket_counts": bucket_counts,
        "eligible_pair_ids": eligible,
        "schedule": schedule,
    }
    plan["plan_id"] = f"sha256:{value_sha256(plan)}"
    validate_curriculum_plan(plan)
    return plan


def validate_curriculum_plan(value: Any) -> None:
    """Validate plan structure, content ID, counts, and deterministic expansion."""
    if not isinstance(value, dict):
        raise CurriculumPlanError("Curriculum plan must be a JSON object")
    required = {
        "schema_version",
        "generator",
        "plan_id",
        "stage_name",
        "source_index",
        "action_taxonomy_version",
        "sampling_unit",
        "epoch",
        "sample_count",
        "seed",
        "policy",
        "bucket_counts",
        "eligible_pair_ids",
        "schedule",
    }
    if set(value) != required:
        raise CurriculumPlanError("Curriculum plan has an unstable top-level field set")
    _validate_plan_identity(value)
    policy = value.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "name",
        "ratios",
        "apportionment",
        "replacement",
        "tie_break_order",
        "pair_filter",
    }:
        raise CurriculumPlanError("Curriculum plan policy has an unstable field set")
    if (
        policy.get("name") != POLICY_NAME
        or policy.get("apportionment") != "largest_remainder_fixed_bucket_order"
        or policy.get("replacement") != "sha256_cycle_after_exhaustion"
        or policy.get("tie_break_order") != list(BUCKETS)
    ):
        raise CurriculumPlanError("Curriculum plan policy declaration is unsupported")
    ratios, normalised = _normalise_ratios(policy.get("ratios"))
    if policy["ratios"] != normalised:
        raise CurriculumPlanError("Curriculum plan ratios are not normalised numbers")
    counts = _validate_bucket_counts(value.get("bucket_counts"), value["sample_count"])
    if counts != _apportion_counts(ratios, value["sample_count"]):
        raise CurriculumPlanError("Curriculum plan bucket counts do not match its ratios")
    eligible = _validate_eligible_pairs(value.get("eligible_pair_ids"))
    _validate_pair_filter(policy.get("pair_filter"), eligible)
    for bucket in BUCKETS:
        if ratios[bucket] > 0 and not eligible[bucket]:
            raise CurriculumPlanError(f"Curriculum plan has an empty positive-ratio {bucket}")
    expected_schedule = _expand_schedule(
        dataset_id=value["source_index"]["dataset_id"],
        stage_name=value["stage_name"],
        epoch=value["epoch"],
        seed=value["seed"],
        bucket_counts=counts,
        eligible_pair_ids=eligible,
    )
    if value.get("schedule") != expected_schedule:
        raise CurriculumPlanError("Curriculum plan schedule is not the declared stable expansion")
    unsigned = dict(value)
    plan_id = unsigned.pop("plan_id")
    if plan_id != f"sha256:{value_sha256(unsigned)}":
        raise CurriculumPlanError("Curriculum plan ID does not match its canonical content")


def _validate_source_index(index: Mapping[str, Any]) -> tuple[str, dict[str, list[str]]]:
    if not isinstance(index, dict) or index.get("schema_version") != 2:
        raise CurriculumPlanError("Curriculum planning requires dataset index schema v2")
    if index.get("status") != "accepted":
        raise CurriculumPlanError("Curriculum planning requires status='accepted'")
    dataset_id = index.get("dataset_id")
    if not isinstance(dataset_id, str) or not _DATASET_ID_RE.fullmatch(dataset_id):
        raise CurriculumPlanError("Dataset index has an invalid dataset_id")
    unsigned = dict(index)
    unsigned.pop("dataset_id", None)
    try:
        expected_dataset_id = f"sha256:{value_sha256(unsigned)}"
    except DatasetValidationError as exc:
        raise CurriculumPlanError("Dataset index is not canonical JSON") from exc
    if dataset_id != expected_dataset_id:
        raise CurriculumPlanError("Dataset index dataset_id does not match its canonical content")
    episode_buckets = _validate_episode_buckets(index.get("episodes"))
    _validate_declared_buckets(index.get("action_buckets"), episode_buckets)
    return dataset_id, _validate_source_pairs(index.get("pairs"), episode_buckets)


def _validate_episode_buckets(value: Any) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise CurriculumPlanError("Dataset index episodes must be a non-empty list")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, dict) or item.get("accepted") is not True:
            raise CurriculumPlanError("Every curriculum episode must be accepted")
        episode_id = item.get("episode_id")
        action = item.get("action_curriculum")
        if not isinstance(episode_id, str) or not episode_id:
            raise CurriculumPlanError("Every curriculum episode needs a non-empty episode_id")
        if episode_id in result:
            raise CurriculumPlanError(f"Duplicate curriculum episode ID: {episode_id!r}")
        if not isinstance(action, dict) or action.get("taxonomy_version") != TAXONOMY_VERSION:
            raise CurriculumPlanError(f"Episode {episode_id!r} has an invalid action taxonomy")
        bucket = action.get("bucket")
        if bucket not in BUCKETS:
            raise CurriculumPlanError(f"Episode {episode_id!r} has an invalid action bucket")
        result[episode_id] = bucket
    return result


def _validate_declared_buckets(value: Any, episode_buckets: Mapping[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != {"taxonomy_version", *BUCKETS}:
        raise CurriculumPlanError("Dataset index action_buckets has an unstable field set")
    if value.get("taxonomy_version") != TAXONOMY_VERSION:
        raise CurriculumPlanError("Dataset index action bucket taxonomy is unsupported")
    for bucket in BUCKETS:
        declaration = value.get(bucket)
        expected = sorted(
            episode_id for episode_id, claimed in episode_buckets.items() if claimed == bucket
        )
        if not isinstance(declaration, dict) or set(declaration) != {
            "episode_count",
            "episode_ids",
        }:
            raise CurriculumPlanError(f"Dataset action bucket {bucket!r} is malformed")
        if declaration.get("episode_count") != len(expected) or declaration.get(
            "episode_ids"
        ) != expected:
            raise CurriculumPlanError(
                f"Dataset action bucket {bucket!r} disagrees with episode claims"
            )


def _validate_source_pairs(
    value: Any, episode_buckets: Mapping[str, str]
) -> dict[str, list[str]]:
    if not isinstance(value, list) or not value:
        raise CurriculumPlanError("Dataset index pairs must be a non-empty list")
    result = {bucket: [] for bucket in BUCKETS}
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise CurriculumPlanError("Every dataset edit pair must be an object")
        pair_id = item.get("pair_id")
        source_id = item.get("source_episode")
        target_id = item.get("target_episode")
        if not isinstance(pair_id, str) or not _PAIR_ID_RE.fullmatch(pair_id):
            raise CurriculumPlanError("Every dataset edit pair needs a valid pair_id")
        if pair_id in seen:
            raise CurriculumPlanError(f"Duplicate dataset pair ID: {pair_id!r}")
        seen.add(pair_id)
        if (
            not isinstance(source_id, str)
            or not isinstance(target_id, str)
            or source_id == target_id
            or source_id not in episode_buckets
            or target_id not in episode_buckets
        ):
            raise CurriculumPlanError(f"Pair {pair_id!r} has missing or identical endpoints")
        source_bucket = episode_buckets[source_id]
        target_bucket = episode_buckets[target_id]
        if source_bucket != target_bucket:
            raise CurriculumPlanError(
                f"Pair {pair_id!r} crosses action buckets: {source_bucket!r} -> {target_bucket!r}"
            )
        result[source_bucket].append(pair_id)
    return {bucket: sorted(result[bucket]) for bucket in BUCKETS}


def _normalise_ratios(
    value: Mapping[str, object] | Any,
) -> tuple[dict[str, Decimal], dict[str, float]]:
    if not isinstance(value, Mapping) or set(value) != set(BUCKETS):
        raise CurriculumPlanError(f"Ratios must specify exactly {list(BUCKETS)!r}")
    decimals: dict[str, Decimal] = {}
    for bucket in BUCKETS:
        raw = value[bucket]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
            raise CurriculumPlanError(f"Ratio for {bucket!r} must be a finite number")
        try:
            ratio = Decimal(str(raw))
        except InvalidOperation as exc:
            raise CurriculumPlanError(f"Ratio for {bucket!r} must be a finite number") from exc
        if not ratio.is_finite() or not math.isfinite(float(ratio)):
            raise CurriculumPlanError(f"Ratio for {bucket!r} must be finite")
        if ratio < 0:
            raise CurriculumPlanError(f"Ratio for {bucket!r} must be non-negative")
        decimals[bucket] = Decimal(0) if ratio == 0 else ratio
    if sum(decimals.values(), Decimal(0)) != Decimal(1):
        raise CurriculumPlanError("Action bucket ratios must sum exactly to 1")
    normalised = {bucket: float(decimals[bucket]) for bucket in BUCKETS}
    if any(decimals[bucket] > 0 and normalised[bucket] == 0 for bucket in BUCKETS):
        raise CurriculumPlanError("Action bucket ratio is too small for stable JSON encoding")
    roundtrip = {bucket: Decimal(str(normalised[bucket])) for bucket in BUCKETS}
    if sum(roundtrip.values(), Decimal(0)) != Decimal(1):
        raise CurriculumPlanError("Action bucket ratios are not exactly representable in JSON")
    return roundtrip, normalised


def _filter_pairs(
    pair_ids_by_bucket: Mapping[str, Sequence[str]], allowed_pair_ids: Sequence[str] | None
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    if allowed_pair_ids is None:
        return (
            {bucket: list(pair_ids_by_bucket[bucket]) for bucket in BUCKETS},
            {"mode": "all_eligible_pairs"},
        )
    if isinstance(allowed_pair_ids, (str, bytes)) or not allowed_pair_ids:
        raise CurriculumPlanError("Explicit pair allowlist must be a non-empty sequence")
    allowed = list(allowed_pair_ids)
    if any(not isinstance(pair_id, str) for pair_id in allowed) or len(allowed) != len(
        set(allowed)
    ):
        raise CurriculumPlanError("Explicit pair allowlist IDs must be strings and unique")
    all_pairs = {pair_id for bucket in BUCKETS for pair_id in pair_ids_by_bucket[bucket]}
    unknown = sorted(set(allowed) - all_pairs)
    if unknown:
        raise CurriculumPlanError(f"Explicit pair allowlist contains unknown IDs: {unknown!r}")
    allowed_set = set(allowed)
    result = {
        bucket: [pair_id for pair_id in pair_ids_by_bucket[bucket] if pair_id in allowed_set]
        for bucket in BUCKETS
    }
    return result, {"mode": "explicit_pair_ids", "pair_ids": sorted(allowed_set)}


def _apportion_counts(ratios: Mapping[str, Decimal], sample_count: int) -> dict[str, int]:
    quotas = {bucket: ratios[bucket] * sample_count for bucket in BUCKETS}
    counts = {
        bucket: int(quotas[bucket].to_integral_value(rounding=ROUND_FLOOR))
        for bucket in BUCKETS
    }
    remaining = sample_count - sum(counts.values())
    order = sorted(
        BUCKETS,
        key=lambda bucket: (-(quotas[bucket] - counts[bucket]), BUCKETS.index(bucket)),
    )
    for bucket in order[:remaining]:
        counts[bucket] += 1
    return counts


def _expand_schedule(
    *,
    dataset_id: str,
    stage_name: str,
    epoch: int,
    seed: int,
    bucket_counts: Mapping[str, int],
    eligible_pair_ids: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    selected: dict[str, list[str]] = {}
    slots: list[tuple[str, int, int, str]] = []
    for bucket_index, bucket in enumerate(BUCKETS):
        selected[bucket] = _pair_cycle(
            eligible_pair_ids[bucket],
            bucket_counts[bucket],
            dataset_id=dataset_id,
            stage_name=stage_name,
            epoch=epoch,
            seed=seed,
            bucket=bucket,
        )
        for ordinal in range(bucket_counts[bucket]):
            key = _stable_digest(
                "schedule-slot", dataset_id, stage_name, epoch, seed, bucket, ordinal
            )
            slots.append((key, bucket_index, ordinal, bucket))
    slots.sort()
    offsets = {bucket: 0 for bucket in BUCKETS}
    schedule = []
    for sample_index, (_key, _bucket_index, _slot_ordinal, bucket) in enumerate(slots):
        pair_ordinal = offsets[bucket]
        schedule.append(
            {
                "sample_index": sample_index,
                "bucket": bucket,
                "pair_id": selected[bucket][pair_ordinal],
            }
        )
        offsets[bucket] += 1
    return schedule


def _pair_cycle(
    pair_ids: Sequence[str],
    count: int,
    *,
    dataset_id: str,
    stage_name: str,
    epoch: int,
    seed: int,
    bucket: str,
) -> list[str]:
    if count == 0:
        return []
    if not pair_ids:
        raise CurriculumPlanError(f"Cannot expand non-empty {bucket!r} from no eligible pairs")
    result: list[str] = []
    cycle = 0
    while len(result) < count:
        ranked = sorted(
            pair_ids,
            key=lambda pair_id: (
                _stable_digest(
                    "pair-cycle",
                    dataset_id,
                    stage_name,
                    epoch,
                    seed,
                    bucket,
                    cycle,
                    pair_id,
                ),
                pair_id,
            ),
        )
        result.extend(ranked[: count - len(result)])
        cycle += 1
    return result


def _stable_digest(*parts: object) -> str:
    return hashlib.sha256(canonical_bytes(list(parts))).hexdigest()


def _validate_plan_identity(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != SCHEMA_VERSION:
        raise CurriculumPlanError("Unsupported curriculum plan schema version")
    if value.get("generator") != {
        "name": "mcdata.curriculum-plan",
        "policy_version": 1,
    }:
        raise CurriculumPlanError("Unsupported curriculum plan generator")
    _validate_stage(value.get("stage_name"))
    _validate_int(value.get("epoch"), "epoch", minimum=0)
    _validate_int(value.get("sample_count"), "sample_count", minimum=1)
    _validate_int(
        value.get("seed"), "seed", minimum=_SIGNED_64_MIN, maximum=_SIGNED_64_MAX
    )
    source = value.get("source_index")
    if not isinstance(source, dict) or set(source) != {
        "schema_version",
        "dataset_id",
        "sha256",
        "status",
    }:
        raise CurriculumPlanError("Curriculum plan source_index has an unstable field set")
    if (
        source.get("schema_version") != 2
        or source.get("status") != "accepted"
        or not isinstance(source.get("dataset_id"), str)
        or not _DATASET_ID_RE.fullmatch(source["dataset_id"])
        or not isinstance(source.get("sha256"), str)
        or not _SHA256_RE.fullmatch(source["sha256"])
    ):
        raise CurriculumPlanError("Curriculum plan source_index identity is invalid")
    if value.get("action_taxonomy_version") != TAXONOMY_VERSION:
        raise CurriculumPlanError("Curriculum plan action taxonomy is unsupported")
    if value.get("sampling_unit") != "edit_pair":
        raise CurriculumPlanError("Curriculum plan sampling unit must be edit_pair")


def _validate_bucket_counts(value: Any, sample_count: int) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(BUCKETS):
        raise CurriculumPlanError("Curriculum plan bucket_counts is malformed")
    result: dict[str, int] = {}
    for bucket in BUCKETS:
        _validate_int(value[bucket], f"bucket count {bucket}", minimum=0)
        result[bucket] = value[bucket]
    if sum(result.values()) != sample_count:
        raise CurriculumPlanError("Curriculum plan bucket counts do not sum to sample_count")
    return result


def _validate_eligible_pairs(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != set(BUCKETS):
        raise CurriculumPlanError("Curriculum plan eligible_pair_ids is malformed")
    result: dict[str, list[str]] = {}
    globally_seen: set[str] = set()
    for bucket in BUCKETS:
        pair_ids = value[bucket]
        if not _is_sorted_unique_pair_ids(pair_ids, allow_empty=True):
            raise CurriculumPlanError(f"Eligible pair IDs for {bucket!r} are invalid")
        overlap = globally_seen.intersection(pair_ids)
        if overlap:
            raise CurriculumPlanError(f"Eligible pairs appear in multiple buckets: {sorted(overlap)!r}")
        globally_seen.update(pair_ids)
        result[bucket] = pair_ids
    return result


def _validate_pair_filter(value: Any, eligible: Mapping[str, Sequence[str]]) -> None:
    if value == {"mode": "all_eligible_pairs"}:
        return
    if not isinstance(value, dict) or set(value) != {"mode", "pair_ids"}:
        raise CurriculumPlanError("Curriculum plan pair_filter is malformed")
    pair_ids = value.get("pair_ids")
    if value.get("mode") != "explicit_pair_ids" or not _is_sorted_unique_pair_ids(
        pair_ids, allow_empty=False
    ):
        raise CurriculumPlanError("Curriculum plan explicit pair filter is invalid")
    eligible_union = sorted(pair_id for bucket in BUCKETS for pair_id in eligible[bucket])
    if pair_ids != eligible_union:
        raise CurriculumPlanError("Curriculum plan pair filter does not match eligible pair IDs")


def _validate_stage(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise CurriculumPlanError(
            "stage_name must be 1-128 characters with no surrounding/control whitespace"
        )


def _is_sorted_unique_pair_ids(value: Any, *, allow_empty: bool) -> bool:
    if not isinstance(value, list) or (not allow_empty and not value):
        return False
    if any(not isinstance(pair_id, str) or not _PAIR_ID_RE.fullmatch(pair_id) for pair_id in value):
        return False
    return value == sorted(value) and len(value) == len(set(value))


def _validate_int(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">={minimum}"
        raise CurriculumPlanError(f"{label} must be an integer in {bounds}")
