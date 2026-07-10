from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from mcdata.config import ConfigError, load_profile, load_yaml
from mcdata.scene_model import parse_scene


class TerrainRegistryError(ValueError):
    """Raised when a terrain registry cannot prove its declared identity."""


def load_terrain_registry(path: Path, *, repository_root: Path | None = None) -> dict[str, Any]:
    """Load and fully validate a terrain registry without mutating external state."""
    try:
        document = load_yaml(path)
    except ConfigError as exc:
        raise TerrainRegistryError(str(exc)) from exc
    root = repository_root if repository_root is not None else path.resolve().parents[1]
    validate_terrain_registry(document, repository_root=root)
    return document


def validate_terrain_registry(
    document: Mapping[str, Any], *, repository_root: Path
) -> None:
    """Fail closed on schema, duplicate identity, provenance, or config drift."""
    registry = deepcopy(dict(document))
    _validate_schema(registry)
    _validate_unique_ids(registry)
    for family in registry["terrain_families"]:
        family_id = family["family_id"]
        for instance in family["instances"]:
            _validate_provenance(instance, repository_root=repository_root)
            _validate_config_bindings(instance, repository_root=repository_root)
            actual_hash = terrain_identity_sha256(family_id, instance)
            if instance["identity_sha256"] != actual_hash:
                raise TerrainRegistryError(
                    f"Terrain instance {instance['instance_id']!r} identity SHA-256 mismatch: "
                    f"declared {instance['identity_sha256']}, computed {actual_hash}"
                )


def accepted_terrain_instances(document: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return detached accepted records with their family ID made explicit."""
    accepted: list[dict[str, Any]] = []
    for family in document.get("terrain_families", []):
        if family.get("status") != "accepted":
            continue
        for instance in family.get("instances", []):
            if instance.get("status") == "accepted":
                item = deepcopy(instance)
                item["family_id"] = family["family_id"]
                accepted.append(item)
    return tuple(accepted)


def terrain_identity_sha256(family_id: str, instance: Mapping[str, Any]) -> str:
    """Hash all terrain identity fields using stable compact canonical JSON."""
    payload = terrain_identity_payload(family_id, instance)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def terrain_identity_payload(
    family_id: str, instance: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the exact identity payload; lifecycle status and stored hash are excluded."""
    required = (
        "instance_id",
        "minecraft",
        "world",
        "provenance",
        "spawn",
        "probe_bounds",
        "capabilities",
        "config_bindings",
    )
    missing = [key for key in required if key not in instance]
    if missing:
        raise TerrainRegistryError(
            f"Terrain identity payload is missing required fields: {', '.join(missing)}"
        )
    return {
        "family_id": family_id,
        **{key: deepcopy(instance[key]) for key in required},
    }


def _validate_schema(document: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:  # pragma: no cover - required project dependency
        raise TerrainRegistryError("Terrain registry validation requires jsonschema") from exc
    schema_path = Path(__file__).parent / "schemas" / "terrain_registry.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: [str(part) for part in item.absolute_path],
    )
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path) or "root"
    raise TerrainRegistryError(
        f"Terrain registry violates schema at {location}: {error.message}"
    )


def _validate_unique_ids(document: dict[str, Any]) -> None:
    family_ids: set[str] = set()
    instance_ids: set[str] = set()
    for family in document["terrain_families"]:
        family_id = family["family_id"]
        if family_id in family_ids:
            raise TerrainRegistryError(f"Duplicate terrain family ID: {family_id!r}")
        family_ids.add(family_id)
        for instance in family["instances"]:
            instance_id = instance["instance_id"]
            if instance_id in instance_ids:
                raise TerrainRegistryError(f"Duplicate terrain instance ID: {instance_id!r}")
            instance_ids.add(instance_id)
    for candidate in document["blocked_candidates"]:
        family_id = candidate["family_id"]
        if family_id in family_ids:
            raise TerrainRegistryError(f"Duplicate terrain family ID: {family_id!r}")
        family_ids.add(family_id)


