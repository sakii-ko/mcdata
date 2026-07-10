from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from rich.console import Console

from .modrinth import ProjectVersion, VersionFile, latest_project_version
from .net import download_file

console = Console()

RESOURCEPACK_SOURCE_MANIFEST = "resourcepack-sources.json"


@dataclass(frozen=True)
class _ResourcePackSelection:
    asset_key: str
    project: str
    version: ProjectVersion
    file: VersionFile
    selection_pattern: str | None


def install_mods(work_dir: Path, *, game_version: str, slugs: list[str]) -> list[str]:
    mods_dir = work_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    for slug in slugs:
        version = latest_project_version(
            slug,
            game_version=game_version,
            loaders=["fabric"],
            version_types=_allowed_mod_version_types(slug),
        )
        file = version.primary_file
        dest = mods_dir / file.filename
        _remove_stale_mods(mods_dir, slug=slug, keep=dest.name)
        if not dest.exists():
            console.print(f"Downloading mod {slug} {version.version_number} -> {dest.name}")
            download_file(file.url, dest)
        installed.append(dest.name)
    return installed


def _allowed_mod_version_types(slug: str) -> list[str]:
    if slug == "sodium":
        return ["release"]
    return ["release", "beta"]


def install_asset_set(
    work_dir: Path,
    *,
    game_version: str,
    asset_config: dict[str, Any],
    asset_set_name: str,
) -> tuple[list[str], str | None]:
    sets = asset_config.get("asset_sets", {})
    if asset_set_name not in sets:
        known = ", ".join(sorted(sets))
        raise RuntimeError(f"Unknown asset set '{asset_set_name}'. Known sets: {known}")

    resource_defs = asset_config.get("assets", {}).get("resourcepacks", {})
    shader_defs = asset_config.get("assets", {}).get("shaderpacks", {})
    selected = sets[asset_set_name]

    resourcepack_dir = work_dir / "resourcepacks"
    resourcepack_source_dir = work_dir / ".mcdata" / "resourcepack_sources"
    shaderpack_dir = work_dir / "shaderpacks"
    resourcepack_dir.mkdir(parents=True, exist_ok=True)
    resourcepack_source_dir.mkdir(parents=True, exist_ok=True)
    shaderpack_dir.mkdir(parents=True, exist_ok=True)
    resource_keys = _resourcepack_keys(selected, asset_set_name=asset_set_name)
    selections = _resolve_resourcepack_selections(
        resource_keys,
        resource_defs=resource_defs,
        game_version=game_version,
    )
    resourcepacks, resourcepack_sources = _install_resourcepack_selections(
        selections,
        source_dir=resourcepack_source_dir,
        legacy_dir=resourcepack_dir,
        game_version=game_version,
    )

    _write_resourcepack_source_manifest(
        work_dir,
        game_version=game_version,
        resourcepacks=resourcepack_sources,
    )

    shaderpack_name = _install_shaderpack(
        selected.get("shaderpack"),
        shader_defs=shader_defs,
        shaderpack_dir=shaderpack_dir,
        game_version=game_version,
    )
    return resourcepacks, shaderpack_name


def _resourcepack_keys(selected: Any, *, asset_set_name: str) -> list[str]:
    if not isinstance(selected, dict):
        raise RuntimeError(f"Asset set {asset_set_name!r} must be an object")
    value = selected.get("resourcepacks", []) or []
    if not isinstance(value, list) or any(not isinstance(key, str) or not key for key in value):
        raise RuntimeError(f"Asset set {asset_set_name!r} resourcepacks must be a list of names")
    return value


def _resolve_resourcepack_selections(
    asset_keys: list[str],
    *,
    resource_defs: Any,
    game_version: str,
) -> list[_ResourcePackSelection]:
    if not isinstance(resource_defs, dict):
        raise RuntimeError("Resource-pack asset definitions must be an object")
    selections: list[_ResourcePackSelection] = []
    for asset_key in asset_keys:
        spec = resource_defs.get(asset_key)
        if not isinstance(spec, dict):
            raise RuntimeError(f"Unknown or invalid resource-pack asset {asset_key!r}")
        project = spec.get("slug")
        if not isinstance(project, str) or not project:
            raise RuntimeError(f"Resource-pack asset {asset_key!r} has no Modrinth slug")
        version = latest_project_version(project, game_version=game_version)
        selections.extend(
            _select_resourcepack_files(
                asset_key,
                project=project,
                spec=spec,
                version=version,
                game_version=game_version,
            )
        )
    _validate_unique_resourcepack_selections(selections)
    return selections


