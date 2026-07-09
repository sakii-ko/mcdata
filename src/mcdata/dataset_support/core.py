from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class DatasetValidationError(ValueError):
    """Raised when a run collection cannot form an accepted dataset."""


def canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DatasetValidationError(f"Dataset metadata is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"Could not read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetValidationError(f"Expected a JSON object in {path}")
    return value


def relative_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise DatasetValidationError(f"Dataset evidence is outside {root}: {path}") from exc


def artifact(root: Path, path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise DatasetValidationError(f"Required regular file is missing: {path}")
    return {
        "path": relative_path(root, path),
        "sha256": file_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DatasetValidationError(f"Expected {label} to be an object")
    return value


def require_hash(value: Any, length: int, label: str) -> str:
    if not isinstance(value, str) or len(value) != length:
        raise DatasetValidationError(f"Expected {label} to be a {length}-character hex digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise DatasetValidationError(f"Expected {label} to be hexadecimal") from exc
    return value


def require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetValidationError(f"Expected non-empty {label}")
    return value


def validate_report_evidence(
    evidence: Any,
    artifacts: dict[str, dict[str, Any]],
    label: str,
) -> None:
    values = require_mapping(evidence, f"{label}.evidence")
    if set(values) != set(artifacts):
        raise DatasetValidationError(f"{label} evidence set does not match source artifacts")
    for key, source_artifact in artifacts.items():
        item = require_mapping(values.get(key), f"{label}.evidence.{key}")
        if (
            item.get("sha256") != source_artifact["sha256"]
            or item.get("size_bytes") != source_artifact["size_bytes"]
        ):
            raise DatasetValidationError(f"{label} has stale {key} evidence")


def shader_runtime(log_path: Path, shaderpacks: list[dict[str, Any]]) -> dict[str, Any]:
    if len(shaderpacks) > 1:
        raise DatasetValidationError("Only one active shaderpack can be audited per episode")
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise DatasetValidationError(
            f"Could not read client runtime log {log_path}: {exc}"
        ) from exc
    marker = "Using shaderpack: "
    observed = sorted({line.split(marker, 1)[1].strip() for line in lines if marker in line})
    expected = shaderpacks[0]["filename"] if shaderpacks else None
    if expected is None:
        disabled = [line for line in lines if "Shaders are disabled because" in line]
        if observed or not disabled:
            raise DatasetValidationError(
                f"Vanilla shader-disable runtime evidence is missing: {log_path}"
            )
        evidence_line = disabled[-1]
    else:
        if observed != [expected]:
            raise DatasetValidationError(
                f"Shader runtime mismatch in {log_path}: expected={expected!r}, observed={observed!r}"
            )
        evidence_line = next(line for line in reversed(lines) if marker + expected in line)
    return {
        "status": "pass",
        "expected_shaderpack": expected,
        "observed_shaderpacks": observed,
        "evidence_line": evidence_line,
    }


def checksum_content(root: Path, index_tmp: Path) -> str:
    excluded = {
        "SHA256SUMS",
        "SHA256SUMS.tmp",
        "dataset_index.json",
        "dataset_index.json.tmp",
    }
    paths = list(root.rglob("*"))
    symlinks = [path for path in paths if path.is_symlink()]
    if symlinks:
        raise DatasetValidationError(f"Dataset contains symlinks: {symlinks!r}")
    files = sorted(path for path in paths if path.is_file() and path.name not in excluded)
    entries = [(relative_path(root, path), file_sha256(path)) for path in files]
    entries.append(("dataset_index.json", file_sha256(index_tmp)))
    return "".join(f"{digest}  {path}\n" for path, digest in sorted(entries))


def write_dataset_outputs(root: Path, index: dict[str, Any]) -> None:
    index_path = root / "dataset_index.json"
    sums_path = root / "SHA256SUMS"
    index_tmp = index_path.with_name(f"{index_path.name}.tmp")
    sums_tmp = sums_path.with_name(f"{sums_path.name}.tmp")
    for path in (index_tmp, sums_tmp):
        path.unlink(missing_ok=True)
    try:
        index_tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sums_tmp.write_text(checksum_content(root, index_tmp), encoding="utf-8")
        os.replace(sums_tmp, sums_path)
        os.replace(index_tmp, index_path)
    finally:
        index_tmp.unlink(missing_ok=True)
        sums_tmp.unlink(missing_ok=True)


def validate_index_schema(index: dict[str, Any]) -> None:
    try:
        from jsonschema import ValidationError, validate  # optional until dataset packaging runs
    except ImportError as exc:
        raise DatasetValidationError(
            "dataset-index requires jsonschema; install the mcdata package dependencies"
        ) from exc
    schema_path = Path(__file__).parents[1] / "schemas" / "dataset_index.schema.json"
    schema = load_json(schema_path)
    try:
        validate(index, schema)
    except ValidationError as exc:
        raise DatasetValidationError(
            f"Generated dataset index violates its schema: {exc.message}"
        ) from exc


def collect_runtime_logs(
    dataset_root: Path,
    *,
    expected_profiles: Sequence[str],
) -> list[Path]:
    """Snapshot each episode's client log into its run directory for portable auditing."""
    root = dataset_root.resolve()
    manifests = []
    for path in sorted(root.glob("*/manifest.json")):
        manifest = load_json(path)
        profile = require_mapping(manifest.get("profile"), "manifest.profile").get("name")
        resources = require_mapping(manifest.get("resources"), "manifest.resources")
        runtime = require_mapping(resources.get("resourcepack_runtime"), "resourcepack_runtime")
        manifests.append((path.parent, profile, runtime.get("log_path")))
    actual = [item[1] for item in manifests]
    if (
        not actual
        or len(actual) != len(set(actual))
        or set(actual) != set(expected_profiles)
        or len(expected_profiles) != len(set(expected_profiles))
    ):
        raise DatasetValidationError("Runtime-log profile set does not match expected profiles")
    outputs = []
    for run_dir, profile, source_value in manifests:
        source = Path(require_nonempty_string(source_value, f"runtime log for {profile}"))
        if source.is_symlink() or not source.is_file():
            raise DatasetValidationError(
                f"Runtime log is missing or unsafe for {profile}: {source}"
            )
        destination = run_dir / "client_latest.log"
        tmp = destination.with_name(f"{destination.name}.tmp")
        try:
            shutil.copy2(source, tmp)
            if file_sha256(tmp) != file_sha256(source):
                raise DatasetValidationError(f"Runtime log copy hash mismatch for {profile}")
            os.replace(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)
        outputs.append(destination)
    return outputs
