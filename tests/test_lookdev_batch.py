from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from mcdata.config import load_profile
from mcdata.qa.lookdev_batch import (
    LookdevRunRequest,
    file_sha256,
    summarize_batch,
    validate_lookdev_run,
)

ROOT = Path(__file__).resolve().parent.parent
ACCEPTED_PROFILES = [
    "feedback_vanilla_1080p",
    "lookdev_vanilla_unbound_1080p",
    "feedback_legendary_rt_1080p",
    "feedback_legendary_rt_unbound_1080p",
    "lookdev_legendary_rt_bliss_1080p",
    "preview_legendary_rt_solas_1080p",
    "lookdev_legendary_rt_unbound_seuspbr_1080p",
    "lookdev_legendary_rt_solas_seuspbr_1080p",
    "feedback_modernarch_1080p",
    "lookdev_modernarch_unbound_1080p",
    "lookdev_optimum_1080p",
    "preview_optimum_unbound_1080p",
    "lookdev_patrix_full_1080p",
    "lookdev_patrix_full_unbound_1080p",
    "lookdev_stylista_1080p",
    "preview_stylista_unbound_1080p",
    "lookdev_prettyrealistic_1080p",
    "lookdev_prettyrealistic_unbound_1080p",
    "lookdev_style_vanilla_1080p",
    "lookdev_style_stylista_1080p",
    "lookdev_style_reimagined_1080p",
    "lookdev_style_ashen_1080p",
    "lookdev_style_simplified_1080p",
    "lookdev_style_quadral_1080p",
    "lookdev_style_bare_bones_pbr_1080p",
    "lookdev_style_natural_1080p",
]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _passing_request(tmp_path: Path) -> LookdevRunRequest:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    trajectory = b'{"duration_sec":59.034,"events":[],"route":[]}\n'
    (run_dir / "trajectory.json").write_bytes(trajectory)
    shared = tmp_path / "shared.json"
    shared.write_bytes(trajectory)
    trajectory_sha = file_sha256(shared)
    assert trajectory_sha is not None

    capture = run_dir / "capture.mp4"
    capture.write_bytes(b"fake h264 capture evidence")
    capture_sha = file_sha256(capture)
    assert capture_sha is not None
    instance_manifest = tmp_path / "instance" / "mcdata_manifest.json"
    _write_json(instance_manifest, {"profile": "candidate", "minecraft_version": "26.2"})
    bootstrap_sha = file_sha256(instance_manifest)
    assert bootstrap_sha is not None

    _write_json(
        run_dir / "manifest.json",
        {
            "lane": "lookdev_batch",
            "profile": {"name": "candidate", "server_port": 25800},
            "error": None,
            "trajectory": {
                "strategy": "lookdev_showcase_60s",
                "sha256": trajectory_sha,
            },
            "capture": {
                "enabled": True,
                "settings": {
                    "width": 1920,
                    "height": 1080,
                    "fps": 24,
                    "display": ":77",
                },
                "ffprobe": {
                    "streams": [
                        {
                            "width": 1920,
                            "height": 1080,
                            "avg_frame_rate": "24/1",
                            "nb_frames": "1440",
                        }
                    ]
                },
            },
            "git": {"commit": "a" * 40, "dirty": False, "source": "sync_commit"},
            "resources": {"resourcepack_runtime": {"status": "pass"}},
        },
    )
    _write_json(
        run_dir / "qa_report.json",
        {
            "probe": {
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "fps": 24.0,
                "duration_sec": 60.0,
            },
            "route_reference": {"passed": True},
            "frames": [
                {"border": {"has_black_border": False}}
                for _ in range(12)
            ],
            "evidence": {
                "video": {"sha256": capture_sha},
                "trajectory": {"sha256": trajectory_sha},
            },
            "warnings": [],
        },
    )
    (run_dir / "qa_report.md").write_text("# QA\n", encoding="utf-8")
    (run_dir / "contact_sheet.jpg").write_bytes(b"fake contact sheet")
    return LookdevRunRequest(
        profile="candidate",
        run_dir=run_dir,
        render_rc=0,
        qa_rc=0,
        unique_run_count=1,
        expected_trajectory_sha256=trajectory_sha,
        expected_sync="a" * 40,
        shared_trajectory=shared,
        instance_manifest=instance_manifest,
        bootstrap_manifest_sha256=bootstrap_sha,
        lane="lookdev_batch",
        strategy="lookdev_showcase_60s",
        server_port=25800,
        display=":77",
        config_unchanged=True,
        bootstrap_set_unchanged=True,
    )


def test_passing_run_requires_hash_bound_explicit_route_pass(tmp_path: Path) -> None:
    record = validate_lookdev_run(_passing_request(tmp_path))

    assert record["passed"] is True
    assert record["route_reference_passed"] is True
    assert all(record["checks"].values())


def test_run_fails_when_route_reference_does_not_explicitly_pass(tmp_path: Path) -> None:
    request = _passing_request(tmp_path)
    qa_path = request.run_dir / "qa_report.json"  # type: ignore[union-attr]
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["route_reference"]["passed"] = False
    _write_json(qa_path, qa)

    record = validate_lookdev_run(request)

    assert record["passed"] is False
    assert record["route_reference_passed"] is False
    assert record["checks"]["route_reference"] is False


