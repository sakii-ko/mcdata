from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcdata.config import load_yaml
from mcdata.resourcepack_catalog import (
    REQUIRED_STYLE_FAMILIES,
    ResourcePackCatalogError,
    catalog_coverage_report,
    load_resourcepack_catalog,
    publishable_training_blockers,
    validate_lineage_split_assignments,
    validate_resourcepack_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "configs" / "resourcepack_catalog.yml"
ASSET_CONFIG_PATH = ROOT / "configs" / "asset_sets.yml"


def _catalog() -> dict:
    # Round-trip copying also detaches YAML aliases before mutation tests.
    return json.loads(json.dumps(load_yaml(CATALOG_PATH)))


def _candidate(document: dict, candidate_id: str) -> dict:
    return next(
        item for item in document["candidates"] if item["candidate_id"] == candidate_id
    )


def test_canonical_catalog_validates_and_maps_every_configured_resourcepack() -> None:
    document = load_resourcepack_catalog(
        CATALOG_PATH,
        asset_config_path=ASSET_CONFIG_PATH,
    )
    report = catalog_coverage_report(document, asset_config=load_yaml(ASSET_CONFIG_PATH))

    assert tuple(document["required_style_families"]) == REQUIRED_STYLE_FAMILIES
    assert report["candidate_count"] == 43
    assert report["lineage_count"] == 36
    assert report["missing_required_families"] == []
    assert report["configured_asset_mapping"] == {
        "configured_count": 33,
        "mapped_count": 33,
        "missing": [],
        "unknown": [],
    }


def test_every_style_family_has_a_primary_candidate_except_builtin_ui_control() -> None:
    report = catalog_coverage_report(_catalog())

    for family, coverage in report["family_coverage"].items():
        if family == "accessibility_high_contrast":
            assert coverage["primary_styles"] == 0
            assert coverage["candidates"] == 1
        else:
            assert coverage["primary_styles"] >= 1


def test_named_diversity_candidates_are_recorded_without_fake_runtime_claims() -> None:
    document = _catalog()
    expected = {
        "patrix-32x-free": "realism_pbr",
        "patrix-hires-paid": "realism_pbr",
        "modernarch-128x-free": "architectural_cinematic",
        "modernarch-hires-paid": "architectural_cinematic",
        "luna-hd-64x-free": "architectural_cinematic",
        "luna-hd-hires-paid": "architectural_cinematic",
        "genesis-scifi-research": "scifi_cyber",
        "bare-bones-16x-free": "cartoon_minimal",
        "f8thful-8x-research": "retro_lowres",
        "conquest-32x-research": "handpainted_fantasy_medieval",
        "excalibur-16x-research": "handpainted_fantasy_medieval",
        "ms-painted-128x-research": "cartoon_minimal",
        "minecraft-high-contrast-builtin": "accessibility_high_contrast",
    }
    for candidate_id, family in expected.items():
        candidate = _candidate(document, candidate_id)
        assert candidate["style_family"] == family
        assert candidate["integration"]["status"] != "runtime_verified"
        assert candidate["integration"]["runtime_verified_game_versions"] == []
        assert candidate["integration"]["evidence_paths"] == []


def test_resolution_variants_share_lineage() -> None:
    document = _catalog()

    assert {
        _candidate(document, candidate_id)["lineage_id"]
        for candidate_id in ("patrix-32x-free", "patrix-hires-paid")
    } == {"patrix"}
    assert {
        _candidate(document, candidate_id)["lineage_id"]
        for candidate_id in ("modernarch-128x-free", "modernarch-hires-paid")
    } == {"modernarch"}
    assert {
        _candidate(document, candidate_id)["lineage_id"]
        for candidate_id in ("luna-hd-64x-free", "luna-hd-hires-paid")
    } == {"luna-hd"}
    assert {
        _candidate(document, candidate_id)["lineage_id"]
        for candidate_id in ("default-hd-64x-free", "default-hd-128x-free")
    } == {"default-hd"}


@pytest.mark.parametrize(
    ("candidate_id", "blocker"),
    [
        ("modernarch-hires-paid", "paid_without_written_authorization"),
        ("bare-bones-16x-free", "all_rights_reserved_without_written_authorization"),
        ("luna-hd-64x-free", "license_unknown"),
        ("yitalith-128x-free", "ml_training_permission_not_explicit"),
    ],
)
def test_publishable_train_declaration_fails_closed(
    candidate_id: str, blocker: str
) -> None:
    document = _catalog()
    candidate = _candidate(document, candidate_id)
    candidate["eligibility"]["train"] = "publishable_train"

    with pytest.raises(ResourcePackCatalogError, match=blocker):
        validate_resourcepack_catalog(document)


def test_open_redistribution_is_not_treated_as_explicit_ml_permission() -> None:
    candidate = _candidate(_catalog(), "yitalith-128x-free")

    assert candidate["license"]["spdx"] == "GPL-3.0-or-later"
    assert candidate["permissions"]["redistribution"]["status"] == "explicit"
    assert candidate["permissions"]["ml_training"]["status"] == "unknown"
    assert "ml_training_permission_not_explicit" in publishable_training_blockers(candidate)


def test_video_permission_does_not_upgrade_ml_permission() -> None:
    candidate = _candidate(_catalog(), "excalibur-16x-research")

    assert candidate["permissions"]["redistribution"]["status"] == "denied"
    assert candidate["permissions"]["ml_training"]["status"] == "unknown"
    assert candidate["eligibility"] == {
        "train": "research_only",
        "heldout": "research_only",
    }


def test_synthetic_open_candidate_can_be_publishable_only_after_all_gates() -> None:
    document = _catalog()
    candidate = _candidate(document, "yitalith-128x-free")
    candidate["integration"] = {
        **candidate["integration"],
        "status": "runtime_verified",
        "runtime_verified_game_versions": ["26.2"],
        "evidence_paths": ["runs/evidence/synthetic/runtime_audit.json"],
    }
    candidate["permissions"]["ml_training"] = {
        "status": "explicit",
        "evidence_url": "https://modrinth.com/resourcepack/yitalith",
    }
    candidate["eligibility"] = {
        "train": "publishable_train",
        "heldout": "publishable_heldout",
    }

    validate_resourcepack_catalog(document)
    assert publishable_training_blockers(candidate) == ()


def test_paid_arr_candidate_requires_complete_written_authorization_override() -> None:
    document = _catalog()
    candidate = _candidate(document, "modernarch-hires-paid")
    candidate["integration"] = {
        **candidate["integration"],
        "status": "runtime_verified",
        "runtime_verified_game_versions": ["26.2"],
        "evidence_paths": ["runs/evidence/synthetic/runtime_audit.json"],
    }
    candidate["permissions"] = {
        "ml_training": {
            "status": "explicit",
            "evidence_url": "https://www.designio.graphics/contact",
        },
        "redistribution": {
            "status": "explicit",
            "evidence_url": "https://www.designio.graphics/contact",
        },
        "written_authorization": {
            "status": "granted",
            "evidence_path": "private/authorizations/modernarch.json",
            "scope": "ML training and derived-video redistribution",
            "rights_holder": "Synthetic test rights holder",
        },
    }
    candidate["eligibility"] = {
        "train": "publishable_train",
        "heldout": "publishable_heldout",
    }

    validate_resourcepack_catalog(document)
    assert publishable_training_blockers(candidate) == ()


def test_incomplete_granted_authorization_fails() -> None:
    document = _catalog()
    candidate = _candidate(document, "modernarch-hires-paid")
    candidate["permissions"]["written_authorization"] = {
        "status": "granted",
        "evidence_path": None,
        "scope": "ML training",
        "rights_holder": "A rights holder",
    }

    with pytest.raises(ResourcePackCatalogError, match="authorization.*incomplete"):
        validate_resourcepack_catalog(document)


def test_lineage_split_validation_rejects_resolution_leakage() -> None:
    document = _catalog()

    with pytest.raises(ResourcePackCatalogError, match="lineage split leakage.*patrix"):
        validate_lineage_split_assignments(
            document,
            {
                "patrix-32x-free": "train",
                "patrix-hires-paid": "test",
            },
        )


def test_lineage_split_validation_allows_same_lineage_in_one_split() -> None:
    validate_lineage_split_assignments(
        _catalog(),
        {
            "default-hd-64x-free": "train",
            "default-hd-128x-free": "train",
            "f8thful-8x-research": "test",
        },
    )


def test_coverage_report_exposes_current_legal_and_runtime_gaps() -> None:
    report = catalog_coverage_report(_catalog())

    assert report["ml_training_permission_counts"] == {"unknown": 43}
    assert report["integration_status_counts"] == {
        "compatibility_unknown": 7,
        "configured_not_runtime_verified": 32,
        "research_only": 4,
    }
    assert report["access_tier_counts"] == {"free": 39, "paid": 4}
    assert report["families_without_publishable_train"] == list(
        REQUIRED_STYLE_FAMILIES
    )


def test_runtime_claim_without_evidence_fails() -> None:
    document = _catalog()
    candidate = _candidate(document, "f8thful-8x-research")
    candidate["integration"]["status"] = "runtime_verified"

    with pytest.raises(ResourcePackCatalogError, match="needs version and evidence"):
        validate_resourcepack_catalog(document)


def test_missing_configured_asset_mapping_fails() -> None:
    document = _catalog()
    _candidate(document, "faithful-32x-free")["integration"][
        "configured_asset_keys"
    ] = []

    with pytest.raises(ResourcePackCatalogError, match="Configured candidate.*no asset key"):
        validate_resourcepack_catalog(document, asset_config=load_yaml(ASSET_CONFIG_PATH))
