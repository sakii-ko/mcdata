from __future__ import annotations

import copy
import hashlib
import shutil
from pathlib import Path

import pytest

import mcdata.terrain as terrain_module
from mcdata.config import load_yaml
from mcdata.terrain import (
    TerrainRegistryError,
    accepted_terrain_instances,
    load_terrain_registry,
    terrain_identity_sha256,
    validate_terrain_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "configs" / "terrains.yml"


def _document() -> dict:
    return load_yaml(REGISTRY_PATH)


def _instance(document: dict) -> tuple[str, dict]:
    family = document["terrain_families"][0]
    return family["family_id"], family["instances"][0]


def _rehash(document: dict) -> None:
    family_id, instance = _instance(document)
    instance["identity_sha256"] = terrain_identity_sha256(family_id, instance)


def test_canonical_registry_has_one_real_accepted_flat_plaza() -> None:
    document = load_terrain_registry(REGISTRY_PATH, repository_root=ROOT)
    accepted = accepted_terrain_instances(document)

    assert len(accepted) == 1
    instance = accepted[0]
    assert instance["family_id"] == "showcase_plaza_flat_v1"
    assert instance["instance_id"] == (
        "showcase_plaza_flat_v1_mc26_2_seed1_render_matrix_base"
    )
    assert instance["minecraft"] == {"version": "26.2"}
    assert instance["world"] == {"seed": 1, "profile": "render_matrix_base"}
    assert instance["spawn"] == {"x": 0, "y": 64, "z": -14}
    assert instance["probe_bounds"] == {
        "x": [-16, 16],
        "y": [63.0, 66.0],
        "z": [-16, 16],
    }
    assert instance["provenance"]["immutable_world_snapshot"] == {
        "status": "unavailable"
    }
    assert instance["provenance"]["navigation_surface"] == {
        "status": "unavailable"
    }
    assert document["blocked_candidates"] == [
        {
            "family_id": "plains_riverbank_flat_v1",
            "status": "blocked",
            "blocker_codes": [
                "immutable_world_snapshot_missing",
                "navigation_surface_hash_missing",
                "height_aware_probe_missing",
                "vertical_policy_missing",
                "liquid_hazard_policy_missing",
            ],
        }
    ]


def test_canonical_scene_provenance_matches_current_file_and_nonzero_y_origin() -> None:
    family_id, instance = _instance(_document())
    scene = instance["provenance"]["scene"]

    assert scene["origin"] == [0, 64, 0]
    assert scene["sha256"] == hashlib.sha256((ROOT / scene["path"]).read_bytes()).hexdigest()
    assert instance["identity_sha256"] == terrain_identity_sha256(family_id, instance)


def test_render_world_state_variants_do_not_create_terrain_families() -> None:
    document = _document()

    assert document["identity_policy"] == {
        "hash_algorithm": "sha256",
        "canonical_encoding": "utf8_json_sorted_keys_compact",
        "render_world_state_variants_are_terrain_identity": False,
        "excluded_render_world_state_dimensions": [
            "time",
            "weather",
            "precipitation_biome",
            "collision_preserving_surface_overlay",
        ],
    }
    accepted_family_ids = [item["family_id"] for item in document["terrain_families"]]
    assert accepted_family_ids == ["showcase_plaza_flat_v1"]
    assert not any(token in accepted_family_ids for token in ("noon", "rain", "snow"))


def test_flat_navigation_limits_are_explicit() -> None:
    _, instance = _instance(_document())

    assert instance["capabilities"] == {
        "navigation_model": "flat_2d_feedback_v1",
        "flat_full_block_surface_only": True,
        "liquid_traversal": False,
        "vertical_edge_traversal": False,
        "step_or_slope_traversal": False,
        "drop_traversal": False,
        "gap_jump_traversal": False,
        "swimming": False,
        "climbing": False,
        "hazard_aware_routing": False,
    }


def test_identity_hash_is_order_independent_and_binds_all_identity_axes() -> None:
    family_id, instance = _instance(_document())
    expected = terrain_identity_sha256(family_id, instance)
    reordered = dict(reversed(list(instance.items())))
    assert terrain_identity_sha256(family_id, reordered) == expected

    mutations = [
        lambda value: value["minecraft"].update(version="26.3"),
        lambda value: value["world"].update(seed=2),
        lambda value: value["world"].update(profile="another_world"),
        lambda value: value["provenance"]["scene"].update(sha256="a" * 64),
        lambda value: value["provenance"]["scene"].update(origin=[1, 64, 0]),
        lambda value: value["spawn"].update(x=1),
        lambda value: value["probe_bounds"].update(x=[-15, 16]),
        lambda value: value["capabilities"].update(liquid_traversal=True),
        lambda value: value["config_bindings"].update(action_strategy="ground_astar_loop"),
        lambda value: value.update(instance_id="another_instance"),
    ]
    for mutate in mutations:
        changed = copy.deepcopy(instance)
        mutate(changed)
        assert terrain_identity_sha256(family_id, changed) != expected
    assert terrain_identity_sha256("another_family", instance) != expected


def test_scene_hash_drift_fails_even_with_recomputed_identity_hash() -> None:
    document = _document()
    _, instance = _instance(document)
    instance["provenance"]["scene"]["sha256"] = "a" * 64
    _rehash(document)

    with pytest.raises(TerrainRegistryError, match="Scene SHA-256 mismatch"):
        validate_terrain_registry(document, repository_root=ROOT)


def test_scene_origin_drift_fails_even_with_recomputed_identity_hash() -> None:
    document = _document()
    _, instance = _instance(document)
    instance["provenance"]["scene"]["origin"] = [1, 64, 0]
    _rehash(document)

    with pytest.raises(TerrainRegistryError, match="Scene origin mismatch"):
        validate_terrain_registry(document, repository_root=ROOT)


def test_matching_nonzero_scene_xz_origin_still_fails_phase1(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    for filename in ("profiles.yml", "actions.yml", "scene.yml"):
        shutil.copyfile(ROOT / "configs" / filename, config_dir / filename)
    scene_path = config_dir / "scene.yml"
    scene_path.write_text(
        scene_path.read_text(encoding="utf-8").replace(
            "origin: [0, 64, 0]", "origin: [1, 64, 0]", 1
        ),
        encoding="utf-8",
    )
    document = _document()
    _, instance = _instance(document)
    instance["provenance"]["scene"]["origin"] = [1, 64, 0]
    instance["provenance"]["scene"]["sha256"] = hashlib.sha256(
        scene_path.read_bytes()
    ).hexdigest()
    _rehash(document)

    with pytest.raises(TerrainRegistryError, match="non-zero scene x/z origin"):
        validate_terrain_registry(document, repository_root=tmp_path)


def test_config_binding_drift_fails_even_with_recomputed_identity_hash() -> None:
    document = _document()
    _, instance = _instance(document)
    instance["spawn"]["x"] = 1
    _rehash(document)

    with pytest.raises(TerrainRegistryError, match="spawn does not match bound profile"):
        validate_terrain_registry(document, repository_root=ROOT)


@pytest.mark.parametrize(
    "scene_override",
    [
        {"enabled": False, "origin": [0, 64, 0]},
        {"enabled": True, "origin": [9, 64, 0]},
        {
            "enabled": True,
            "origin": [0, 64, 0],
            "variant": "unproven_wall",
            "additions": [
                {
                    "kind": "fill",
                    "block": "minecraft:stone",
                    "from": [0, 0, 0],
                    "to": [0, 2, 0],
                }
            ],
        },
    ],
)
def test_effective_profile_scene_override_fails_closed(
    monkeypatch: pytest.MonkeyPatch, scene_override: dict
) -> None:
    original_load_profile = terrain_module.load_profile

    def load_drifted_profile(config_dir: Path, name: str) -> dict:
        profile = original_load_profile(config_dir, name)
        profile["world_state"]["scene"] = copy.deepcopy(scene_override)
        return profile

    monkeypatch.setattr(terrain_module, "load_profile", load_drifted_profile)

    with pytest.raises(TerrainRegistryError, match="scene does not match bound profile"):
        validate_terrain_registry(_document(), repository_root=ROOT)


def test_duplicate_instance_id_fails_closed() -> None:
    document = _document()
    family = document["terrain_families"][0]
    family["instances"].append(copy.deepcopy(family["instances"][0]))

    with pytest.raises(TerrainRegistryError, match="violates schema"):
        validate_terrain_registry(document, repository_root=ROOT)


def test_second_accepted_alias_fails_even_with_a_valid_identity_hash() -> None:
    document = _document()
    alias = copy.deepcopy(document["terrain_families"][0])
    alias["family_id"] = "showcase_plaza_alias"
    alias["instances"][0]["instance_id"] = "showcase_plaza_alias_seed1"
    alias["instances"][0]["identity_sha256"] = terrain_identity_sha256(
        alias["family_id"], alias["instances"][0]
    )
    document["terrain_families"].append(alias)

    with pytest.raises(TerrainRegistryError, match="violates schema"):
        validate_terrain_registry(document, repository_root=ROOT)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("root", "identity_policy"),
        ("family", "family_id"),
        ("instance", "minecraft"),
        ("instance", "probe_bounds"),
        ("instance", "capabilities"),
        ("instance", "config_bindings"),
    ],
)
def test_missing_required_registry_fields_fail_closed(target: str, field: str) -> None:
    document = _document()
    family = document["terrain_families"][0]
    instance = family["instances"][0]
    containers = {"root": document, "family": family, "instance": instance}
    del containers[target][field]

    with pytest.raises(TerrainRegistryError, match="violates schema"):
        validate_terrain_registry(document, repository_root=ROOT)