def _select_resourcepack_files(
    asset_key: str,
    *,
    project: str,
    spec: dict[str, Any],
    version: ProjectVersion,
    game_version: str,
) -> list[_ResourcePackSelection]:
    if game_version not in version.game_versions:
        raise RuntimeError(
            f"Resource pack {asset_key} version {version.version_number} does not declare "
            f"target Minecraft version {game_version}"
        )
    patterns = _resourcepack_file_patterns(spec, asset_key=asset_key)
    if patterns is None:
        selected = [(version.primary_file, None)]
    else:
        selected = [
            (_match_resourcepack_file(version, pattern, asset_key=asset_key), pattern)
            for pattern in patterns
        ]
    selections = [
        _ResourcePackSelection(asset_key, project, version, file, pattern)
        for file, pattern in selected
    ]
    for selection in selections:
        _validate_resourcepack_selection(selection)
    _validate_unique_resourcepack_selections(selections)
    return selections


def _resourcepack_file_patterns(spec: dict[str, Any], *, asset_key: str) -> tuple[str, ...] | None:
    if "file_patterns" not in spec:
        return None
    value = spec["file_patterns"]
    if not isinstance(value, list) or not value:
        raise RuntimeError(
            f"Resource-pack asset {asset_key!r} file_patterns must be a non-empty list"
        )
    patterns: list[str] = []
    for index, pattern in enumerate(value):
        if not isinstance(pattern, str):
            raise RuntimeError(
                f"Resource-pack asset {asset_key!r} file_patterns[{index}] must be a string"
            )
        patterns.append(_validate_resourcepack_file_pattern(pattern, asset_key=asset_key))
    return tuple(patterns)


def _validate_resourcepack_file_pattern(pattern: str, *, asset_key: str) -> str:
    if (
        not pattern
        or Path(pattern).name != pattern
        or "/" in pattern
        or "\\" in pattern
        or "," in pattern
        or '"' in pattern
        or "'" in pattern
        or any(ord(character) < 32 or ord(character) == 127 for character in pattern)
        or not pattern.endswith(".zip")
    ):
        raise RuntimeError(
            f"Unsafe Modrinth file pattern for resource-pack asset {asset_key!r}: {pattern!r}"
        )
    return pattern


def _match_resourcepack_file(
    version: ProjectVersion, pattern: str, *, asset_key: str
) -> VersionFile:
    exact = [file for file in version.files if file.filename == pattern]
    matches = exact or [file for file in version.files if fnmatchcase(file.filename, pattern)]
    if len(matches) != 1:
        candidates = [file.filename for file in version.files]
        detail = "no files" if not matches else f"{len(matches)} files"
        raise RuntimeError(
            f"Resource-pack asset {asset_key!r} pattern {pattern!r} matched {detail} in "
            f"Modrinth version {version.version_number}; candidates={candidates!r}"
        )
    return matches[0]


def _validate_resourcepack_selection(selection: _ResourcePackSelection) -> None:
    _validate_resourcepack_filename(selection.file.filename)
    if not selection.file.sha512:
        raise RuntimeError(
            f"Modrinth did not provide SHA-512 for resource pack {selection.asset_key} "
            f"{selection.version.version_number} file {selection.file.filename!r}"
        )


def _validate_unique_resourcepack_selections(
    selections: list[_ResourcePackSelection],
) -> None:
    seen: dict[str, str] = {}
    for selection in selections:
        filename = selection.file.filename
        if filename in seen:
            raise RuntimeError(
                f"Duplicate selected resource-pack filename {filename!r}: "
                f"assets {seen[filename]!r} and {selection.asset_key!r}"
            )
        seen[filename] = selection.asset_key


