import importlib
import json
from pathlib import Path

from mcdata.qa import report


def test_bilinear_filter_supports_pillow_without_resampling(monkeypatch) -> None:
    monkeypatch.delattr(report.Image, "Resampling", raising=False)

    reloaded = importlib.reload(report)

    assert reloaded._BILINEAR == reloaded.Image.BILINEAR
    importlib.reload(report)


def test_position_alignment_passes_with_small_offsets(tmp_path: Path) -> None:
    left = _positions(tmp_path, "left", [(0, 64, 0), (1, 64, 1)])
    right = _positions(tmp_path, "right", [(0.5, 64, 0.5), (2, 64, 1)])

    alignment = report.compare_position_alignment([left, right])

    assert alignment is not None
    assert alignment["passed"] is True
    assert alignment["max_distance_blocks"] <= 2.0
    assert alignment["mean_distance_blocks"] is not None
    assert alignment["mean_distance_blocks"] <= alignment["max_distance_blocks"]


def test_position_alignment_fails_when_max_exceeds_threshold(tmp_path: Path) -> None:
    left = _positions(tmp_path, "left", [(0, 64, 0), (1, 64, 1)])
    right = _positions(tmp_path, "right", [(0, 64, 0), (5, 64, 1)])

    alignment = report.compare_position_alignment([left, right])

    assert alignment is not None
    assert alignment["passed"] is False
    assert alignment["max_distance_blocks"] > 2.0


def _positions(tmp_path: Path, name: str, values: list[tuple[float, float, float]]) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    with (run_dir / "positions.jsonl").open("w", encoding="utf-8") as fh:
        for idx, (x, y, z) in enumerate(values):
            fh.write(json.dumps({"idx": idx, "x": x, "y": y, "z": z}) + "\n")
    return run_dir
