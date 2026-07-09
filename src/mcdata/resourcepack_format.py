from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ResourcePackError(RuntimeError):
    """Raised when a resource pack cannot be resolved or materialized safely."""


@dataclass(frozen=True)
class ResourcePackFormat:
    resource_major: int
    resource_minor: int
    source_jar: Path

    def __post_init__(self) -> None:
        if not _is_nonnegative_int(self.resource_major):
            raise ValueError("resource_major must be a non-negative integer")
        if not _is_nonnegative_int(self.resource_minor):
            raise ValueError("resource_minor must be a non-negative integer")

    @property
    def version(self) -> tuple[int, int]:
        return self.resource_major, self.resource_minor


def discover_target_resource_format(main_dir: Path, game_version: str) -> ResourcePackFormat:
    """Read the official resource format embedded in a launcher client/Fabric JAR."""
    versions_dir = main_dir / "versions"
    candidates: list[ResourcePackFormat] = []
    errors: list[str] = []
    for jar_path in sorted(versions_dir.glob("*/*.jar")):
        try:
            version_document = _read_exact_json_member(jar_path, "version.json")
        except (OSError, ResourcePackError, zipfile.BadZipFile) as exc:
            errors.append(f"{jar_path}: {exc}")
            continue
        if version_document.get("id") != game_version:
            continue
        try:
            pack_version = version_document["pack_version"]
            major = pack_version["resource_major"]
            minor = pack_version["resource_minor"]
        except (KeyError, TypeError) as exc:
            errors.append(f"{jar_path}: incomplete pack_version ({exc})")
            continue
        if not _is_nonnegative_int(major) or not _is_nonnegative_int(minor):
            errors.append(f"{jar_path}: invalid resource pack version {major!r}.{minor!r}")
            continue
        candidates.append(ResourcePackFormat(major, minor, jar_path.resolve()))

    if not candidates:
        detail = f" Inspected errors: {'; '.join(errors)}" if errors else ""
        raise ResourcePackError(
            f"No launcher client/Fabric jar contains resource format for {game_version!r}.{detail}"
        )
    versions = {candidate.version for candidate in candidates}
    if len(versions) != 1:
        detail = ", ".join(
            f"{candidate.source_jar}={candidate.resource_major}.{candidate.resource_minor}"
            for candidate in candidates
        )
        raise ResourcePackError(
            f"Launcher jars disagree on the resource format for {game_version!r}: {detail}"
        )
    return min(
        candidates, key=lambda candidate: _jar_preference(candidate.source_jar, game_version)
    )


def _read_exact_json_member(path: Path, member: str) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        matches = [info for info in archive.infolist() if info.filename == member]
        if len(matches) != 1:
            raise ResourcePackError(f"ZIP must contain exactly one root {member}")
        document = _decode_json(archive.read(matches[0]), source=f"{path}!/{member}")
    if not isinstance(document, dict):
        raise ResourcePackError(f"JSON document must be an object: {path}!/{member}")
    return document


def _decode_json(data: bytes, *, source: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number {value}")

    def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            data,
            parse_constant=reject_constant,
            object_pairs_hook=object_without_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ResourcePackError(f"Invalid JSON in {source}: {exc}") from exc


def _jar_preference(path: Path, game_version: str) -> tuple[int, str]:
    exact = path.parent.name == game_version and path.stem == game_version
    return (0 if exact else 1, str(path))


def _is_nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0
