from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from mcdata.config import load_profile, load_yaml
from mcdata.modrinth import ProjectVersion, VersionFile
from mcdata.packs import _select_resourcepack_files


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "docs/evidence/resourcepack_style_expansion_26_2.json"
SUBSET_PATH = ROOT / "configs/profile_subsets/lookdev_style_expansion_26_2.txt"


def _evidence() -> dict:
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def _subset() -> list[str]:
    return SUBSET_PATH.read_text(encoding="utf-8").splitlines()


def test_style_expansion_evidence_is_exact_and_research_only() -> None:
    evidence = _evidence()
    accepted = evidence["accepted"]

    assert evidence["snapshot_date"] == "2026-07-10"
    assert evidence["target_game_version"] == "26.2"
    assert evidence["target_resource_format"] == 88
    assert [item["profile"] for item in accepted] == _subset()
    assert {item["style_family"] for item in accepted} == {
        "retro_lowres",
        "handpainted_fantasy_medieval",
        "cartoon_minimal",
    }
    assert {
        item["project_slug"]: item["archive_inspection"]["requires_normalization"]
        for item in accepted
    } == {
        "8x8-textures": False,
        "fantasy-texture-pack": False,
        "ms-painted": True,
    }
    for item in accepted:
        assert "26.2" in item["declared_game_versions"]
        assert item["eligibility"] == "research_only"
        assert item["access_tier"] == "free"
        assert len(item["file"]["sha512"]) == 128
        assert len(item["file"]["sha1"]) == 40
        assert item["file"]["size"] > 0
        assert len(item["file"]["sha256"]) == 64
        assert len(item["archive_inspection"]["effective_sha256"]) == 64
        assert item["archive_inspection"] == {
            **item["archive_inspection"],
            "upstream_sha512_matched": True,
            "root_pack_mcmeta_count": 1,
            "supports_target_resource_format": True,
        }


def test_audited_sources_resolve_through_the_fail_closed_selector() -> None:
    assets = load_yaml(ROOT / "configs/asset_sets.yml")["assets"]["resourcepacks"]

    for item in _evidence()["accepted"]:
        file = item["file"]
        version = ProjectVersion(
            project=item["project_slug"],
            version_number=item["version_number"],
            version_type="release",
            game_versions=item["declared_game_versions"],
            loaders=["minecraft"],
            files=[
                VersionFile(
                    filename=file["filename"],
                    url="https://cdn.modrinth.com/audited-public-file.zip",
                    primary=True,
                    sha512=file["sha512"],
                    sha1=file["sha1"],
                    size=file["size"],
                )
            ],
        )

        selections = _select_resourcepack_files(
            item["asset_key"],
            project=item["project_slug"],
            spec=assets[item["asset_key"]],
            version=version,
            game_version="26.2",
        )

        assert len(selections) == 1
        assert selections[0].file.filename == file["filename"]
        assert selections[0].file.sha512 == file["sha512"]
        assert selections[0].selection_pattern == file["filename"]


def test_style_expansion_profiles_are_aligned_no_shader_candidates() -> None:
    asset_config = load_yaml(ROOT / "configs/asset_sets.yml")
    catalog = load_yaml(ROOT / "configs/resourcepack_catalog.yml")["candidates"]
    candidates = {item["candidate_id"]: item for item in catalog}
    resolved = [load_profile(ROOT / "configs", name) for name in _subset()]
    invariants = [
        {key: value for key, value in profile.items() if key not in {"description", "asset_set"}}
        for profile in resolved
    ]

    assert invariants[1:] == invariants[:-1]
    for evidence_item, profile in zip(_evidence()["accepted"], resolved, strict=True):
        asset_set = asset_config["asset_sets"][evidence_item["asset_set"]]
        candidate = candidates[evidence_item["candidate_id"]]
        assert profile["game_version"] == "26.2"
        assert profile["width"] == 1920
        assert profile["height"] == 1080
        assert profile["asset_set"] == evidence_item["asset_set"]
        assert asset_set == {
            "description": asset_set["description"],
            "resourcepacks": [evidence_item["asset_key"]],
            "shaderpack": None,
        }
        assert candidate["integration"] == {
            "status": "configured_not_runtime_verified",
            "configured_asset_keys": [evidence_item["asset_key"]],
            "runtime_verified_game_versions": [],
            "evidence_paths": [],
        }
        assert candidate["eligibility"] == {
            "train": "research_only",
            "heldout": "research_only",
        }


def test_ui_and_partial_packs_are_not_smuggled_into_the_profile_subset() -> None:
    configured = set(_subset())
    rejected = _evidence()["rejected"]

    assert {item["reason"] for item in rejected} >= {
        "no_exact_26_2_version",
        "ui_audio_overlay_not_full_world_material",
        "map_building_subset_not_full_world_material",
        "ui_only_not_world_material",
        "provider_not_supported_by_fail_closed_downloader",
    }
    assert all(item.get("profile") not in configured for item in rejected)
    for item in rejected:
        if audited_version := item.get("audited_version"):
            assert len(audited_version["sha512"]) == 128
            assert audited_version["size"] > 0


def test_batch_driver_prints_the_checked_in_style_expansion_subset() -> None:
    env = {**os.environ, "BATCH_PROFILES_FILE": str(SUBSET_PATH)}
    result = subprocess.run(
        [str(ROOT / "scripts/lookdev_render_batch.sh"), "--print-profiles"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert result.stdout.splitlines() == _subset()
    assert result.stderr == ""
