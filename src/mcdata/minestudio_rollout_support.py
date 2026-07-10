from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any


class MineStudioRolloutImportError(ValueError):
    """Raised when a raw rollout cannot be imported without provenance loss."""


def artifact_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise MineStudioRolloutImportError(f"{label} artifact path must be non-empty")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != value:
        raise MineStudioRolloutImportError(f"{label} artifact path must be normalized and relative")
    path = root / Path(*pure.parts)
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise MineStudioRolloutImportError(f"{label} artifact escapes its rollout directory") from exc
    return path


def read_json(path: Path, label: str) -> dict[str, Any]:
    raw = read_bytes(path, label)
    value = parse_json_bytes(raw, path, label)
    if not isinstance(value, dict):
        raise MineStudioRolloutImportError(f"{label} must be a JSON object")
    return value


def parse_json_bytes(raw: bytes, path: Path, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MineStudioRolloutImportError(f"could not parse {label} {path}: {exc}") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MineStudioRolloutImportError(f"JSON object repeats key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise MineStudioRolloutImportError(f"JSON contains non-finite constant {value}")


def read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MineStudioRolloutImportError(f"could not read {label} {path}: {exc}") from exc


def exact_mapping(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise MineStudioRolloutImportError(f"{label} has an unstable field set")
    return value


def canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MineStudioRolloutImportError(f"rollout manifest is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MineStudioRolloutImportError(f"{label} must be a non-empty string")
    return value


def finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise MineStudioRolloutImportError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise MineStudioRolloutImportError(f"{label} must be finite")
    return 0.0 if result == 0 else result


def finite_nonzero(value: Any, label: str) -> float:
    result = finite(value, label)
    if result == 0:
        raise MineStudioRolloutImportError(f"{label} must be nonzero")
    return result


def require_sha256(value: Any, label: str) -> None:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise MineStudioRolloutImportError(f"{label} must be 64 lowercase hex characters")