def _install_resourcepack_selections(
    selections: list[_ResourcePackSelection],
    *,
    source_dir: Path,
    legacy_dir: Path,
    game_version: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    filenames: list[str] = []
    records: list[dict[str, Any]] = []
    for selection in selections:
        dest = _install_resourcepack_source(
            source_dir=source_dir,
            legacy_dir=legacy_dir,
            asset_key=selection.asset_key,
            version=selection.version,
            file=selection.file,
        )
        filenames.append(dest.name)
        records.append(_resourcepack_source_record(selection, dest, game_version=game_version))
    return filenames, records


def _resourcepack_source_record(
    selection: _ResourcePackSelection,
    path: Path,
    *,
    game_version: str,
) -> dict[str, Any]:
    record = {
        "asset_key": selection.asset_key,
        "project": selection.project,
        "version": selection.version.version_number,
        "filename": path.name,
        "path": str(path),
        "game_versions": selection.version.game_versions,
        "target_game_version": game_version,
        "download_url": selection.file.url,
        "expected_sha512": selection.file.sha512,
        "expected_size": selection.file.size,
    }
    if selection.selection_pattern is not None:
        record["selection_pattern"] = selection.selection_pattern
    return record


def _install_shaderpack(
    shader_key: Any,
    *,
    shader_defs: Any,
    shaderpack_dir: Path,
    game_version: str,
) -> str | None:
    if not shader_key:
        return None
    if not isinstance(shader_key, str) or not isinstance(shader_defs, dict):
        raise RuntimeError(f"Invalid shader-pack selection: {shader_key!r}")
    spec = shader_defs.get(shader_key)
    if not isinstance(spec, dict) or not isinstance(spec.get("slug"), str):
        raise RuntimeError(f"Unknown or invalid shader-pack asset {shader_key!r}")
    version = latest_project_version(spec["slug"], game_version=game_version)
    file = version.primary_file
    dest = shaderpack_dir / file.filename
    if not dest.exists():
        console.print(
            f"Downloading shader pack {shader_key} {version.version_number} -> {dest.name}"
        )
        download_file(file.url, dest)
    return dest.name


def _install_resourcepack_source(
    *,
    source_dir: Path,
    legacy_dir: Path,
    asset_key: str,
    version: ProjectVersion,
    file: VersionFile,
) -> Path:
    filename = _validate_resourcepack_filename(file.filename)
    if not file.sha512:
        raise RuntimeError(
            f"Modrinth did not provide SHA-512 for resource pack {asset_key} "
            f"{version.version_number}"
        )
    dest = _safe_child(source_dir, filename)
    if dest.is_symlink():
        raise RuntimeError(f"Resource-pack source cache cannot be a symlink: {dest}")
    if dest.exists() and _sha512(dest) != file.sha512:
        dest.unlink()
    if not dest.exists():
        legacy = _safe_child(legacy_dir, filename)
        if legacy.exists() and not legacy.is_symlink() and _sha512(legacy) == file.sha512:
            console.print(f"Caching verified upstream resource pack {asset_key} -> {dest.name}")
            _copy_file_atomic(legacy, dest)
        else:
            console.print(
                f"Downloading resource pack {asset_key} {version.version_number} -> {dest.name}"
            )
            download_file(file.url, dest)
    actual_sha512 = _sha512(dest)
    if actual_sha512 != file.sha512:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-512 mismatch for resource pack {asset_key}: "
            f"expected {file.sha512}, got {actual_sha512}"
        )
    if file.size is not None and dest.stat().st_size != file.size:
        actual_size = dest.stat().st_size
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"Size mismatch for resource pack {asset_key}: expected {file.size}, got {actual_size}"
        )
    return dest


def _validate_resourcepack_filename(filename: str) -> str:
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or "," in filename
        or '"' in filename
        or "'" in filename
        or any(ord(character) < 32 or ord(character) == 127 for character in filename)
        or Path(filename).suffix != ".zip"
    ):
        raise RuntimeError(f"Unsafe Modrinth resource-pack filename: {filename!r}")
    return filename


def _safe_child(directory: Path, filename: str) -> Path:
    root = directory.resolve(strict=True)
    child = directory / filename
    if child.parent.resolve(strict=True) != root:
        raise RuntimeError(f"Resource-pack filename escapes cache directory: {filename!r}")
    return child


def _copy_file_atomic(source: Path, destination: Path) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    try:
        shutil.copy2(source, tmp_name)
        os.replace(tmp_name, destination)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_resourcepack_sources(work_dir: Path) -> dict[str, Any]:
    path = work_dir / ".mcdata" / RESOURCEPACK_SOURCE_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing resource-pack source manifest: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read resource-pack source manifest: {path}") from exc
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != 1
        or not isinstance(data.get("target_game_version"), str)
        or not isinstance(data.get("resourcepacks"), list)
    ):
        raise RuntimeError(f"Invalid resource-pack source manifest: {path}")
    return data


def _write_resourcepack_source_manifest(
    work_dir: Path,
    *,
    game_version: str,
    resourcepacks: list[dict[str, Any]],
) -> None:
    _write_json_atomic(
        work_dir / ".mcdata" / RESOURCEPACK_SOURCE_MANIFEST,
        {
            "schema_version": 1,
            "target_game_version": game_version,
            "resourcepacks": resourcepacks,
        },
    )


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _remove_stale_mods(mods_dir: Path, *, slug: str, keep: str) -> None:
    prefixes = {
        "fabric-api": ["fabric-api-"],
        "sodium": ["sodium-fabric-", "sodium-"],
        "iris": ["iris-fabric-", "iris-"],
        "modmenu": ["modmenu-"],
        "advancementdisable": ["advancementdisable-"],
        "no-chat-reports": ["NoChatReports-", "no-chat-reports-"],
        "entity-model-features": ["entity_model_features-", "entity-model-features-"],
        "entitytexturefeatures": ["entity_texture_features-", "entitytexturefeatures-"],
        "continuity": ["continuity-"],
        "polytone": ["polytone-"],
        "libjf": ["libjf-"],
        "respackopts": ["respackopts-"],
        "lambdynamiclights": ["lambdynamiclights-"],
        "euphoria-patches": ["EuphoriaPatcher-", "euphoria-patches-"],
    }.get(slug, [f"{slug}-"])
    for path in mods_dir.glob("*.jar"):
        if path.name == keep:
            continue
        if any(path.name.startswith(prefix) for prefix in prefixes):
            path.unlink()
