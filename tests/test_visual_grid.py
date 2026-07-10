from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from mcdata.qa import visual_grid


def test_visual_grid_writes_lossless_four_moment_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for profile in ("patrix_solas", "modernarch_bliss"):
        run = tmp_path / profile
        run.mkdir()
        (run / "capture.mp4").write_bytes(profile.encode())
    spec_path = _write_spec(tmp_path)
    monkeypatch.setattr(
        visual_grid,
        "summarize_probe",
        lambda _video: {
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "duration_sec": 600.0,
        },
    )

    colors = iter(("red", "green", "blue", "yellow", "cyan", "magenta", "gray", "white"))

    def fake_extract(_video: Path, _timestamp: float, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1920, 1080), next(colors)).save(out)

    monkeypatch.setattr(visual_grid, "_extract_lossless_frame", fake_extract)

    result = visual_grid.write_visual_grid(spec_path, tmp_path / "out")

    assert [moment["id"] for moment in result["moments"]] == [
        "material_closeup",
        "scene_wide",
        "water_reflection",
        "motion",
    ]
    assert len(result["frames"]) == 8
    assert all(record["width"] == 1920 for record in result["frames"])
    grid = Image.open(tmp_path / "out" / "visual_comparison_grid.png")
    assert grid.size == (220 + 2 * 320, 112 + 4 * 180)
    manifest = json.loads((tmp_path / "out" / "visual_grid_manifest.json").read_text())
    assert manifest["outputs"]["grid"]["sha256"] == result["outputs"]["grid"]["sha256"]
    assert Path(manifest["profiles"][0]["input"]).is_absolute()


def test_visual_grid_rejects_missing_standard_moment(tmp_path: Path) -> None:
    spec_path = _write_spec(tmp_path)
    data = json.loads(spec_path.read_text())
    data["moments"].pop()
    spec_path.write_text(json.dumps(data))

    with pytest.raises(visual_grid.VisualGridError, match="exact order"):
        visual_grid.load_visual_grid_spec(spec_path)


def test_visual_grid_rejects_timestamp_outside_shortest_video(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for profile in ("patrix_solas", "modernarch_bliss"):
        run = tmp_path / profile
        run.mkdir()
        (run / "capture.mp4").write_bytes(profile.encode())
    spec = visual_grid.load_visual_grid_spec(_write_spec(tmp_path))
    monkeypatch.setattr(
        visual_grid,
        "summarize_probe",
        lambda _video: {
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "fps": 24.0,
            "duration_sec": 40.0,
        },
    )

    with pytest.raises(visual_grid.VisualGridError, match="outside .* duration"):
        visual_grid._validate_videos(spec)


def _write_spec(tmp_path: Path) -> Path:
    spec = {
        "title": "Premium realistic rendering candidates",
        "cell_width": 320,
        "cell_height": 180,
        "profiles": [
            {"id": "patrix_solas", "label": "Patrix + Solas", "input": "patrix_solas"},
            {
                "id": "modernarch_bliss",
                "label": "ModernArch + Bliss",
                "input": "modernarch_bliss",
            },
        ],
        "moments": [
            {"id": "material_closeup", "label": "Material close-up", "timestamp_sec": 30},
            {"id": "scene_wide", "label": "Scene wide", "timestamp_sec": 90},
            {"id": "water_reflection", "label": "Water reflection", "timestamp_sec": 180},
            {"id": "motion", "label": "Motion", "timestamp_sec": 300},
        ],
    }
    path = tmp_path / "visual_grid.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path
