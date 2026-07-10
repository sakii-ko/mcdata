from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from mcdata.config import ConfigError, load_yaml


CATALOG_SCHEMA_VERSION = 1
REQUIRED_STYLE_FAMILIES = (
    "realism_pbr",
    "architectural_cinematic",
    "vanilla_plus",
    "handpainted_fantasy_medieval",
    "cartoon_minimal",
    "retro_lowres",
    "scifi_cyber",
    "dark_horror",
    "accessibility_high_contrast",
)
UNKNOWN_LICENSES = {"unknown", "LicenseRef-Unknown"}
ALL_RIGHTS_RESERVED = "LicenseRef-All-Rights-Reserved"


class ResourcePackCatalogError(ValueError):
    """Raised when the candidate catalog cannot prove a safe declared state."""


def load_resourcepack_catalog(
    path: Path, *, asset_config_path: Path | None = None
) -> dict[str, Any]:
    """Load and fully validate the license-aware resource-pack catalog."""
    try:
        # The canonical YAML uses anchors only to reduce repeated rights declarations.
        # A JSON round trip detaches aliases so callers cannot mutate multiple candidates at once.
        document = json.loads(json.dumps(load_yaml(path)))
        asset_config = load_yaml(asset_config_path) if asset_config_path is not None else None
    except ConfigError as exc:
        raise ResourcePackCatalogError(str(exc)) from exc
    validate_resourcepack_catalog(document, asset_config=asset_config)
    return document


def validate_resourcepack_catalog(
    document: Mapping[str, Any], *, asset_config: Mapping[str, Any] | None = None
) -> None:
    """Fail closed on schema, rights, integration, or catalog identity drift."""
    catalog = deepcopy(dict(document))
    _validate_schema(catalog)
    _validate_required_families(catalog)
    candidates = catalog["candidates"]
    _validate_unique_fields(candidates)
    for candidate in candidates:
        _validate_candidate(candidate)
    if asset_config is not None:
        _validate_asset_mapping(candidates, asset_config)


def validate_lineage_split_assignments(
    document: Mapping[str, Any], assignments: Mapping[str, str]
) -> None:
    """Reject train/validation/test assignments that split one visual lineage."""
    candidates = {
        candidate["candidate_id"]: candidate
        for candidate in document.get("candidates", [])
        if isinstance(candidate, Mapping) and isinstance(candidate.get("candidate_id"), str)
    }
    unknown = sorted(set(assignments).difference(candidates))
    if unknown:
        raise ResourcePackCatalogError(
            "Lineage split assignment references unknown candidates: " + ", ".join(unknown)
        )
    lineage_splits: dict[str, set[str]] = defaultdict(set)
    for candidate_id, split in assignments.items():
        if not isinstance(split, str) or not split:
            raise ResourcePackCatalogError(
                f"Candidate {candidate_id!r} has an invalid empty split"
            )
        lineage_splits[candidates[candidate_id]["lineage_id"]].add(split)
    leaking = {
        lineage: sorted(splits)
        for lineage, splits in lineage_splits.items()
        if len(splits) > 1
    }
    if leaking:
        details = "; ".join(
            f"{lineage}={splits}" for lineage, splits in sorted(leaking.items())
        )
        raise ResourcePackCatalogError(f"Resource-pack lineage split leakage: {details}")


