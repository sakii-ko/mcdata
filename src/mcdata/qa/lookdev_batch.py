from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LookdevRunRequest:
    profile: str
    run_dir: Path | None
    render_rc: int
    qa_rc: int
    unique_run_count: int
    expected_trajectory_sha256: str
    shared_trajectory: Path
    instance_manifest: Path
    bootstrap_manifest_sha256: str
    lane: str
    strategy: str
    server_port: int
    display: str
    config_unchanged: bool
    bootstrap_set_unchanged: bool
    expected_width: int = 1920
    expected_height: int = 1080
    expected_fps: float = 24.0
    expected_duration_sec: float = 60.0
    expected_qa_frames: int = 12


def file_sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_mapping(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"{path.name}: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return {}, f"{path.name}: expected a JSON object"
    return value, None


def _manifest_checks(
    manifest: dict[str, Any], request: LookdevRunRequest
) -> dict[str, bool]:
    profile = manifest.get("profile")
    trajectory = manifest.get("trajectory")
    capture = manifest.get("capture")
    if not isinstance(profile, dict):
        profile = {}
    if not isinstance(trajectory, dict):
        trajectory = {}
    if not isinstance(capture, dict):
        capture = {}
    settings = capture.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    return {
        "manifest_identity": (
            profile.get("name") == request.profile
            and manifest.get("lane") == request.lane
            and profile.get("server_port") == request.server_port
            and manifest.get("error") in (None, "")
        ),
        "manifest_trajectory": (
            trajectory.get("strategy") == request.strategy
            and trajectory.get("sha256") == request.expected_trajectory_sha256
        ),
        "manifest_capture": (
            capture.get("enabled") is True
            and settings.get("width") == request.expected_width
            and settings.get("height") == request.expected_height
            and settings.get("fps") == request.expected_fps
            and settings.get("display") == request.display
        ),
    }


def _qa_checks(
    qa_report: dict[str, Any], request: LookdevRunRequest, run_dir: Path
) -> dict[str, bool]:
    route = qa_report.get("route_reference")
    probe = qa_report.get("probe")
    frames = qa_report.get("frames")
    evidence = qa_report.get("evidence")
    if not isinstance(route, dict):
        route = {}
    if not isinstance(probe, dict):
        probe = {}
    if not isinstance(frames, list):
        frames = []
    if not isinstance(evidence, dict):
        evidence = {}
    video_evidence = evidence.get("video")
    trajectory_evidence = evidence.get("trajectory")
    if not isinstance(video_evidence, dict):
        video_evidence = {}
    if not isinstance(trajectory_evidence, dict):
        trajectory_evidence = {}
    duration = float(probe.get("duration_sec") or 0.0)
    fps = float(probe.get("fps") or 0.0)
    return {
        "route_reference": route.get("passed") is True,
        "capture_probe": (
            probe.get("codec") == "h264"
            and probe.get("width") == request.expected_width
            and probe.get("height") == request.expected_height
            and abs(fps - request.expected_fps) <= 0.01
            and abs(duration - request.expected_duration_sec) <= 1.0
        ),
        "qa_frames": (
            len(frames) == request.expected_qa_frames
            and all(_frame_has_no_black_border(frame) for frame in frames)
        ),
        "qa_outputs": (
            (run_dir / "qa_report.md").is_file()
            and (run_dir / "contact_sheet.jpg").is_file()
        ),
        "qa_evidence": (
            video_evidence.get("sha256") == file_sha256(run_dir / "capture.mp4")
            and trajectory_evidence.get("sha256")
            == request.expected_trajectory_sha256
        ),
    }


def _frame_has_no_black_border(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    border = value.get("border")
    return isinstance(border, dict) and border.get("has_black_border") is False


def _missing_run_checks() -> dict[str, bool]:
    return {
        key: False
        for key in (
            "manifest_identity",
            "manifest_trajectory",
            "manifest_capture",
            "route_reference",
            "capture_probe",
            "qa_frames",
            "qa_outputs",
            "qa_evidence",
            "capture_exists",
            "run_trajectory_unchanged",
        )
    }


def validate_lookdev_run(request: LookdevRunRequest) -> dict[str, Any]:
    errors: list[str] = []
    run_dir = request.run_dir
    manifest: dict[str, Any] = {}
    qa_report: dict[str, Any] = {}
    if run_dir is None:
        errors.append("run directory could not be uniquely located")
    else:
        manifest, manifest_error = _load_mapping(run_dir / "manifest.json")
        qa_report, qa_error = _load_mapping(run_dir / "qa_report.json")
        errors.extend(error for error in (manifest_error, qa_error) if error)

    checks = {
        "render_command": request.render_rc == 0,
        "qa_command": request.qa_rc == 0,
        "unique_run_locator": request.unique_run_count == 1,
        "config_unchanged": request.config_unchanged,
        "bootstrap_set_unchanged": request.bootstrap_set_unchanged,
        "bootstrap_manifest_unchanged": (
            file_sha256(request.instance_manifest)
            == request.bootstrap_manifest_sha256
        ),
        "shared_trajectory_unchanged": (
            file_sha256(request.shared_trajectory)
            == request.expected_trajectory_sha256
        ),
    }
    if run_dir is not None:
        checks.update(_manifest_checks(manifest, request))
        checks.update(_qa_checks(qa_report, request, run_dir))
        checks.update(
            {
                "capture_exists": (
                    (run_dir / "capture.mp4").is_file()
                    and (run_dir / "capture.mp4").stat().st_size > 0
                ),
                "run_trajectory_unchanged": (
                    file_sha256(run_dir / "trajectory.json")
                    == request.expected_trajectory_sha256
                ),
            }
        )
    else:
        checks.update(_missing_run_checks())

    probe = qa_report.get("probe") if isinstance(qa_report.get("probe"), dict) else {}
    route = (
        qa_report.get("route_reference")
        if isinstance(qa_report.get("route_reference"), dict)
        else {}
    )
    return {
        "profile": request.profile,
        "run_dir": str(run_dir) if run_dir is not None else None,
        "passed": all(checks.values()),
        "render_rc": request.render_rc,
        "qa_rc": request.qa_rc,
        "unique_run_count": request.unique_run_count,
        "route_reference_passed": route.get("passed") is True,
        "checks": checks,
        "probe": {
            key: probe.get(key)
            for key in ("codec", "width", "height", "fps", "duration_sec")
        },
        "warnings": qa_report.get("warnings", []),
        "validation_errors": errors,
    }


def write_validation_record(record: dict[str, Any], record_path: Path, results: Path) -> None:
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def summarize_batch(
    records: list[dict[str, Any]],
    *,
    expected_profiles: list[str],
    config_unchanged: bool,
    bootstrap_unchanged: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actual_profiles = [record.get("profile") for record in records]
    failed_profiles = [
        str(record.get("profile")) for record in records if record.get("passed") is not True
    ]
    profile_order_passed = actual_profiles == expected_profiles
    passed_count = sum(record.get("passed") is True for record in records)
    passed = (
        profile_order_passed
        and passed_count == len(expected_profiles)
        and config_unchanged
        and bootstrap_unchanged
    )
    return {
        **(metadata or {}),
        "status": "complete",
        "passed": passed,
        "expected_profile_count": len(expected_profiles),
        "record_count": len(records),
        "passed_count": passed_count,
        "failed_count": len(expected_profiles) - passed_count,
        "failed_profiles": failed_profiles,
        "profile_order_passed": profile_order_passed,
        "config_unchanged": config_unchanged,
        "bootstrap_manifests_unchanged": bootstrap_unchanged,
    }


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return normalized == "true"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate look-dev batch evidence")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-run")
    for option in ("profile", "expected-trajectory-sha256", "lane", "strategy", "display"):
        validate.add_argument(f"--{option}", required=True)
    for option in ("render-rc", "qa-rc", "unique-run-count", "server-port"):
        validate.add_argument(f"--{option}", required=True, type=int)
    for option in (
        "shared-trajectory",
        "instance-manifest",
        "record",
        "results",
    ):
        validate.add_argument(f"--{option}", required=True, type=Path)
    validate.add_argument("--run-dir", default="")
    validate.add_argument("--bootstrap-manifest-sha256", required=True)
    validate.add_argument("--config-unchanged", required=True, type=_parse_bool)
    validate.add_argument("--bootstrap-set-unchanged", required=True, type=_parse_bool)

    complete = subparsers.add_parser("complete-batch")
    complete.add_argument("--results", required=True, type=Path)
    complete.add_argument("--expected-profiles", required=True, type=Path)
    complete.add_argument("--output", required=True, type=Path)
    complete.add_argument("--config-unchanged", required=True, type=_parse_bool)
    complete.add_argument("--bootstrap-unchanged", required=True, type=_parse_bool)
    complete.add_argument("--batch-id", required=True)
    complete.add_argument("--lane", required=True)
    complete.add_argument("--server-port", required=True, type=int)
    complete.add_argument("--expected-sync", required=True)
    return parser


def _validate_command(args: argparse.Namespace) -> int:
    request = LookdevRunRequest(
        profile=args.profile,
        run_dir=Path(args.run_dir) if args.run_dir else None,
        render_rc=args.render_rc,
        qa_rc=args.qa_rc,
        unique_run_count=args.unique_run_count,
        expected_trajectory_sha256=args.expected_trajectory_sha256,
        shared_trajectory=args.shared_trajectory,
        instance_manifest=args.instance_manifest,
        bootstrap_manifest_sha256=args.bootstrap_manifest_sha256,
        lane=args.lane,
        strategy=args.strategy,
        server_port=args.server_port,
        display=args.display,
        config_unchanged=args.config_unchanged,
        bootstrap_set_unchanged=args.bootstrap_set_unchanged,
    )
    record = validate_lookdev_run(request)
    write_validation_record(record, args.record, args.results)
    print(
        f"RESULT:{request.profile}:passed={str(record['passed']).lower()}:"
        f"route_reference.passed={str(record['route_reference_passed']).lower()}:"
        f"run_dir={record['run_dir']}"
    )
    return 0 if record["passed"] else 1


def _complete_command(args: argparse.Namespace) -> int:
    records = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected_profiles = [
        line.strip()
        for line in args.expected_profiles.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completion = summarize_batch(
        records,
        expected_profiles=expected_profiles,
        config_unchanged=args.config_unchanged,
        bootstrap_unchanged=args.bootstrap_unchanged,
        metadata={
            "batch_id": args.batch_id,
            "lane": args.lane,
            "server_port": args.server_port,
            "expected_sync": args.expected_sync,
        },
    )
    args.output.write_text(
        json.dumps(completion, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("ALL_DONE:" + json.dumps(completion, sort_keys=True))
    return 0 if completion["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "validate-run":
        return _validate_command(args)
    return _complete_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
