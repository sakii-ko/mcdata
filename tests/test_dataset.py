from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import validate

from mcdata.dataset import DatasetValidationError, collect_runtime_logs, write_dataset_index

GENERATOR_COMMIT = "f" * 40


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_episode(
    root: Path,
    profile: str,
    state: dict,
    *,
    commit: str = "abc123",
    feedback: bool = False,
) -> Path:
    run_dir = root / f"run_{profile}"
    run_dir.mkdir(parents=True)
    (run_dir / "capture.mp4").write_bytes(f"video-{profile}".encode())
    (run_dir / "positions.jsonl").write_text('{"idx": 0}\n', encoding="utf-8")
    trajectory_path = run_dir / "trajectory.json"
    trajectory_path.write_text(
        (
            '{"type":"feedback_roam","route":[{"x":0,"z":0},{"x":1,"z":0},'
            '{"x":0,"z":0}],"events":[]}\n'
            if feedback
            else '{"events": [{"t": 0, "key": "w", "action": "tap"}]}\n'
        ),
        encoding="utf-8",
    )
    trajectory_sha = _sha256(trajectory_path)
    if feedback:
        (run_dir / "navigation_log.jsonl").write_text(
            '{"event":"start","mono":1.0}\n'
            '{"event":"control","t_rel":59.9,"moving":true,"yaw_error":1.0}\n',
            encoding="utf-8",
        )
    (run_dir / "client_latest.log").write_text(
        "[Render thread/INFO]: Shaders are disabled because enableShaders is set to false\n",
        encoding="utf-8",
    )
    resource = {
        "filename": f"{profile}.zip",
        "path": f"/remote/instance/{profile}.zip",
        "sha256": "1" * 64,
        "size_bytes": 123,
    }
    manifest = {
        "schema_version": 2,
        "run_id": f"episode-{profile}",
        "lane": "gpu0",
        "profile": {"name": profile, "asset_set": f"asset-{profile}"},
        "mc_version": "26.2",
        "resources": {
            "mods": [],
            "resourcepacks": [resource],
            "shaderpacks": [],
            "resourcepack_runtime": {
                "status": "pass",
                "expected_file_packs": [f"file/{profile}.zip"],
                "actual_file_packs": [f"file/{profile}.zip"],
                "missing_file_packs": [],
                "unexpected_file_packs": [],
                "duplicate_file_packs": [],
                "log_path": f"/remote/instances/{profile}/logs/latest.log",
            },
            "resourcepack_resolution": {
                "schema_version": 1,
                "game_version": "26.2",
                "normalizer_version": "1",
                "target": {
                    "resource_major": 88,
                    "resource_minor": 0,
                    "source_jar_sha256": "2" * 64,
                },
                "packs": [
                    {
                        "filename": resource["filename"],
                        "project": f"project-{profile}",
                        "version": "v1",
                        "download_url": f"https://example.test/{profile}.zip",
                        "expected_size": 120,
                        "normalized": True,
                        "source_sha256": "3" * 64,
                        "source_sha512": "4" * 128,
                        "upstream_sha512": "4" * 128,
                        "effective_sha256": resource["sha256"],
                        "before": {"pack": {"pack_format": 15}},
                        "after": {"pack": {"pack_format": 88}},
                    }
                ],
            },
        },
        "world": {"seed": 1, "profile": "render_matrix_base", "state": state},
        "trajectory": {
            "sha256": trajectory_sha,
            "event_count": 0 if feedback else 60,
            "duration_sec": 604.0 if feedback else 38.937,
            "type": "feedback_roam" if feedback else "astar_walk",
            "execution_mode": (
                "online_position_yaw_feedback" if feedback else "open_loop_event_replay"
            ),
            "route_point_count": 3 if feedback else 0,
        },
        "capture": {
            "enabled": True,
            "settings": {"width": 1280, "height": 720, "fps": 24},
            "ffprobe": {
                "streams": [
                    {
                        "nb_frames": "1440",
                        "width": 1280,
                        "height": 720,
                        "avg_frame_rate": "24/1",
                    }
                ]
            },
        },
        "git": {
            "commit": commit,
            "dirty": False,
            "source": "sync_commit",
            "status_porcelain": [],
        },
        "ended_at": "2026-07-10T00:01:00+00:00",
        "error": None,
    }
    _write_json(run_dir / "manifest.json", manifest)
    evidence_paths = {
        "manifest": run_dir / "manifest.json",
        "video": run_dir / "capture.mp4",
        "trajectory": trajectory_path,
        "positions": run_dir / "positions.jsonl",
    }
    if feedback:
        evidence_paths["navigation"] = run_dir / "navigation_log.jsonl"
    evidence = {
        key: {
            "path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for key, path in evidence_paths.items()
    }
    _write_json(
        run_dir / "qa_report.json",
        {
            "probe": {
                "codec": "h264",
                "width": 1280,
                "height": 720,
                "fps": 24.0,
                "duration_sec": 60.0,
            },
            "warnings": [],
            "evidence": evidence,
            "route_reference": {
                "passed": True,
                "count": 12,
                "threshold_blocks": 3.0,
                "max_deviation_blocks": 0.5,
                "mean_deviation_blocks": 0.2,
                "max_yaw_error_degrees": 1.0,
                "y_out_of_range_count": 0,
                **(
                    {
                        "mode": "online_position_yaw_feedback",
                        "failure_count": 0,
                        "navigation_control_count": 600,
                        "movement_distance_blocks": 240.0,
                        "navigation_duration_ratio": 0.998,
                        "terminal_stop": True,
                        "route_progress_ordered": True,
                        "ordered_route_progress_blocks": 240.0,
                        "minimum_route_progress_blocks": 30.0,
                        "position_duration_ratio": 0.999,
                    }
                    if feedback
                    else {}
                ),
            },
        },
    )
    return run_dir


def _write_compare(root: Path, name: str, runs: list[Path]) -> Path:
    path = root / name / "qa_compare_report.json"
    pairs = []
    for left_index, left in enumerate(runs):
        for right in runs[left_index + 1 :]:
            pairs.append(
                {
                    "left": f"/remote/runs/{left.name}",
                    "right": f"/remote/runs/{right.name}",
                    "passed": True,
                    "count": 12,
                }
            )
    evidence = []
    for run in runs:
        evidence_paths = {
            "manifest": run / "manifest.json",
            "video": run / "capture.mp4",
            "trajectory": run / "trajectory.json",
            "positions": run / "positions.jsonl",
        }
        if (run / "navigation_log.jsonl").exists():
            evidence_paths["navigation"] = run / "navigation_log.jsonl"
        evidence.append(
            {
                "input": f"/remote/runs/{run.name}",
                **{
                    key: {
                        "path": str(path),
                        "sha256": _sha256(path),
                        "size_bytes": path.stat().st_size,
                    }
                    for key, path in evidence_paths.items()
                },
            }
        )
    _write_json(
        path,
        {
            "inputs": [f"/remote/runs/{run.name}" for run in runs],
            "evidence": evidence,
            "position_alignment": {
                "passed": True,
                "threshold_blocks": 2.0,
                "max_distance_blocks": 0.7,
                "mean_distance_blocks": 0.3,
                "pairs": pairs,
            },
        },
    )
    return path


def _write_visual_review(root: Path, profiles: list[str]) -> Path:
    evidence = root / "visual_review" / "all_profiles.jpg"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"visual-evidence")
    review = evidence.parent / "review.json"
    _write_json(
        review,
        {
            "schema_version": 1,
            "status": "pass",
            "reviewed_profiles": profiles,
            "evidence": [evidence.relative_to(root).as_posix()],
            "notes": ["HUD visible; no toast or missing textures."],
        },
    )
    return review