def catalog_coverage_report(
    document: Mapping[str, Any], *, asset_config: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return deterministic family, integration, rights, and asset-mapping coverage."""
    candidates = list(document.get("candidates", []))
    required = tuple(document.get("required_style_families", ()))
    by_family: dict[str, dict[str, int]] = {}
    for family in required:
        family_candidates = [item for item in candidates if item.get("style_family") == family]
        by_family[family] = {
            "candidates": len(family_candidates),
            "primary_styles": sum(
                item.get("candidate_role") == "primary_style" for item in family_candidates
            ),
            "configured": sum(
                item.get("integration", {}).get("status")
                in {"configured_not_runtime_verified", "runtime_verified"}
                for item in family_candidates
            ),
            "runtime_verified": sum(
                item.get("integration", {}).get("status") == "runtime_verified"
                for item in family_candidates
            ),
            "publishable_train": sum(
                item.get("eligibility", {}).get("train") == "publishable_train"
                for item in family_candidates
            ),
            "publishable_heldout": sum(
                item.get("eligibility", {}).get("heldout") == "publishable_heldout"
                for item in family_candidates
            ),
        }
    report: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "candidate_count": len(candidates),
        "lineage_count": len({item.get("lineage_id") for item in candidates}),
        "family_coverage": by_family,
        "missing_required_families": [
            family for family in required if by_family[family]["candidates"] == 0
        ],
        "families_without_publishable_train": [
            family for family in required if by_family[family]["publishable_train"] == 0
        ],
        "integration_status_counts": dict(
            sorted(Counter(item["integration"]["status"] for item in candidates).items())
        ),
        "access_tier_counts": dict(
            sorted(Counter(item["access_tier"] for item in candidates).items())
        ),
        "ml_training_permission_counts": dict(
            sorted(
                Counter(
                    item["permissions"]["ml_training"]["status"] for item in candidates
                ).items()
            )
        ),
        "redistribution_permission_counts": dict(
            sorted(
                Counter(
                    item["permissions"]["redistribution"]["status"]
                    for item in candidates
                ).items()
            )
        ),
    }
    if asset_config is not None:
        configured = set(_resourcepack_asset_keys(asset_config))
        mapped = {
            key
            for item in candidates
            for key in item["integration"]["configured_asset_keys"]
        }
        report["configured_asset_mapping"] = {
            "configured_count": len(configured),
            "mapped_count": len(configured.intersection(mapped)),
            "missing": sorted(configured.difference(mapped)),
            "unknown": sorted(mapped.difference(configured)),
        }
    return report


def publishable_training_blockers(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    """Explain why a candidate cannot enter a redistributable ML training set."""
    blockers: list[str] = []
    access_tier = candidate["access_tier"]
    license_id = candidate["license"]["spdx"]
    permissions = candidate["permissions"]
    authorization = permissions["written_authorization"]
    has_override = authorization["status"] == "granted"

    if license_id in UNKNOWN_LICENSES:
        blockers.append("license_unknown")
    if access_tier == "paid" and not has_override:
        blockers.append("paid_without_written_authorization")
    if license_id == ALL_RIGHTS_RESERVED and not has_override:
        blockers.append("all_rights_reserved_without_written_authorization")
    if permissions["ml_training"]["status"] != "explicit":
        blockers.append("ml_training_permission_not_explicit")
    if permissions["redistribution"]["status"] != "explicit":
        blockers.append("redistribution_permission_not_explicit")
    if candidate["integration"]["status"] != "runtime_verified":
        blockers.append("runtime_not_verified")
    return tuple(blockers)


def _validate_schema(document: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - required project dependency
        raise ResourcePackCatalogError("Resource-pack catalog requires jsonschema") from exc
    schema_path = Path(__file__).parent / "schemas" / "resourcepack_catalog.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "root"
    raise ResourcePackCatalogError(
        f"Resource-pack catalog violates schema at {location}: {error.message}"
    )


def _validate_required_families(document: Mapping[str, Any]) -> None:
    declared = tuple(document["required_style_families"])
    if declared != REQUIRED_STYLE_FAMILIES:
        raise ResourcePackCatalogError(
            "required_style_families must equal taxonomy v1 in canonical order"
        )
    covered = {candidate["style_family"] for candidate in document["candidates"]}
    missing = [family for family in REQUIRED_STYLE_FAMILIES if family not in covered]
    if missing:
        raise ResourcePackCatalogError(
            "Resource-pack catalog is missing required style families: " + ", ".join(missing)
        )


def _validate_unique_fields(candidates: Iterable[Mapping[str, Any]]) -> None:
    candidate_ids: set[str] = set()
    asset_keys: set[str] = set()
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        if candidate_id in candidate_ids:
            raise ResourcePackCatalogError(f"Duplicate candidate ID: {candidate_id!r}")
        candidate_ids.add(candidate_id)
        for asset_key in candidate["integration"]["configured_asset_keys"]:
            if asset_key in asset_keys:
                raise ResourcePackCatalogError(
                    f"Configured asset key is mapped more than once: {asset_key!r}"
                )
            asset_keys.add(asset_key)


def _validate_candidate(candidate: Mapping[str, Any]) -> None:
    _validate_integration(candidate)
    _validate_resolution(candidate)
    _validate_permissions(candidate)
    _validate_eligibility(candidate)


def _validate_integration(candidate: Mapping[str, Any]) -> None:
    integration = candidate["integration"]
    status = integration["status"]
    verified = integration["runtime_verified_game_versions"]
    evidence = integration["evidence_paths"]
    configured = integration["configured_asset_keys"]
    if status == "runtime_verified":
        if not verified or not evidence:
            raise ResourcePackCatalogError(
                f"Runtime-verified candidate {candidate['candidate_id']!r} needs version and evidence"
            )
    elif verified or evidence:
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} claims runtime evidence without verified status"
        )
    if status == "configured_not_runtime_verified" and not configured:
        raise ResourcePackCatalogError(
            f"Configured candidate {candidate['candidate_id']!r} has no asset key"
        )
    if status in {"research_only", "compatibility_unknown"} and configured:
        raise ResourcePackCatalogError(
            f"Research candidate {candidate['candidate_id']!r} cannot claim configured asset keys"
        )
    for path in evidence:
        _validate_relative_path(path, label="runtime evidence")


def _validate_resolution(candidate: Mapping[str, Any]) -> None:
    resolution = candidate["resolution"]
    values = resolution["values_px"]
    if resolution["status"] in {"exact", "range", "mixed"} and not values:
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} must name its texture resolution"
        )
    if resolution["status"] in {"unknown", "not_applicable"} and values:
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} has values for an unknown/N/A resolution"
        )
    if values != sorted(values):
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} resolution values must be sorted"
        )


def _validate_permissions(candidate: Mapping[str, Any]) -> None:
    permissions = candidate["permissions"]
    for permission_name in ("ml_training", "redistribution"):
        permission = permissions[permission_name]
        if permission["status"] == "explicit" and permission["evidence_url"] is None:
            raise ResourcePackCatalogError(
                f"Explicit {permission_name} permission for {candidate['candidate_id']!r} "
                "requires an evidence URL"
            )
        if permission["status"] == "unknown" and permission["evidence_url"] is not None:
            raise ResourcePackCatalogError(
                f"Unknown {permission_name} permission for {candidate['candidate_id']!r} "
                "cannot cite evidence"
            )
    authorization = permissions["written_authorization"]
    if authorization["status"] == "granted":
        if not all(
            authorization[field] for field in ("evidence_path", "scope", "rights_holder")
        ):
            raise ResourcePackCatalogError(
                f"Granted authorization for {candidate['candidate_id']!r} is incomplete"
            )
        _validate_relative_path(authorization["evidence_path"], label="authorization evidence")
    elif any(
        authorization[field] is not None
        for field in ("evidence_path", "scope", "rights_holder")
    ):
        raise ResourcePackCatalogError(
            f"Non-granted authorization for {candidate['candidate_id']!r} must not claim scope"
        )


def _validate_eligibility(candidate: Mapping[str, Any]) -> None:
    permissions = candidate["permissions"]
    blockers = publishable_training_blockers(candidate)
    eligibility = candidate["eligibility"]
    if eligibility["train"] == "publishable_train" and blockers:
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} cannot be publishable_train: "
            + ", ".join(blockers)
        )
    if eligibility["heldout"] == "publishable_heldout" and blockers:
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} cannot be publishable_heldout: "
            + ", ".join(blockers)
        )
    if permissions["ml_training"]["status"] == "denied" and eligibility["train"] != "denied":
        raise ResourcePackCatalogError(
            f"Candidate {candidate['candidate_id']!r} has denied ML permission but is not denied"
        )


def _validate_asset_mapping(
    candidates: Iterable[Mapping[str, Any]], asset_config: Mapping[str, Any]
) -> None:
    expected = set(_resourcepack_asset_keys(asset_config))
    mapped = {
        key
        for candidate in candidates
        for key in candidate["integration"]["configured_asset_keys"]
    }
    missing = sorted(expected.difference(mapped))
    unknown = sorted(mapped.difference(expected))
    if missing or unknown:
        raise ResourcePackCatalogError(
            f"Resource-pack asset mapping mismatch: missing={missing!r}, unknown={unknown!r}"
        )


def _resourcepack_asset_keys(asset_config: Mapping[str, Any]) -> tuple[str, ...]:
    assets = asset_config.get("assets", {})
    resourcepacks = assets.get("resourcepacks", {}) if isinstance(assets, Mapping) else {}
    if not isinstance(resourcepacks, Mapping):
        raise ResourcePackCatalogError("asset_sets resourcepacks must be a mapping")
    return tuple(sorted(str(key) for key in resourcepacks))


def _validate_relative_path(value: str, *, label: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ResourcePackCatalogError(f"Unsafe {label} path: {value!r}")
