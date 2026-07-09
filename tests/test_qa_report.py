import importlib
import hashlib
import json
from pathlib import Path

from PIL import Image

from mcdata.qa import report
from mcdata.qa import route


def test_bilinear_filter_supports_pillow_without_resampling(monkeypatch) -> None:
    monkeypatch.delattr(report.Image, "Resampling", raising=False)

    reloaded = importlib.reload(report)

    assert reloaded._BILINEAR == reloaded.Image.BILINEAR
    importlib.reload(report)


def test_position_alignment_passes_with_small_offsets(tmp_path: Path) -> None:
    left = _positions(tmp_path, "left", [(0, 64, 0), (1, 64, 1)])
    right = _positions(tmp_path, "right", [(0.5, 64, 0.5), (2, 64, 1)])

    alignment = route.compare_position_alignment([left, right])

    assert alignment is not None
    assert alignment["passed"] is True
    assert alignment["max_distance_blocks"] <= 2.0
    assert alignment["mean_distance_blocks"] is not None
    assert alignment["mean_distance_blocks"] <= alignment["max_distance_blocks"]


def test_position_alignment_fails_when_max_exceeds_threshold(tmp_path: Path) -> None:
    left = _positions(tmp_path, "left", [(0, 64, 0), (1, 64, 1)])
    right = _positions(tmp_path, "right", [(0, 64, 0), (5, 64, 1)])

    alignment = route.compare_position_alignment([left, right])

    assert alignment is not None
    assert alignment["passed"] is False
    assert alignment["max_distance_blocks"] > 2.0


def test_route_reference_passes_when_positions_follow_ideal_track() -> None:
    positions = [
        {"idx": 0, "t_rel": -0.1, "x": 0.0, "y": 64.0, "z": 0.0},
        {"idx": 1, "t_rel": 0.0, "x": 0.0, "y": 64.0, "yaw": -90.0, "z": 0.0},
        {"idx": 2, "t_rel": 1.0, "x": 1.0, "y": 64.0, "yaw": -90.0, "z": 0.0},
    ]
    ideal = [
        {"t": 0.0, "x": 0.0, "yaw": -90.0, "z": 0.0},
        {"t": 1.0, "x": 1.0, "yaw": -90.0, "z": 0.0},
    ]

    result = route.check_route_reference(positions, ideal, max_dev=0.25)

    assert result["passed"] is True
    assert result["count"] == 2
    assert result["max_deviation_blocks"] == 0.0
    assert result["max_yaw_error_degrees"] == 0.0
    assert result["yaw_sample_count"] == 2


def test_route_reference_fails_on_large_xz_deviation() -> None:
    positions = [{"idx": 0, "t_rel": 1.0, "x": 5.0, "y": 64.0, "z": 0.0}]
    ideal = [
        {"t": 0.0, "x": 0.0, "z": 0.0},
        {"t": 1.0, "x": 1.0, "z": 0.0},
    ]

    result = route.check_route_reference(positions, ideal, max_dev=3.0)

    assert result["passed"] is False
    assert result["max_deviation_blocks"] == 4.0


def test_route_reference_fails_on_y_out_of_range() -> None:
    positions = [{"idx": 0, "t_rel": 0.0, "x": 0.0, "y": 41.0, "yaw": 0.0, "z": 0.0}]
    ideal = [{"t": 0.0, "x": 0.0, "yaw": 0.0, "z": 0.0}]

    result = route.check_route_reference(positions, ideal)

    assert result["passed"] is False
    assert result["y_out_of_range_count"] == 1


def test_route_reference_fails_when_yaw_sample_is_missing() -> None:
    positions = [{"idx": 0, "t_rel": 0.0, "x": 0.0, "y": 64.0, "z": 0.0}]
    ideal = [{"t": 0.0, "x": 0.0, "yaw": 0.0, "z": 0.0}]

    result = route.check_route_reference(positions, ideal)

    assert result["passed"] is False
    assert result["missing_yaw_count"] == 1


def test_route_reference_fails_on_large_circular_yaw_error() -> None:
    positions = [{"idx": 0, "t_rel": 0.0, "x": 0.0, "y": 64.0, "yaw": -170.0, "z": 0.0}]
    ideal = [{"t": 0.0, "x": 0.0, "yaw": 170.0, "z": 0.0}]

    result = route.check_route_reference(positions, ideal, max_yaw_dev_deg=10.0)

    assert result["passed"] is False
    assert result["max_yaw_error_degrees"] == 20.0