def test_run_fails_on_lane_or_fixed_server_port_drift(tmp_path: Path) -> None:
    request = _passing_request(tmp_path)
    manifest_path = request.run_dir / "manifest.json"  # type: ignore[union-attr]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lane"] = "different_lane"
    manifest["profile"]["server_port"] = 25801
    _write_json(manifest_path, manifest)

    record = validate_lookdev_run(request)

    assert record["passed"] is False
    assert record["checks"]["manifest_identity"] is False


def test_run_fails_on_warning_provenance_or_resourcepack_runtime_drift(tmp_path: Path) -> None:
    request = _passing_request(tmp_path)
    qa_path = request.run_dir / "qa_report.json"  # type: ignore[union-attr]
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["warnings"] = ["route reference check failed"]
    _write_json(qa_path, qa)
    manifest_path = request.run_dir / "manifest.json"  # type: ignore[union-attr]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["git"]["commit"] = "b" * 40
    manifest["resources"]["resourcepack_runtime"]["status"] = "fail"
    _write_json(manifest_path, manifest)

    record = validate_lookdev_run(request)

    assert record["passed"] is False
    assert record["checks"]["qa_warnings"] is False
    assert record["checks"]["manifest_provenance"] is False
    assert record["checks"]["manifest_resourcepack_runtime"] is False


def test_batch_summary_requires_exact_profile_order_and_all_passes() -> None:
    records = [{"profile": profile, "passed": True} for profile in ACCEPTED_PROFILES]

    passing = summarize_batch(
        records,
        expected_profiles=ACCEPTED_PROFILES,
        config_unchanged=True,
        bootstrap_unchanged=True,
    )
    reordered = summarize_batch(
        list(reversed(records)),
        expected_profiles=ACCEPTED_PROFILES,
        config_unchanged=True,
        bootstrap_unchanged=True,
    )

    assert passing["passed"] is True
    assert passing["expected_profile_count"] == 26
    assert reordered["passed"] is False
    assert reordered["profile_order_passed"] is False


def test_batch_summary_supports_an_exact_repair_subset() -> None:
    subset = [
        "feedback_vanilla_1080p",
        "lookdev_legendary_rt_bliss_1080p",
        "lookdev_style_natural_1080p",
    ]
    records = [{"profile": profile, "passed": True} for profile in subset]

    passing = summarize_batch(
        records,
        expected_profiles=subset,
        config_unchanged=True,
        bootstrap_unchanged=True,
    )
    missing = summarize_batch(
        records[:-1],
        expected_profiles=subset,
        config_unchanged=True,
        bootstrap_unchanged=True,
    )

    assert passing["passed"] is True
    assert passing["expected_profile_count"] == len(subset)
    assert passing["record_count"] == len(subset)
    assert missing["passed"] is False
    assert missing["profile_order_passed"] is False


def test_batch_script_exposes_exact_accepted_profile_set() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "lookdev_render_batch.sh"), "--print-profiles"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ACCEPTED_PROFILES
    assert all("yitalith" not in profile for profile in ACCEPTED_PROFILES)


def test_batch_script_prints_a_valid_subset_without_creating_run_root(
    tmp_path: Path,
) -> None:
    profiles_file = tmp_path / "repair-profiles.txt"
    profiles_file.write_text(
        "\nfeedback_vanilla_1080p\nlookdev_legendary_rt_bliss_1080p\n\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "lookdev_render_batch.sh"), "--print-profiles"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "BATCH_PROFILES_FILE": str(profiles_file),
            "BATCH_ID": "must_not_be_created",
            "MCDATA_TMP_ROOT": str(runtime_root),
        },
    )

    assert result.stdout.splitlines() == [
        "feedback_vanilla_1080p",
        "lookdev_legendary_rt_bliss_1080p",
    ]
    assert not runtime_root.exists()


def test_batch_script_rejects_empty_duplicate_and_invalid_subsets_before_output(
    tmp_path: Path,
) -> None:
    cases = {
        "empty": ("\n\n", "does not contain any profiles"),
        "duplicate": (
            "feedback_vanilla_1080p\nfeedback_vanilla_1080p\n",
            "duplicate profile",
        ),
        "invalid": ("feedback_vanilla_1080p\n../escape\n", "invalid profile name"),
    }
    runtime_root = tmp_path / "runtime"
    for case_name, (contents, expected_error) in cases.items():
        profiles_file = tmp_path / f"{case_name}.txt"
        profiles_file.write_text(contents, encoding="utf-8")
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "lookdev_render_batch.sh"),
                "--print-profiles",
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "BATCH_PROFILES_FILE": str(profiles_file),
                "BATCH_ID": "must_not_be_created",
                "MCDATA_TMP_ROOT": str(runtime_root),
            },
        )

        assert result.returncode == 2
        assert expected_error in result.stderr
        assert not runtime_root.exists()


def test_accepted_profiles_resolve_to_the_shared_capture_contract() -> None:
    for profile_name in ACCEPTED_PROFILES:
        profile = load_profile(ROOT / "configs", profile_name)
        assert profile["game_version"] == "26.2"
        assert profile["width"] == 1920
        assert profile["height"] == 1080
        assert profile["capture_fps"] == 24
