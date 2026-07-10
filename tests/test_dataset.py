from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import validate

from mcdata.action_curriculum import summarize_action_run
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
    material: str | None = None,
    shader: str | None = None,
    manifest_schema_version: int = 3,
    include_action_curriculum: bool = True,
) -> Path:
    material_name = material or profile
    world_state = {
        "time": "noon",
        "weather": "clear",
        "weather_duration_sec": 999999,
        "biome": {"id": "minecraft:plains", "precipitation": "rain"},
        "player": {"x": 0, "y": 64, "z": -14, "yaw": 90, "pitch": 18},
        "scene": {"enabled": True, "origin": [0, 64, 0]},
        "gamerules": {"advance_time": False, "advance_weather": False},
        **state,
    }
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
            '{"event":"control","t_rel":59.9,"moving":true,"mouse_dx":0,"yaw_error":1.0}\n'
            '{"event":"stop","t_rel":60.0,"reason":"stop_requested"}\n',
            encoding="utf-8",
        )
        action_evidence = run_dir / "navigation_log.jsonl"
        execution_mode = "online_position_yaw_feedback"
    else:
        replay_record = {
            "actual_t": 0.0,
            "event": {"action": "tap", "key": "w", "t": 0},
            "scheduled_t": 0.0,
        }
        replay_record["execution_status"] = "executed"
        (run_dir / "replay_log.jsonl").write_text(
            json.dumps({"event": "start", "mono": 1.0})
            + "\n"
            + json.dumps(replay_record)
            + "\n",
            encoding="utf-8",
        )
        action_evidence = run_dir / "replay_log.jsonl"
        execution_mode = "open_loop_event_replay"
    shader_filename = f"{shader}.zip" if shader else None
    runtime_line = (
        f"[Render thread/INFO]: Using shaderpack: {shader_filename}\n"
        if shader_filename
        else "[Render thread/INFO]: Shaders are disabled because enableShaders is set to false\n"
    )
    (run_dir / "client_latest.log").write_text(runtime_line, encoding="utf-8")
    resource = {
        "filename": f"{material_name}.zip",
        "path": f"/remote/instance/{material_name}.zip",
        "sha256": hashlib.sha256(f"material-{material_name}".encode()).hexdigest(),
        "size_bytes": 123,
    }
    shader_resource = (
        {
            "filename": shader_filename,
            "path": f"/remote/instance/{shader_filename}",
            "sha256": hashlib.sha256(f"shader-{shader}".encode()).hexdigest(),
            "size_bytes": 456,
        }
        if shader_filename
        else None
    )
    manifest = {
        "schema_version": manifest_schema_version,
        "run_id": f"episode-{profile}",
        "lane": "gpu0",
        "profile": {
            "name": profile,
            "asset_set": f"asset-{material_name}-{shader or 'none'}",
            "loader": "fabric",
            "quality": "high",
            "server_port": 25570,
            "config": {
                "options": {"gamma": "1.0", "fov": "0.0"},
                "shader_options": {"preset": "ultra"} if shader else {},
            },
        },
        "mc_version": "26.2",
        "resources": {
            "mods": [],
            "resourcepacks": [resource],
            "shaderpacks": [shader_resource] if shader_resource else [],
            "resourcepack_runtime": {
                "status": "pass",
                "expected_file_packs": [f"file/{material_name}.zip"],
                "actual_file_packs": [f"file/{material_name}.zip"],
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
        "world": {"seed": 1, "profile": "render_matrix_base", "state": world_state},
        "trajectory": {
            "sha256": trajectory_sha,
            "event_count": 0 if feedback else 1,
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
    if include_action_curriculum:
        manifest["action_curriculum"] = summarize_action_run(
            trajectory_path,
            action_evidence,
            execution_mode=execution_mode,
        )
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


def _write_pair_manifest(root: Path, pairs: list[dict[str, str]]) -> Path:
    path = root / "edit_pairs.json"
    _write_json(path, {"schema_version": 1, "pairs": pairs})
    return path


def _fixture(root: Path) -> tuple[list[str], Path, Path, Path, Path]:
    profiles = ["matrix_low", "matrix_textured", "matrix_night"]
    low = _write_episode(root, profiles[0], {"time": "noon", "weather": "clear"}, material="base")
    textured = _write_episode(
        root, profiles[1], {"time": "noon", "weather": "clear"}, material="textured"
    )
    night = _write_episode(
        root, profiles[2], {"time": "midnight", "weather": "clear"}, material="base"
    )
    strict = _write_compare(root, "compare_strict", [low, textured])
    diagnostic = _write_compare(root, "compare_all", [low, textured, night])
    review = _write_visual_review(root, profiles)
    pairs = _write_pair_manifest(
        root,
        [
            {
                "prompt": "Render the scene with a textured material style.",
                "source_episode": "episode-matrix_low",
                "target_episode": "episode-matrix_textured",
                "edit_axis": "material_style",
            },
            {
                "prompt": "Change the same scene from noon to midnight.",
                "source_episode": "episode-matrix_low",
                "target_episode": "episode-matrix_night",
                "edit_axis": "time_of_day",
            },
        ],
    )
    return profiles, strict, diagnostic, review, pairs


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


def _downgrade_to_legacy_v2_without_action_claim(
    manifest_path: Path,
    compare_paths: tuple[Path, ...],
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest.pop("action_curriculum")
    _write_json(manifest_path, manifest)
    replay_path = manifest_path.parent / "replay_log.jsonl"
    if replay_path.exists():
        records = [json.loads(line) for line in replay_path.read_text().splitlines()]
        for record in records[1:]:
            record.pop("execution_status", None)
        replay_path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
            encoding="utf-8",
        )
    _refresh_manifest_evidence(manifest_path, compare_paths)


def test_write_dataset_index_groups_variants_and_is_deterministic(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
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
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    strict_cohort = next(
        item for item in index["cohorts"] if item["role"] == "strict_rendering_matrix"
    )
    variants = [item for item in index["cohorts"] if item["role"] == "world_state_variant"]
    assert index["status"] == "accepted"
    assert index["schema_version"] == 2
    assert index["pair_manifest"]["path"] == "edit_pairs.json"
    assert {item["edit_axis"] for item in index["pairs"]} == {
        "material_style",
        "time_of_day",
    }
    assert len(strict_cohort["profile_names"]) == 2
    assert len(variants) == 1 and variants[0]["profile_names"] == ["matrix_night"]
    assert index["action_buckets"] == {
        "taxonomy_version": 1,
        "l1": {
            "episode_count": 3,
            "episode_ids": [
                "episode-matrix_low",
                "episode-matrix_night",
                "episode-matrix_textured",
            ],
        },
        "l1_l2": {"episode_count": 0, "episode_ids": []},
        "l1_l2_l3": {"episode_count": 0, "episode_ids": []},
        "l1_l2_l3_l4": {"episode_count": 0, "episode_ids": []},
    }
    assert all(item["action_curriculum"]["bucket"] == "l1" for item in index["episodes"])
    assert all(item["action_curriculum_source"] == "manifest" for item in index["episodes"])
    assert all(item["manifest"]["schema_version"] == 3 for item in index["episodes"])
    assert all(
        not Path(item["action_curriculum"]["evidence"]["path"]).is_absolute()
        for item in index["episodes"]
    )
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


def test_dataset_index_accepts_all_single_edit_axes(tmp_path: Path) -> None:
    snow_biome = {"id": "minecraft:snowy_plains", "precipitation": "snow"}
    specs = [
        ("base", {}, "base", None),
        ("styled", {}, "styled", None),
        ("shader", {}, "base", "ultra"),
        ("night", {"time": "midnight"}, "base", None),
        ("golden_hour", {"time": 12000}, "base", None),
        ("rain", {"weather": "rain"}, "base", None),
        ("snow_clear", {"biome": snow_biome}, "base", None),
        ("snowfall", {"biome": snow_biome, "weather": "rain"}, "base", None),
    ]
    runs = [
        _write_episode(tmp_path, name, state, material=material, shader=shader)
        for name, state, material, shader in specs
    ]
    profiles = [item[0] for item in specs]
    strict = _write_compare(tmp_path, "compare_strict", runs[:3])
    diagnostic = _write_compare(tmp_path, "compare_all", runs)
    review = _write_visual_review(tmp_path, profiles)
    pairs = _write_pair_manifest(
        tmp_path,
        [
            {
                "prompt": "Use the stylized material treatment.",
                "source_episode": "episode-base",
                "target_episode": "episode-styled",
                "edit_axis": "material_style",
            },
            {
                "prompt": "Enable the ultra-quality shader.",
                "source_episode": "episode-base",
                "target_episode": "episode-shader",
                "edit_axis": "shader_quality",
            },
            {
                "prompt": "Turn noon into midnight.",
                "source_episode": "episode-base",
                "target_episode": "episode-night",
                "edit_axis": "time_of_day",
            },
            {
                "prompt": "Turn noon into warm golden-hour light.",
                "source_episode": "episode-base",
                "target_episode": "episode-golden_hour",
                "edit_axis": "time_of_day",
            },
            {
                "prompt": "Make the clear day rainy.",
                "source_episode": "episode-base",
                "target_episode": "episode-rain",
                "edit_axis": "weather",
            },
            {
                "prompt": "Make it snow in the fixed snowy biome.",
                "source_episode": "episode-snow_clear",
                "target_episode": "episode-snowfall",
                "edit_axis": "snow_weather",
            },
        ],
    )

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="base",
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    by_axis = {item["edit_axis"]: item for item in index["pairs"]}
    assert set(by_axis) == {
        "material_style",
        "shader_quality",
        "time_of_day",
        "weather",
        "snow_weather",
    }
    time_axis_values = {
        (item["axis_values"]["source"], item["axis_values"]["target"])
        for item in index["pairs"]
        if item["edit_axis"] == "time_of_day"
    }
    assert time_axis_values == {
        ("noon", "golden_hour"),
        ("noon", "midnight"),
    }
    assert by_axis["snow_weather"]["axis_values"]["target"] == "snow"
    assert all(item["invariants"]["qa_passed"] is True for item in index["pairs"])


def test_dataset_rejects_arbitrary_numeric_time_of_day(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    manifest_path = tmp_path / "run_matrix_night" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["world"]["state"]["time"] = 11999
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match="numeric tick 12000"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("prompt", " ", "non-empty pair prompt"),
        ("edit_axis", None, "required property"),
        ("source_episode", "episode-missing", "missing source/target"),
    ],
)
def test_dataset_rejects_unbound_prompt_or_missing_pair_endpoint(
    tmp_path: Path,
    field: str,
    value: str | None,
    match: str,
) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    document = json.loads(pairs.read_text(encoding="utf-8"))
    if value is None:
        del document["pairs"][0][field]
    else:
        document["pairs"][0][field] = value
    _write_json(pairs, document)

    with pytest.raises(DatasetValidationError, match=match):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_compound_edit_declared_as_single_axis(tmp_path: Path) -> None:
    base = _write_episode(tmp_path, "base", {}, material="base")
    compound = _write_episode(
        tmp_path,
        "compound",
        {"time": "midnight"},
        material="styled",
    )
    profiles = ["base", "compound"]
    strict = _write_compare(tmp_path, "compare_strict", [base])
    diagnostic = _write_compare(tmp_path, "compare_all", [base, compound])
    pairs = _write_pair_manifest(
        tmp_path,
        [
            {
                "prompt": "Change only the material style.",
                "source_episode": "episode-base",
                "target_episode": "episode-compound",
                "edit_axis": "material_style",
            }
        ],
    )

    with pytest.raises(DatasetValidationError, match="actual differences"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="base",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
        )


@pytest.mark.parametrize("drift", ["scene", "spawn", "trajectory", "capture"])
def test_dataset_rejects_pair_invariant_drift(tmp_path: Path, drift: str) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    manifest_path = tmp_path / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if drift == "scene":
        manifest["world"]["state"]["scene"]["origin"] = [1, 64, 0]
    elif drift == "spawn":
        manifest["world"]["state"]["player"]["x"] = 1
    elif drift == "trajectory":
        manifest["trajectory"]["strategy"] = "different_strategy"
    else:
        manifest["capture"]["settings"]["hide_hud"] = True
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match=f"crosses {drift}"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_conflicting_target_and_unpaired_episode(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    document = json.loads(pairs.read_text(encoding="utf-8"))
    document["pairs"].append(
        {
            "prompt": "Reuse the target for a conflicting source.",
            "source_episode": "episode-matrix_night",
            "target_episode": "episode-matrix_textured",
            "edit_axis": "material_style",
        }
    )
    _write_json(pairs, document)
    with pytest.raises(DatasetValidationError, match="conflicting target"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    isolated_root = tmp_path / "unpaired"
    profiles, strict, diagnostic, review, pairs = _fixture(isolated_root)
    document = json.loads(pairs.read_text(encoding="utf-8"))
    document["pairs"] = document["pairs"][:1]
    _write_json(pairs, document)
    with pytest.raises(DatasetValidationError, match="do not cover every accepted episode"):
        write_dataset_index(
            isolated_root,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_without_manual_review_is_only_automated_pass(tmp_path: Path) -> None:
    profiles, strict, diagnostic, _review, pairs = _fixture(tmp_path)

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
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
    pairs = _write_pair_manifest(
        tmp_path,
        [
            {
                "prompt": "Apply the material pack while preserving the feedback route.",
                "source_episode": "episode-feedback_vanilla",
                "target_episode": "episode-feedback_material",
                "edit_axis": "material_style",
            }
        ],
    )

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile=profiles[0],
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    assert index["cohorts"][0]["role"] == "policy_aligned_rendering_matrix"
    assert index["invariants"]["trajectory_execution_mode"] == ("online_position_yaw_feedback")
    assert index["invariants"]["trajectory_event_count"] == 0
    assert all("navigation" in episode for episode in index["episodes"])
    assert all(episode["trajectory"]["route_point_count"] == 3 for episode in index["episodes"])
    assert all(
        episode["action_curriculum"]["observed_level"] == 1
        and episode["action_curriculum"]["observed_semantic_action_counts"][
            "deliberate_jump"
        ]
        == 0
        and episode["action_curriculum"]["controller_recovery_counts"]["jump_taps"] == 0
        for episode in index["episodes"]
    )
    assert all(
        episode["action_curriculum_source"] == "manifest"
        for episode in index["episodes"]
    )
    assert index["status"] == "accepted"
    assert index["manual_review"] is not None


def test_legacy_v2_feedback_derives_action_from_navigation_log(tmp_path: Path) -> None:
    profiles = ["legacy_feedback_vanilla", "legacy_feedback_material"]
    runs = [
        _write_episode(
            tmp_path,
            profile,
            {"time": "noon", "weather": "clear"},
            feedback=True,
            manifest_schema_version=2,
            include_action_curriculum=False,
        )
        for profile in profiles
    ]
    strict = _write_compare(tmp_path, "feedback_compare", runs)
    diagnostic = _write_compare(tmp_path, "feedback_diagnostic", runs)
    pairs = _write_pair_manifest(
        tmp_path,
        [
            {
                "prompt": "Apply the material while preserving legacy feedback navigation.",
                "source_episode": "episode-legacy_feedback_vanilla",
                "target_episode": "episode-legacy_feedback_material",
                "edit_axis": "material_style",
            }
        ],
    )

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile=profiles[0],
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
    )

    assert all(
        episode["action_curriculum_source"] == "derived_legacy_replay"
        and episode["action_curriculum"]["evidence"]["kind"] == "navigation_log"
        and episode["action_curriculum"]["observed_level"] == 1
        for episode in index["episodes"]
    )


def test_dataset_rejects_profile_or_commit_drift(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_missing_resource_provenance(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_runtime_resolution_mismatch(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_stale_positions_and_partial_diagnostic(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    positions = tmp_path / "run_matrix_textured" / "positions.jsonl"
    positions.write_text('{"idx": 0, "x": 99}\n', encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="stale positions"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path / "partial")
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_rejects_missing_or_tampered_action_evidence(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    replay_path = tmp_path / "run_matrix_textured" / "replay_log.jsonl"
    replay_path.unlink()

    with pytest.raises(DatasetValidationError, match="replay evidence is missing"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    root = tmp_path / "tampered"
    profiles, strict, diagnostic, review, pairs = _fixture(root)
    manifest_path = root / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["action_curriculum"]["observed_semantic_action_counts"][
        "navigation_move"
    ] += 1
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))

    with pytest.raises(DatasetValidationError, match="does not match replay evidence"):
        write_dataset_index(
            root,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_dataset_derives_strict_action_contract_from_legacy_v2_replay(
    tmp_path: Path,
) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    for profile in profiles:
        _downgrade_to_legacy_v2_without_action_claim(
            tmp_path / f"run_{profile}" / "manifest.json",
            (strict, diagnostic),
        )

    index = write_dataset_index(
        tmp_path,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )

    assert all(
        item["action_curriculum_source"] == "derived_legacy_replay"
        and item["manifest"]["schema_version"] == 2
        and item["action_curriculum"]["observed_semantic_action_counts"][
            "navigation_move"
        ]
        == 1
        for item in index["episodes"]
    )
    assert index["action_buckets"]["l1"]["episode_count"] == len(profiles)


def test_legacy_v2_action_derivation_rejects_missing_or_tampered_replay(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"
    profiles, strict, diagnostic, review, pairs = _fixture(missing_root)
    manifest_path = missing_root / "run_matrix_textured" / "manifest.json"
    _downgrade_to_legacy_v2_without_action_claim(manifest_path, (strict, diagnostic))
    (manifest_path.parent / "replay_log.jsonl").unlink()
    with pytest.raises(DatasetValidationError, match="replay evidence is missing"):
        write_dataset_index(
            missing_root,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    tampered_root = tmp_path / "tampered_legacy"
    profiles, strict, diagnostic, review, pairs = _fixture(tampered_root)
    manifest_path = tampered_root / "run_matrix_textured" / "manifest.json"
    _downgrade_to_legacy_v2_without_action_claim(manifest_path, (strict, diagnostic))
    replay_path = manifest_path.parent / "replay_log.jsonl"
    records = [json.loads(line) for line in replay_path.read_text().splitlines()]
    records[1]["event"]["key"] = "d"
    replay_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    with pytest.raises(DatasetValidationError, match="exactly match trajectory events"):
        write_dataset_index(
            tampered_root,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )


def test_v3_requires_action_claim_and_v2_claim_is_still_manifest_sourced(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "v3_missing"
    profiles, strict, diagnostic, review, pairs = _fixture(missing_root)
    manifest_path = missing_root / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("action_curriculum")
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))
    with pytest.raises(DatasetValidationError, match="schema v3 requires action_curriculum"):
        write_dataset_index(
            missing_root,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )

    claimed_root = tmp_path / "v2_claimed"
    profiles, strict, diagnostic, review, pairs = _fixture(claimed_root)
    manifest_path = claimed_root / "run_matrix_textured" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    _write_json(manifest_path, manifest)
    _refresh_manifest_evidence(manifest_path, (strict, diagnostic))
    index = write_dataset_index(
        claimed_root,
        expected_profiles=profiles,
        primary_profile="matrix_low",
        generator_commit=GENERATOR_COMMIT,
        pair_manifest=pairs,
        strict_compare_report=strict,
        diagnostic_compare_reports=[diagnostic],
        visual_review=review,
    )
    by_profile = {item["profile_name"]: item for item in index["episodes"]}
    assert by_profile["matrix_textured"]["manifest"]["schema_version"] == 2
    assert by_profile["matrix_textured"]["action_curriculum_source"] == "manifest"


def test_dataset_rejects_self_review_nan_and_symlink(tmp_path: Path) -> None:
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
    review_data = json.loads(review.read_text(encoding="utf-8"))
    review_data["evidence"] = [review.relative_to(tmp_path).as_posix()]
    _write_json(review, review_data)
    with pytest.raises(DatasetValidationError, match="self-referential"):
        write_dataset_index(
            tmp_path,
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            expected_fps=float("nan"),
        )

    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path / "symlink")
    (tmp_path / "symlink" / "unsafe-link").symlink_to("/does/not/exist")
    with pytest.raises(DatasetValidationError, match="symlinks"):
        write_dataset_index(
            tmp_path / "symlink",
            expected_profiles=profiles,
            primary_profile="matrix_low",
            generator_commit=GENERATOR_COMMIT,
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )
    assert not (tmp_path / "symlink" / "dataset_index.json").exists()


def test_collect_runtime_logs_copies_exact_profile_set(tmp_path: Path) -> None:
    profiles, _strict, _diagnostic, _review, _pairs = _fixture(tmp_path)
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
    profiles, strict, diagnostic, review, pairs = _fixture(tmp_path)
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
            pair_manifest=pairs,
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
            pair_manifest=pairs,
            strict_compare_report=strict,
            diagnostic_compare_reports=[diagnostic],
            visual_review=review,
        )
