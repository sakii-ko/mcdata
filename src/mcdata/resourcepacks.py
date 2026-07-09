from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, Mapping, Sequence
import zipfile

from .resourcepack_format import (
    ResourcePackError,
    ResourcePackFormat,
    discover_target_resource_format as discover_target_resource_format,
)


NORMALIZER_VERSION = "1"
RESOLUTION_SCHEMA_VERSION = 1
SOURCE_DIRECTORY = Path(".mcdata/resourcepack_sources")
RESOLUTION_SIDECAR = Path(".mcdata/resourcepack-resolution.json")
_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_ARCHIVE_MEMBERS = 250_000
_MAX_MEMBER_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 32 * 1024 * 1024 * 1024
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_COMPRESSION_RATIO = 10_000


@dataclass(frozen=True)
class ResourcePackSource:
    filename: str
    path: Path
    project: str
    version: str
    game_versions: tuple[str, ...]
    expected_sha512: str | None = None
    download_url: str | None = None
    expected_size: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResourcePackSource:
        required = {"filename", "path", "project", "version", "game_versions"}
        missing = sorted(required.difference(value))
        if missing:
            raise ResourcePackError(
                "Resource-pack source receipt is missing fields: " + ", ".join(missing)
            )
        game_versions = value["game_versions"]
        if isinstance(game_versions, (str, bytes)) or not isinstance(game_versions, Sequence):
            raise ResourcePackError("Resource-pack source game_versions must be a sequence")
        if any(not isinstance(item, str) or not item for item in game_versions):
            raise ResourcePackError("Resource-pack source game_versions must contain strings")
        return cls(
            filename=str(value["filename"]),
            path=Path(value["path"]),
            project=str(value["project"]),
            version=str(value["version"]),
            game_versions=tuple(game_versions),
            expected_sha512=_optional_string(value.get("expected_sha512")),
            download_url=_optional_string(value.get("download_url")),
            expected_size=(
                value["expected_size"]
                if _is_nonnegative_int(value.get("expected_size"))
                else None
            ),
        )


def resourcepack_source_dir(work_dir: Path) -> Path:
    """Return the persistent, non-effective download cache for an instance."""

    return work_dir / SOURCE_DIRECTORY


def materialize_resourcepacks(
    work_dir: Path,
    *,
    sources: Sequence[ResourcePackSource | Mapping[str, Any]],
    target: ResourcePackFormat,
    game_version: str,
) -> tuple[list[str], dict[str, Any]]:
    """Materialize exactly the selected resource packs and write their provenance sidecar.

    Sources must live in ``work_dir/.mcdata/resourcepack_sources`` and carry the Modrinth
    project/version compatibility receipt.  Compatible archives are copied byte-for-byte.
    Archives with valid but obsolete format metadata are deterministically rewritten.
    """

    source_dir = resourcepack_source_dir(work_dir)
    metadata_dir = work_dir / ".mcdata"
    effective_dir = work_dir / "resourcepacks"
    sidecar_path = work_dir / RESOLUTION_SIDECAR
    source_dir.mkdir(parents=True, exist_ok=True)
    metadata_dir.mkdir(parents=True, exist_ok=True)

    normalized_sources = [
        source
        if isinstance(source, ResourcePackSource)
        else ResourcePackSource.from_mapping(source)
        for source in sources
    ]
    prepared = [
        _prepare_source(source, source_dir=source_dir, game_version=game_version)
        for source in normalized_sources
    ]
    filenames = [source.filename for source in prepared]
    if len(filenames) != len(set(filenames)):
        raise ResourcePackError("Selected resource-pack filenames must be unique")

    stage_root = Path(tempfile.mkdtemp(prefix="resourcepacks-stage-", dir=metadata_dir))
    stage_packs = stage_root / "resourcepacks"
    stage_packs.mkdir()
    try:
        pack_records = _materialize_pack_records(
            prepared,
            target=target.version,
            stage_dir=stage_packs,
            effective_dir=effective_dir,
        )
        resolution: dict[str, Any] = {
            "schema_version": RESOLUTION_SCHEMA_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "game_version": game_version,
            "target": _target_record(target),
            "packs": pack_records,
        }
        backup = _swap_effective_directory(
            stage_packs,
            effective_dir,
            backup_parent=metadata_dir,
        )
        try:
            _atomic_write_json(sidecar_path, resolution)
        except BaseException:
            _rollback_effective_directory(effective_dir, backup)
            raise
        else:
            _discard_effective_backup(backup)
        return filenames, resolution
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _target_record(target: ResourcePackFormat) -> dict[str, Any]:
    source_jar = target.source_jar.resolve(strict=True)
    return {
        "resource_major": target.resource_major,
        "resource_minor": target.resource_minor,
        "source_jar": str(source_jar),
        "source_jar_sha256": _sha256(source_jar),
    }


