import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from mcdata.manifest import build_run_manifest, write_run_manifest

ROOT = Path(__file__).resolve().parents[1]


def _sample_manifest() -> dict:
    return build_run_manifest(
        run_id="20260708T000000Z_matrix_low",
        profile_name="matrix_low",
        profile={
            "loader": "fabric",
            "quality": "low",
            "asset_set": "vanilla",
            "width": 1280,
            "height": 720,
            "server_port": 25570,
            "world_seed": 1,
            "world_profile": "render_matrix_base",
            "world_state": {"time": "noon"},
        },
        mc_version="26.2",
        resources={"asset_set": "vanilla", "mods": [], "resourcepacks": [], "shaderpacks": []},
        trajectory={
            "strategy": "ground_astar_loop",
            "path": "runs/example/trajectory.json",
            "source_path": "runs/trajectories/ground_astar_loop.json",
            "sha256": "0" * 64,
            "event_count": 46,
            "duration_sec": 52.09,
            "type": "astar_walk",
            "execution_mode": "open_loop_event_replay",
            "route_point_count": 20,
        },
        action_curriculum={
            "taxonomy_version": 1,
            "planned_level": 1,
            "planned_capabilities": ["navigation"],
            "observed_semantic_action_counts": {
                "navigation_move": 0,
                "navigation_camera": 0,
                "deliberate_jump": 0,
                "deterministic_block_placement": 0,
                "controlled_combat": 0,
            },
            "observed_level": 0,
            "controller_recovery_counts": {
                "attempts": 0,
                "jump_taps": 0,
                "reverse_moves": 0,
            },
            "bucket": "l1",
            "evidence": None,
        },
        action_effect=None,
        capture={
            "enabled": False,
            "settings": {"width": 1280, "height": 720, "fps": 24},
            "ffmpeg_cmd": None,
            "ffprobe": None,
        },
        env={"hostname": "host", "display": ":99"},
        git={"commit": "abc", "dirty": False},
        started_at="2026-07-08T00:00:00+00:00",
        ended_at="2026-07-08T00:00:01+00:00",
        lane="gpu0",
    )


def test_build_run_manifest_validates_against_schema() -> None:
    schema = json.loads(
        (ROOT / "src" / "mcdata" / "schemas" / "manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )

    manifest = _sample_manifest()

    validate(instance=manifest, schema=schema)
    assert manifest["schema_version"] == 3
    assert manifest["lane"] == "gpu0"


def test_example_run_manifest_validates_against_schema() -> None:
    schema = json.loads(
        (ROOT / "src" / "mcdata" / "schemas" / "manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    example = json.loads(
        (ROOT / "docs" / "examples" / "run_manifest_example.json").read_text(encoding="utf-8")
    )

    validate(instance=example, schema=schema)


def test_v3_advanced_manifest_requires_hashed_physical_effect_report() -> None:
    schema = json.loads(
        (ROOT / "src/mcdata/schemas/manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = _sample_manifest()
    manifest["action_curriculum"].update(
        planned_level=2,
        planned_capabilities=["navigation", "deliberate_jump"],
        bucket="l1_l2",
    )

    with pytest.raises(ValidationError):
        validate(instance=manifest, schema=schema)

    manifest["action_effect"] = {
        "kind": "physical_deliberate_jump",
        "schema_version": 1,
        "path": "runs/example/action_effect_report.json",
        "sha256": "1" * 64,
        "size_bytes": 123,
        "report_id": "sha256:" + "2" * 64,
        "planned_jump_count": 4,
        "verified_jump_count": 1,
        "accepted": False,
    }
    validate(instance=manifest, schema=schema)


def test_write_run_manifest_round_trips_json(tmp_path: Path) -> None:
    manifest = _sample_manifest()

    path = write_run_manifest(tmp_path, manifest)

    assert path == tmp_path / "manifest.json"
    assert json.loads(path.read_text(encoding="utf-8")) == manifest
    assert not (tmp_path / "manifest.json.tmp").exists()
