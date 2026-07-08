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


def test_route_reference_passes_when_positions_follow_ideal_track() -> None:
    positions = [
        {"idx": 0, "t_rel": -0.1, "x": 0.0, "y": 64.0, "z": 0.0},
        {"idx": 1, "t_rel": 0.0, "x": 0.0, "y": 64.0, "z": 0.0},
        {"idx": 2, "t_rel": 1.0, "x": 1.0, "y": 64.0, "z": 0.0},
    ]
    ideal = [
        {"t": 0.0, "x": 0.0, "z": 0.0},
        {"t": 1.0, "x": 1.0, "z": 0.0},
    ]

    result = report.check_route_reference(positions, ideal, max_dev=0.25)

    assert result["passed"] is True
    assert result["count"] == 2
    assert result["max_deviation_blocks"] == 0.0


def test_route_reference_fails_on_large_xz_deviation() -> None:
    positions = [{"idx": 0, "t_rel": 1.0, "x": 5.0, "y": 64.0, "z": 0.0}]
    ideal = [
        {"t": 0.0, "x": 0.0, "z": 0.0},
        {"t": 1.0, "x": 1.0, "z": 0.0},
    ]

    result = report.check_route_reference(positions, ideal, max_dev=3.0)

    assert result["passed"] is False
    assert result["max_deviation_blocks"] == 4.0


def test_route_reference_fails_on_y_out_of_range() -> None:
    positions = [{"idx": 0, "t_rel": 0.0, "x": 0.0, "y": 41.0, "z": 0.0}]
    ideal = [{"t": 0.0, "x": 0.0, "z": 0.0}]

    result = report.check_route_reference(positions, ideal)

    assert result["passed"] is False
    assert result["y_out_of_range_count"] == 1


def test_run_markdown_includes_route_reference_header(tmp_path: Path) -> None:
    out = tmp_path / "qa"
    qa_report = {
        "video": "capture.mp4",
        "probe": {"codec": "h264", "width": 1280, "height": 720, "fps": 24.0, "duration_sec": 1.0},
        "route_reference": {
            "passed": False,
            "max_deviation_blocks": 4.0,
            "mean_deviation_blocks": 2.0,
            "threshold_blocks": 3.0,
            "y_min": 63.0,
            "y_max": 66.0,
            "y_out_of_range_count": 1,
        },
        "warnings": ["route reference check failed"],
        "frames": [
            {
                "timestamp_sec": 0.5,
                "brightness": {"p5": 1.0, "p50": 2.0, "p95": 3.0},
                "border": {"has_black_border": False},
            }
        ],
    }

    report._write_run_markdown(out, qa_report)

    text = out.read_text(encoding="utf-8")
    assert "- route_reference: `FAIL`" in text
    assert "- route_max_deviation_blocks: `4.000`" in text
    assert "- route_y_out_of_range_count: `1`" in text


def _positions(tmp_path: Path, name: str, values: list[tuple[float, float, float]]) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    with (run_dir / "positions.jsonl").open("w", encoding="utf-8") as fh:
        for idx, (x, y, z) in enumerate(values):
            fh.write(json.dumps({"idx": idx, "x": x, "y": y, "z": z}) + "\n")
    return run_dir
