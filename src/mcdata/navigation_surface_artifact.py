from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from mcdata.config import ConfigError, load_yaml
from mcdata.navigation_surface import (
    DERIVATION_POLICY,
    FULL_BLOCK_SUPPORT_ALLOWLIST,
    NavigationSurface,
    NavigationSurfaceError,
    derive_scene_navigation_surface,
    edge_primitive_counts,
    navigation_surface_document,
)
from mcdata.scene_model import SceneSpec, parse_scene


def load_navigation_surface_artifact(
    path: Path,
    *,
    repository_root: Path | None = None,
    expected_terrain_instance_id: str | None = None,
) -> NavigationSurface:
    """Load a derivation artifact and reproduce every canonical node fail closed."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NavigationSurfaceError(
            f"Cannot read navigation surface artifact {path}: {exc}"
        ) from exc
    _validate_json_schema(
        document,
        schema_filename="navigation_surface.schema.json",
        label="Navigation surface artifact",
    )
    root = repository_root if repository_root is not None else path.resolve().parents[2]
    terrain_instance_id = document["terrain_instance_id"]
    if (
        expected_terrain_instance_id is not None
        and terrain_instance_id != expected_terrain_instance_id
    ):
        raise NavigationSurfaceError(
            "Navigation surface terrain instance mismatch: "
            f"declared {terrain_instance_id!r}, expected {expected_terrain_instance_id!r}"
        )

    derivation = document["derivation"]
    if derivation["policy"] != DERIVATION_POLICY:
        raise NavigationSurfaceError(
            f"Unsupported navigation surface derivation policy: {derivation['policy']!r}"
        )
    if tuple(derivation["support_allowlist"]) != tuple(sorted(FULL_BLOCK_SUPPORT_ALLOWLIST)):
        raise NavigationSurfaceError("Navigation surface support allowlist is not canonical")

    scene = _load_bound_scene(root, derivation["scene"])

    bounds = derivation["probe_bounds"]
    feet_y = derivation["feet_y"]
    if not bounds["y"][0] <= feet_y - 1 or not feet_y + 1 <= bounds["y"][1]:
        raise NavigationSurfaceError("Navigation surface feet/support/head exceed probe y bounds")
    surface = derive_scene_navigation_surface(
        scene,
        surface_id=document["surface_id"],
        terrain_instance_id=terrain_instance_id,
        feet_y=feet_y,
        x_bounds=tuple(bounds["x"]),
        z_bounds=tuple(bounds["z"]),
        support_allowlist=derivation["support_allowlist"],
    )
    _validate_json_schema(
        navigation_surface_document(surface),
        schema_filename="canonical_navigation_surface.schema.json",
        label="Canonical navigation surface",
    )
    _validate_expected_summary(document["expected"], surface)
    return surface


def _load_bound_scene(repository_root: Path, scene_record: Mapping[str, Any]) -> SceneSpec:
    scene_path = _project_file(repository_root, scene_record["path"])
    actual_scene_sha = hashlib.sha256(scene_path.read_bytes()).hexdigest()
    if actual_scene_sha != scene_record["sha256"]:
        raise NavigationSurfaceError(
            f"Navigation surface scene SHA-256 mismatch: declared {scene_record['sha256']}, "
            f"computed {actual_scene_sha}"
        )
    try:
        scene_mapping = load_yaml(scene_path).get("scene")
    except ConfigError as exc:
        raise NavigationSurfaceError(str(exc)) from exc
    if not isinstance(scene_mapping, dict):
        raise NavigationSurfaceError(f"Scene file {scene_record['path']!r} has no scene mapping")
    try:
        scene = parse_scene(scene_mapping)
    except (KeyError, TypeError, ValueError) as exc:
        raise NavigationSurfaceError(
            f"Navigation surface scene cannot be parsed: {scene_record['path']!r}: {exc}"
        ) from exc
    if list(scene.origin) != scene_record["origin"]:
        raise NavigationSurfaceError(
            f"Navigation surface scene origin mismatch: declared {scene_record['origin']}, "
            f"configured {list(scene.origin)}"
        )
    return scene


def _validate_expected_summary(expected: Mapping[str, Any], surface: NavigationSurface) -> None:
    actual = {
        "node_count": len(surface.nodes),
        "traversable_node_count": sum(node.traversable for node in surface.nodes),
        "edge_primitive_counts": edge_primitive_counts(surface),
        "surface_sha256": surface.surface_sha256,
    }
    if dict(expected) != actual:
        raise NavigationSurfaceError(
            f"Navigation surface derived summary mismatch: declared {dict(expected)}, computed {actual}"
        )


def _validate_json_schema(document: Any, *, schema_filename: str, label: str) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - required project dependency
        raise NavigationSurfaceError("Navigation surface validation requires jsonschema") from exc
    schema_path = Path(__file__).parent / "schemas" / schema_filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise NavigationSurfaceError(f"{label} violates schema at {location}: {error.message}")


def _project_file(repository_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise NavigationSurfaceError(
            f"Navigation surface path must be project-relative without '..': {relative_path!r}"
        )
    root = repository_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise NavigationSurfaceError(
            f"Navigation surface source file is missing: {relative_path!r}"
        )
    return resolved
