from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from .modrinth import latest_project_version
from .net import download_file

console = Console()


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

    resourcepacks: list[str] = []
    resourcepack_dir = work_dir / "resourcepacks"
    shaderpack_dir = work_dir / "shaderpacks"
    resourcepack_dir.mkdir(parents=True, exist_ok=True)
    shaderpack_dir.mkdir(parents=True, exist_ok=True)

    for key in selected.get("resourcepacks", []) or []:
        spec = resource_defs[key]
        version = latest_project_version(spec["slug"], game_version=game_version)
        file = version.primary_file
        dest = resourcepack_dir / file.filename
        if not dest.exists():
            console.print(f"Downloading resource pack {key} {version.version_number} -> {dest.name}")
            download_file(file.url, dest)
        resourcepacks.append(dest.name)

    shaderpack_name: str | None = None
    shader_key = selected.get("shaderpack")
    if shader_key:
        spec = shader_defs[shader_key]
        version = latest_project_version(spec["slug"], game_version=game_version)
        file = version.primary_file
        dest = shaderpack_dir / file.filename
        if not dest.exists():
            console.print(f"Downloading shader pack {shader_key} {version.version_number} -> {dest.name}")
            download_file(file.url, dest)
        shaderpack_name = dest.name

    return resourcepacks, shaderpack_name


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
        "lambdynamiclights": ["lambdynamiclights-"],
        "euphoria-patches": ["EuphoriaPatcher-", "euphoria-patches-"],
    }.get(slug, [f"{slug}-"])
    for path in mods_dir.glob("*.jar"):
        if path.name == keep:
            continue
        if any(path.name.startswith(prefix) for prefix in prefixes):
            path.unlink()