def _fixture(root: Path) -> tuple[list[str], Path, Path, Path]:
    profiles = ["matrix_low", "matrix_textured", "matrix_night"]
    low = _write_episode(root, profiles[0], {"time": "noon", "weather": "clear"})
    textured = _write_episode(root, profiles[1], {"time": "noon", "weather": "clear"})
    night = _write_episode(root, profiles[2], {"time": "midnight", "weather": "clear"})
    strict = _write_compare(root, "compare_strict", [low, textured])
    diagnostic = _write_compare(root, "compare_all", [low, textured, night])
    review = _write_visual_review(root, profiles)
    return profiles, strict, diagnostic, review


def _refresh_manifest_evidence(
    manifest_path: Path,
    compare_paths: tuple[Path, ...],
) -> None:
    evidence = {"sha256": _sha256(manifest_path), "size_bytes": manifest_path.stat().st_size}
    qa_path = manifest_path.parent / "qa_report.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["evidence"]["manifest"].update(evidence)
    _write_json(qa_path, qa)
    for compare_path in compare_paths:
        compare = json.loads(compare_path.read_text(encoding="utf-8"))
        for item in compare["evidence"]:
            if Path(item["input"]).name == manifest_path.parent.name:
                item["manifest"].update(evidence)
        _write_json(compare_path, compare)