def _validate_provenance(instance: dict[str, Any], *, repository_root: Path) -> None:
    provenance = instance["provenance"]
    scene = provenance["scene"]
    scene_path = _project_file(repository_root, scene["path"])
    actual_sha256 = hashlib.sha256(scene_path.read_bytes()).hexdigest()
    if scene["sha256"] != actual_sha256:
        raise TerrainRegistryError(
            f"Scene SHA-256 mismatch for {scene['path']!r}: "
            f"declared {scene['sha256']}, computed {actual_sha256}"
        )
    scene_document = load_yaml(scene_path).get("scene")
    if not isinstance(scene_document, dict):
        raise TerrainRegistryError(f"Scene file {scene['path']!r} has no scene mapping")
    if "origin" not in scene_document:
        raise TerrainRegistryError(f"Scene file {scene['path']!r} must declare an explicit origin")
    actual_origin = list(parse_scene(scene_document).origin)
    if scene["origin"] != actual_origin:
        raise TerrainRegistryError(
            f"Scene origin mismatch for {scene['path']!r}: "
            f"declared {scene['origin']}, configured {actual_origin}"
        )
    if actual_origin[0] != 0 or actual_origin[2] != 0:
        raise TerrainRegistryError(
            "Phase 1 flat planner does not support a non-zero scene x/z origin: "
            f"configured {actual_origin}"
        )


def _validate_config_bindings(instance: dict[str, Any], *, repository_root: Path) -> None:
    config_dir = repository_root / "configs"
    bindings = instance["config_bindings"]
    try:
        profile = load_profile(config_dir, bindings["profile"])
        actions = load_yaml(config_dir / "actions.yml").get("strategies", {})
    except ConfigError as exc:
        raise TerrainRegistryError(str(exc)) from exc
    strategy_name = bindings["action_strategy"]
    if strategy_name not in actions or not isinstance(actions[strategy_name], dict):
        raise TerrainRegistryError(f"Unknown terrain probe action strategy: {strategy_name!r}")
    strategy = actions[strategy_name]
    if strategy.get("type") != "feedback_roam":
        raise TerrainRegistryError("Terrain probe action must be a feedback_roam strategy")

    expected = {
        "minecraft.version": profile.get("game_version"),
        "world.seed": profile.get("world_seed"),
        "world.profile": profile.get("world_profile"),
    }
    actual = {
        "minecraft.version": instance["minecraft"]["version"],
        "world.seed": instance["world"]["seed"],
        "world.profile": instance["world"]["profile"],
    }
    for key, value in expected.items():
        if value is None or actual[key] != value:
            raise TerrainRegistryError(
                f"Terrain {key} does not match bound profile {bindings['profile']!r}: "
                f"declared {actual[key]!r}, configured {value!r}"
            )

    world_state = profile.get("world_state", {})
    configured_scene = world_state.get("scene") if isinstance(world_state, dict) else None
    expected_scene = {
        "enabled": True,
        "origin": instance["provenance"]["scene"]["origin"],
    }
    if configured_scene != expected_scene:
        raise TerrainRegistryError(
            f"Terrain scene does not match bound profile {bindings['profile']!r}: "
            f"required {expected_scene}, configured {configured_scene}"
        )

    player = world_state.get("player", {})
    configured_spawn = {axis: player.get(axis) for axis in ("x", "y", "z")}
    if instance["spawn"] != configured_spawn:
        raise TerrainRegistryError(
            f"Terrain spawn does not match bound profile {bindings['profile']!r}: "
            f"declared {instance['spawn']}, configured {configured_spawn}"
        )
    start = strategy.get("start")
    if start != [instance["spawn"]["x"], instance["spawn"]["z"]]:
        raise TerrainRegistryError(
            f"Terrain spawn x/z does not match probe action start: "
            f"spawn {instance['spawn']}, action start {start}"
        )
    configured_bounds = strategy.get("bounds")
    probe = instance["probe_bounds"]
    declared_bounds = [probe["x"][0], probe["x"][1], probe["z"][0], probe["z"][1]]
    if declared_bounds != configured_bounds:
        raise TerrainRegistryError(
            f"Terrain x/z probe bounds do not match action {strategy_name!r}: "
            f"declared {declared_bounds}, configured {configured_bounds}"
        )
    configured_y = [strategy.get("y_min"), strategy.get("y_max")]
    if probe["y"] != configured_y:
        raise TerrainRegistryError(
            f"Terrain y probe bounds do not match action {strategy_name!r}: "
            f"declared {probe['y']}, configured {configured_y}"
        )


def _project_file(repository_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TerrainRegistryError(
            f"Terrain provenance path must be project-relative without '..': {relative_path!r}"
        )
    root = repository_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise TerrainRegistryError(f"Terrain provenance file is missing: {relative_path!r}")
    return resolved