def test_route_reference_skips_yaw_inside_turn_window_but_keeps_position_check() -> None:
    positions = [
        {"idx": 0, "t_rel": 1.0, "x": 0.5, "y": 64.0, "yaw": 87.0, "z": 0.0},
        {"idx": 1, "t_rel": 2.0, "x": 1.0, "y": 64.0, "yaw": 0.0, "z": 0.0},
    ]
    ideal = [
        {"t": 0.0, "x": 0.0, "yaw": 0.0, "z": 0.0},
        {"t": 2.0, "x": 1.0, "yaw": 0.0, "z": 0.0},
    ]

    result = route.check_route_reference(
        positions,
        ideal,
        max_yaw_dev_deg=10.0,
        yaw_ignore_windows=[(0.5, 1.5)],
    )

    assert result["passed"] is True
    assert result["max_deviation_blocks"] == 0.0
    assert result["max_yaw_error_degrees"] == 0.0
    assert result["yaw_sample_count"] == 1
    assert result["skipped_yaw_count"] == 1
    assert result["samples"][0]["yaw_skipped"] is True


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
            "max_yaw_error_degrees": 12.0,
            "mean_yaw_error_degrees": 8.0,
            "yaw_threshold_degrees": 10.0,
            "yaw_sample_count": 2,
            "missing_yaw_count": 1,
            "skipped_yaw_count": 3,
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
    assert "- route_max_yaw_error_degrees: `12.000`" in text
    assert "- route_yaw_sample_count: `2`" in text
    assert "- route_missing_yaw_count: `1`" in text
    assert "- route_skipped_yaw_count: `3`" in text
    assert "- route_y_out_of_range_count: `1`" in text


def test_compare_report_binds_artifact_hashes_and_downscales_frames(
    tmp_path: Path, monkeypatch
) -> None:
    inputs = []
    for name in ("left", "right"):
        run = tmp_path / name
        run.mkdir()
        for filename, content in (
            ("capture.mp4", b"video" + name.encode()),
            ("manifest.json", b"{}\n"),
            ("trajectory.json", b"{}\n"),
            ("positions.jsonl", b'{"idx": 0}\n'),
        ):
            (run / filename).write_bytes(content)
        inputs.append(run)
    monkeypatch.setattr(
        report,
        "summarize_probe",
        lambda _path: {"duration_sec": 1.0, "width": 1280, "height": 720, "fps": 24.0},
    )
    monkeypatch.setattr(report, "uniform_timestamps", lambda _duration, _frames: [0.5])
    monkeypatch.setattr(
        report,
        "extract_frames_at",
        lambda _video, _timestamps: [Image.new("RGB", (1280, 720), "green")],
    )
    monkeypatch.setattr(
        report,
        "compare_position_alignment",
        lambda _inputs: {
            "passed": True,
            "threshold_blocks": 2.0,
            "max_distance_blocks": 0.0,
            "mean_distance_blocks": 0.0,
            "pairs": [],
        },
    )
    observed_sizes = []
    monkeypatch.setattr(
        report,
        "_write_compare_sheet",
        lambda _path, extracted, _timestamps: observed_sizes.extend(
            image.size for frames in extracted for image in frames
        ),
    )

    result = report.write_compare_report(inputs, frames=1, out_dir=tmp_path / "compare")

    assert observed_sizes == [(240, 135), (240, 135)]
    assert len(result["evidence"]) == 2
    for item, run in zip(result["evidence"], inputs):
        assert item["input"] == str(run)
        assert set(item) == {"input", "video", "manifest", "trajectory", "positions"}
        assert item["video"]["sha256"] == hashlib.sha256(
            (run / "capture.mp4").read_bytes()
        ).hexdigest()


def _positions(tmp_path: Path, name: str, values: list[tuple[float, float, float]]) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    with (run_dir / "positions.jsonl").open("w", encoding="utf-8") as fh:
        for idx, (x, y, z) in enumerate(values):
            fh.write(json.dumps({"idx": idx, "x": x, "y": y, "z": z}) + "\n")
    return run_dir
