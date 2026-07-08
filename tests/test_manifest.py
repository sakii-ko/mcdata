import json
from pathlib import Path

from jsonschema import validate

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
        },
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
    )


def test_build_run_manifest_validates_against_schema() -> None:
    schema = json.loads(
        (ROOT / "src" / "mcdata" / "schemas" / "manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )

    validate(instance=_sample_manifest(), schema=schema)


def test_write_run_manifest_round_trips_json(tmp_path: Path) -> None:
    manifest = _sample_manifest()

    path = write_run_manifest(tmp_path, manifest)

    assert path == tmp_path / "manifest.json"
    assert json.loads(path.read_text(encoding="utf-8")) == manifest