def _materialize_pack_records(
    sources: Sequence[ResourcePackSource],
    *,
    target: tuple[int, int],
    stage_dir: Path,
    effective_dir: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source in sources:
        source_sha256 = _sha256(source.path)
        source_sha512 = _sha512(source.path)
        if source.expected_sha512 is not None and source_sha512 != source.expected_sha512:
            raise ResourcePackError(
                f"Resource-pack source SHA-512 does not match upstream receipt: {source.path}"
            )
        if source.expected_size is not None and source.path.stat().st_size != source.expected_size:
            raise ResourcePackError(
                f"Resource-pack source size does not match upstream receipt: {source.path}"
            )
        before = _inspect_resource_pack(source.path)
        compatible = _metadata_supports(before, target)
        effective_stage = stage_dir / source.filename
        if compatible:
            shutil.copyfile(source.path, effective_stage)
            after = before
        else:
            after = _normalized_metadata(before, target)
            _write_normalized_zip(source.path, effective_stage, metadata=after)
        if _sha256(source.path) != source_sha256:
            raise ResourcePackError(f"Resource-pack source changed while reading: {source.path}")
        records.append(
            _pack_record(
                source,
                source_sha256=source_sha256,
                source_sha512=source_sha512,
                effective_path=effective_dir / source.filename,
                effective_stage=effective_stage,
                normalized=not compatible,
                before=before,
                after=after,
            )
        )
    return records


def _pack_record(
    source: ResourcePackSource,
    *,
    source_sha256: str,
    source_sha512: str,
    effective_path: Path,
    effective_stage: Path,
    normalized: bool,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    return {
        "filename": source.filename,
        "project": source.project,
        "version": source.version,
        "game_versions": list(source.game_versions),
        "source_path": str(source.path),
        "effective_path": str(effective_path.resolve()),
        "source_sha256": source_sha256,
        "source_sha512": source_sha512,
        "upstream_sha512": source.expected_sha512,
        "download_url": source.download_url,
        "expected_size": source.expected_size,
        "effective_sha256": _sha256(effective_stage),
        "normalized": normalized,
        "before": before,
        "after": after,
    }


def _prepare_source(
    source: ResourcePackSource,
    *,
    source_dir: Path,
    game_version: str,
) -> ResourcePackSource:
    if not source.filename or Path(source.filename).name != source.filename:
        raise ResourcePackError(f"Unsafe resource-pack filename: {source.filename!r}")
    if (
        "/" in source.filename
        or "\\" in source.filename
        or "," in source.filename
        or not source.filename.endswith(".zip")
    ):
        raise ResourcePackError(f"Unsafe resource-pack filename: {source.filename!r}")
    if not source.project or not source.version:
        raise ResourcePackError("Resource-pack source project and version must be non-empty")
    if game_version not in source.game_versions:
        raise ResourcePackError(
            f"Resource pack {source.filename!r} does not declare game version {game_version!r}"
        )

    path = source.path.expanduser()
    if not path.is_absolute():
        path = source_dir / path
    try:
        resolved_path = path.resolve(strict=True)
        resolved_source_dir = source_dir.resolve(strict=True)
    except OSError as exc:
        raise ResourcePackError(f"Resource-pack source is unavailable: {path}") from exc
    if resolved_path.parent != resolved_source_dir or resolved_path.name != source.filename:
        raise ResourcePackError(
            f"Resource-pack source must be {source_dir / source.filename}, got {resolved_path}"
        )
    if not resolved_path.is_file():
        raise ResourcePackError(f"Resource-pack source is not a file: {resolved_path}")
    return ResourcePackSource(
        filename=source.filename,
        path=resolved_path,
        project=source.project,
        version=source.version,
        game_versions=source.game_versions,
        expected_sha512=source.expected_sha512,
        download_url=source.download_url,
        expected_size=source.expected_size,
    )


def _inspect_resource_pack(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive_members(archive)
            document = _decode_json(archive.read("pack.mcmeta"), source=f"{path}!/pack.mcmeta")
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if isinstance(exc, ResourcePackError):
            raise
        raise ResourcePackError(f"Cannot read resource pack {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ResourcePackError(f"Resource-pack metadata must be an object: {path}")
    if not isinstance(document.get("pack"), dict):
        raise ResourcePackError(f"Resource-pack metadata must contain a pack object: {path}")
    return document


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    names: dict[str, bool] = {}
    root_metadata_count = 0
    infos = archive.infolist()
    if len(infos) > _MAX_ARCHIVE_MEMBERS:
        raise ResourcePackError(
            f"Resource-pack ZIP has too many members: {len(infos)} > {_MAX_ARCHIVE_MEMBERS}"
        )
    total_uncompressed = 0
    for info in infos:
        name = info.filename
        if name in names:
            raise ResourcePackError(f"Resource-pack ZIP has duplicate member: {name!r}")
        _validate_member_path(name)
        if info.flag_bits & 0x1:
            raise ResourcePackError(f"Encrypted resource-pack member is unsupported: {name!r}")
        unix_type = (info.external_attr >> 16) & 0o170000
        if unix_type == stat.S_IFLNK:
            raise ResourcePackError(f"Resource-pack ZIP contains a symlink: {name!r}")
        if info.is_dir() and info.file_size:
            raise ResourcePackError(f"Resource-pack ZIP directory contains data: {name!r}")
        if info.file_size > _MAX_MEMBER_UNCOMPRESSED_BYTES:
            raise ResourcePackError(f"Resource-pack ZIP member is too large: {name!r}")
        total_uncompressed += info.file_size
        if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ResourcePackError("Resource-pack ZIP exceeds total uncompressed size limit")
        if (
            not info.is_dir()
            and info.file_size
            and (
                info.compress_size == 0
                or info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO
            )
        ):
            raise ResourcePackError(f"Resource-pack ZIP member has unsafe compression ratio: {name!r}")
        if name == "pack.mcmeta" and info.file_size > _MAX_METADATA_BYTES:
            raise ResourcePackError("Resource-pack pack.mcmeta exceeds size limit")
        names[name] = info.is_dir()
        if name == "pack.mcmeta":
            root_metadata_count += 1

    if root_metadata_count != 1:
        raise ResourcePackError("Resource-pack ZIP must contain exactly one root pack.mcmeta")

    for name in names:
        path = PurePosixPath(name.rstrip("/"))
        for parent in path.parents:
            parent_name = parent.as_posix()
            if parent_name == ".":
                break
            if parent_name in names and not names[parent_name]:
                raise ResourcePackError(
                    f"Resource-pack ZIP path traverses a file member: {parent_name!r}"
                )


def _validate_member_path(name: str) -> None:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise ResourcePackError(f"Unsafe resource-pack ZIP member path: {name!r}")
    body = name[:-1] if name.endswith("/") else name
    if not body or re.match(r"^[A-Za-z]:", body):
        raise ResourcePackError(f"Unsafe resource-pack ZIP member path: {name!r}")
    parts = body.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ResourcePackError(f"Unsafe resource-pack ZIP member path: {name!r}")


def _metadata_supports(document: dict[str, Any], target: tuple[int, int]) -> bool:
    pack = document["pack"]
    try:
        minimum = _format_version(pack["min_format"])
        maximum = _format_version(pack["max_format"])
        pack_format = pack["pack_format"]
    except (KeyError, ResourcePackError):
        return False
    if minimum > maximum or not minimum <= target <= maximum:
        return False
    if not _is_nonnegative_int(pack_format):
        return False
    return minimum <= (pack_format, 0) <= maximum


def _format_version(value: Any) -> tuple[int, int]:
    if _is_integral_number(value):
        major = int(value)
        if major < 0:
            raise ResourcePackError(f"Invalid resource format: {value!r}")
        return major, 0
    if not isinstance(value, list) or len(value) not in {1, 2}:
        raise ResourcePackError(f"Invalid resource format: {value!r}")
    if any(not _is_nonnegative_int(part) for part in value):
        raise ResourcePackError(f"Invalid resource format: {value!r}")
    return value[0], value[1] if len(value) == 2 else 0


def _normalized_metadata(
    document: dict[str, Any],
    target: tuple[int, int],
) -> dict[str, Any]:
    normalized = copy.deepcopy(document)
    pack = normalized["pack"]
    pack["pack_format"] = target[0]
    pack["min_format"] = [target[0], target[1]]
    pack["max_format"] = [target[0], target[1]]
    pack.pop("supported_formats", None)
    return normalized


def _write_normalized_zip(source: Path, destination: Path, *, metadata: dict[str, Any]) -> None:
    canonical_metadata = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    try:
        with (
            zipfile.ZipFile(source) as input_archive,
            zipfile.ZipFile(
                destination,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
                strict_timestamps=True,
            ) as output_archive,
        ):
            infos = sorted(input_archive.infolist(), key=lambda info: info.filename)
            for input_info in infos:
                output_info = _canonical_zip_info(input_info.filename, is_dir=input_info.is_dir())
                if input_info.filename == "pack.mcmeta":
                    output_archive.writestr(output_info, canonical_metadata, compresslevel=9)
                elif input_info.is_dir():
                    output_archive.writestr(output_info, b"")
                else:
                    with (
                        input_archive.open(input_info) as input_file,
                        output_archive.open(output_info, "w", force_zip64=True) as output_file,
                    ):
                        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ResourcePackError(f"Cannot normalize resource pack {source}: {exc}") from exc


def _canonical_zip_info(filename: str, *, is_dir: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename, date_time=_FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED if is_dir else zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.external_attr = ((stat.S_IFDIR | 0o755) if is_dir else (stat.S_IFREG | 0o644)) << 16
    if is_dir:
        info.external_attr |= 0x10
    return info


def _swap_effective_directory(
    stage: Path, destination: Path, *, backup_parent: Path
) -> Path | None:
    backup = Path(tempfile.mkdtemp(prefix="resourcepacks-backup-", dir=backup_parent))
    backup.rmdir()
    had_destination = destination.exists()
    if had_destination:
        os.replace(destination, backup)
    try:
        os.replace(stage, destination)
    except BaseException:
        if had_destination and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    return backup if had_destination else None


def _rollback_effective_directory(destination: Path, backup: Path | None) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    if backup is not None and backup.exists():
        os.replace(backup, destination)


def _discard_effective_backup(backup: Path | None) -> None:
    if backup is not None and backup.exists():
        shutil.rmtree(backup)


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


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_integral_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value == int(value)


def _is_nonnegative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