def test_unknown_or_timestamp_fields_fail_closed() -> None:
    for field, value in (("unknown", True), ("generated_at", "2026-07-10T00:00:00Z")):
        document = _document()
        _, instance = _instance(document)
        instance[field] = value
        with pytest.raises(TerrainRegistryError, match="violates schema"):
            validate_terrain_registry(document, repository_root=ROOT)


def test_unavailable_snapshot_and_surface_cannot_carry_fake_hashes() -> None:
    for artifact_name in ("immutable_world_snapshot", "navigation_surface"):
        document = _document()
        _, instance = _instance(document)
        instance["provenance"][artifact_name]["sha256"] = "a" * 64
        _rehash(document)
        with pytest.raises(TerrainRegistryError, match="violates schema"):
            validate_terrain_registry(document, repository_root=ROOT)


def test_fake_immutable_snapshot_source_cannot_become_accepted() -> None:
    document = _document()
    _, instance = _instance(document)
    instance["provenance"] = {
        "source_kind": "immutable_world_snapshot",
        "scene": {"status": "unavailable"},
        "immutable_world_snapshot": {"status": "available", "sha256": "a" * 64},
        "navigation_surface": {"status": "available", "sha256": "b" * 64},
    }
    _rehash(document)

    with pytest.raises(TerrainRegistryError, match="violates schema"):
        validate_terrain_registry(document, repository_root=ROOT)


def test_blocked_candidate_cannot_be_relabelled_as_accepted() -> None:
    document = _document()
    document["blocked_candidates"][0]["status"] = "accepted"

    with pytest.raises(TerrainRegistryError, match="violates schema"):
        validate_terrain_registry(document, repository_root=ROOT)