def test_write_dataset_index_groups_variants_and_is_deterministic(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        strict_compare_report=strict,
        diagnostic_compare_reports=(path for path in [diagnostic]),
        visual_review=review,
    )
    first_index = (tmp_path / "dataset_index.json").read_bytes()
    first_sums = (tmp_path / "SHA256SUMS").read_bytes()
    repeated = write_dataset_index(
        tmp_path,
        expected_profiles=list(reversed(profiles)),
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    strict_cohort = next(
        item for item in index["cohorts"] if item["role"] == "strict_rendering_matrix"
    )
    variants = [item for item in index["cohorts"] if item["role"] == "world_state_variant"]
    assert index["status"] == "accepted"
    assert len(strict_cohort["profile_names"]) == 2
    assert len(variants) == 1 and variants[0]["profile_names"] == ["matrix_night"]
    assert index["dataset_id"] == repeated["dataset_id"]
    assert (tmp_path / "dataset_index.json").read_bytes() == first_index
    assert (tmp_path / "SHA256SUMS").read_bytes() == first_sums
    assert b"/remote/" not in first_index
    assert "dataset_index.json" in first_sums.decode()
    assert "capture.mp4" in first_sums.decode()
    schema = json.loads(
        (Path(__file__).parents[1] / "src/mcdata/schemas/dataset_index.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validate(index, schema)


def test_dataset_without_manual_review_is_only_automated_pass(tmp_path: Path) -> None:
    profiles, strict, diagnostic, _review = _fixture(tmp_path)

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
    )

    assert index["status"] == "automated_pass"
    assert index["manual_review"] is None


def test_feedback_dataset_is_policy_aligned_and_binds_navigation(tmp_path: Path) -> None:
    profiles = ["feedback_vanilla", "feedback_material"]
    runs = [
        _write_episode(
            tmp_path,
            profile,
            {"time": "noon", "weather": "clear"},
            feedback=True,
        )
        for profile in profiles
    ]
    strict = _write_compare(tmp_path, "feedback_compare", runs)
    diagnostic = _write_compare(tmp_path, "feedback_diagnostic", runs)
    review = _write_visual_review(tmp_path, profiles)

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile=profiles[0],
        generator_commit=GENERATOR_COMMIT,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    assert index["cohorts"][0]["role"] == "policy_aligned_rendering_matrix"
    assert index["invariants"]["trajectory_execution_mode"] == ("online_position_yaw_feedback")
    assert index["invariants"]["trajectory_event_count"] == 0
    assert all("navigation" in episode for episode in index["episodes"])
    assert all(episode["trajectory"]["route_point_count"] == 3 for episode in index["episodes"])
    assert index["status"] == "accepted"
    assert index["manual_review"] is not None


def test_dataset_rejects_profile_or_commit_drift(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    manifest_path = tmp_path / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git"]["commit"] = "different"
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match="git commit"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_missing_resource_provenance(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    manifest_path = tmp_path / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["resources"]["resourcepacks"][0]["sha256"]
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match="resourcepacks sha256"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_runtime_resolution_mismatch(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    manifest_path = tmp_path / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resources"]["resourcepack_runtime"]["expected_file_packs"] = []
    manifest["resources"]["resourcepack_runtime"]["actual_file_packs"] = []
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match="runtime|Runtime"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_stale_positions_and_partial_diagnostic(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    positions = tmp_path / "run_matrix_textured" / "positions.jsonl"
    positions.write_text('{"idx": 0, "x": 99}\n', encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="stale positions"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    profiles, strict, diagnostic, review = _fixture(tmp_path / "partial")
    report = json.loads(diagnostic.read_text(encoding="utf-8"))
    report["inputs"] = report["inputs"][:2]
    report["evidence"] = report["evidence"][:2]
    report["position_alignment"]["pairs"] = report["position_alignment"]["pairs"][:1]
    _write_json(diagnostic, report)
    with pytest.raises(DatasetValidationError, match="required cohort"):
        write_dataset_index(
            tmp_path / "partial",
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_self_review_nan_and_symlink(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    review_data = json.loads(review.read_text(encoding="utf-8"))
    review_data["evidence"] = [review.relative_to(tmp_path).as_posix()]
    _write_json(review, review_data)
    with pytest.raises(DatasetValidationError, match="self-referential"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    with pytest.raises(DatasetValidationError, match="must be positive"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            expected_fps=float("nan"),
        )

    profiles, strict, diagnostic, review = _fixture(tmp_path / "symlink")
    (tmp_path / "symlink" / "unsafe-link").symlink_to("/does/not/exist")
    with pytest.raises(DatasetValidationError, match="symlinks"):
        write_dataset_index(
            tmp_path / "symlink",
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )
    assert not (tmp_path / "symlink" / "dataset_index.json").exists()


def test_collect_runtime_logs_copies_exact_profile_set(tmp_path: Path) -> None:
    profiles, _strict, _diagnostic, _review = _fixture(tmp_path)
    for profile in profiles:
        manifest_path = tmp_path / f"run_{profile}" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = tmp_path / "instance_logs" / profile / "latest.log"
        source.parent.mkdir(parents=True)
        source.write_text(f"runtime-{profile}\n", encoding="utf-8")
        manifest["resources"]["resourcepack_runtime"]["log_path"] = str(source)
        _write_json(manifest_path, manifest)

    outputs = collect_runtime_logs(tmp_path, expected_profiles=profiles)

    assert len(outputs) == 3
    for profile in profiles:
        assert (tmp_path / f"run_{profile}" / "client_latest.log").read_text(
            encoding="utf-8"
        ) == f"runtime-{profile}\n"


def test_dataset_rejects_qa_and_strict_compare_failures(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review = _fixture(tmp_path)
    qa_path = tmp_path / "run_matrix_textured" / "qa_report.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["warnings"] = ["black border"]
    _write_json(qa_path, qa)
    with pytest.raises(DatasetValidationError, match="QA warnings"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    qa["warnings"] = []
    _write_json(qa_path, qa)
    strict_report = json.loads(strict.read_text(encoding="utf-8"))
    strict_report["inputs"] = strict_report["inputs"][:1]
    _write_json(strict, strict_report)
    with pytest.raises(DatasetValidationError, match="required cohort"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )
