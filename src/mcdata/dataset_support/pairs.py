from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from mcdata.dataset_support.core import (
    DatasetValidationError,
    artifact,
    load_json,
    require_hash,
    require_mapping,
    require_nonempty_string,
    value_sha256,
)

EDIT_AXES = {
    "material_style",
    "shader_quality",
    "time_of_day",
    "weather",
    "snow_weather",
}


def _validate_manifest_schema(document: dict[str, Any]) -> None:
    try:
        from jsonschema import Draft202012Validator  # optional until dataset packaging runs
    except ImportError as exc:
        raise DatasetValidationError(
            "dataset-index requires jsonschema; install the mcdata package dependencies"
        ) from exc
    schema_path = Path(__file__).parents[1] / "schemas" / "edit_pair_manifest.schema.json"
    schema = load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document), key=lambda item: list(item.path)
    )
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "root"
        raise DatasetValidationError(
            f"Edit-pair manifest violates its schema at {location}: {error.message}"
        )


def _resource_view(episode: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = episode.get(key)
    if not isinstance(values, list):
        raise DatasetValidationError(f"Episode {episode.get('episode_id')!r} has no {key} list")
    result = []
    for value in values:
        item = require_mapping(value, f"episode {key} entry")
        result.append(
            {
                "filename": require_nonempty_string(item.get("filename"), f"{key} filename"),
                "sha256": require_hash(item.get("sha256"), 64, f"{key} sha256"),
            }
        )
    return result


def _trajectory_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    trajectory = require_mapping(manifest.get("trajectory"), "manifest.trajectory")
    return {
        key: trajectory.get(key)
        for key in (
            "sha256",
            "strategy",
            "type",
            "execution_mode",
            "event_count",
            "duration_sec",
            "route_point_count",
        )
    }


def _episode_state(manifest: dict[str, Any], episode: dict[str, Any]) -> dict[str, Any]:
    profile = require_mapping(manifest.get("profile"), "manifest.profile")
    config = require_mapping(profile.get("config"), "manifest.profile.config")
    world = require_mapping(manifest.get("world"), "manifest.world")
    world_state = require_mapping(world.get("state"), "manifest.world.state")
    scene = require_mapping(world_state.get("scene"), "manifest.world.state.scene")
    spawn = require_mapping(world_state.get("player"), "manifest.world.state.player")
    capture = require_mapping(manifest.get("capture"), "manifest.capture")
    capture_settings = require_mapping(capture.get("settings"), "manifest.capture.settings")
    capture_spec = {key: value for key, value in capture_settings.items() if key != "display"}
    git = require_mapping(manifest.get("git"), "manifest.git")
    options = require_mapping(config.get("options", {}), "manifest.profile.config.options")
    shader_options = require_mapping(
        config.get("shader_options", {}), "manifest.profile.config.shader_options"
    )
    if not scene or not spawn or not capture_spec or world.get("seed") is None:
        raise DatasetValidationError(
            f"Pair invariants are incomplete for episode {episode.get('episode_id')!r}"
        )
    material = _resource_view(episode, "resourcepacks")
    shader = {
        "shaderpacks": _resource_view(episode, "shaderpacks"),
        "shader_options": shader_options,
    }
    world_static = {
        key: value for key, value in world_state.items() if key not in {"time", "weather"}
    }
    return {
        "mc_version": require_nonempty_string(manifest.get("mc_version"), "Minecraft version"),
        "git_commit": require_nonempty_string(git.get("commit"), "capture git commit"),
        "world_seed": world.get("seed"),
        "world_profile": require_nonempty_string(world.get("profile"), "world profile"),
        "scene": scene,
        "spawn": spawn,
        "trajectory": _trajectory_contract(manifest),
        "capture_spec": capture_spec,
        "material_style": material,
        "material_fingerprint": [item["sha256"] for item in material],
        "shader_quality": shader,
        "shader_fingerprint": {
            "shaderpacks": [item["sha256"] for item in shader["shaderpacks"]],
            "shader_options": shader_options,
        },
        "time_of_day": world_state.get("time"),
        "weather": world_state.get("weather"),
        "biome": world_state.get("biome"),
        "static": {
            "loader": profile.get("loader"),
            "quality": profile.get("quality"),
            "mods": [item["sha256"] for item in _resource_view(episode, "mods")],
            "client_options": options,
            "world_state": world_static,
            "resourcepack_target": require_mapping(
                episode.get("resourcepack_resolution"), "episode.resourcepack_resolution"
            ).get("target"),
        },
    }


def _require_shared(source: dict[str, Any], target: dict[str, Any], key: str, label: str) -> Any:
    if source[key] != target[key]:
        raise DatasetValidationError(f"Edit pair crosses {label}")
    return source[key]


def _changed_dimensions(source: dict[str, Any], target: dict[str, Any]) -> set[str]:
    projections = {
        "material_style": "material_fingerprint",
        "shader_quality": "shader_fingerprint",
        "time_of_day": "time_of_day",
        "weather": "weather",
    }
    return {dimension for dimension, key in projections.items() if source[key] != target[key]}


def _is_explicit_snow_biome(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and bool(value["id"].strip())
        and value.get("precipitation") == "snow"
    )


def _axis_values(edit_axis: str, source: dict[str, Any], target: dict[str, Any]) -> tuple[Any, Any]:
    if edit_axis == "material_style":
        return source["material_style"], target["material_style"]
    if edit_axis == "shader_quality":
        return source["shader_quality"], target["shader_quality"]
    if edit_axis == "time_of_day":
        return source["time_of_day"], target["time_of_day"]
    source_weather, target_weather = source["weather"], target["weather"]
    if edit_axis == "snow_weather":
        source_weather = "snow" if source_weather == "rain" else source_weather
        target_weather = "snow" if target_weather == "rain" else target_weather
    return source_weather, target_weather


def _validate_axis(edit_axis: str, source: dict[str, Any], target: dict[str, Any]) -> None:
    expected_dimension = "weather" if edit_axis == "snow_weather" else edit_axis
    changed = _changed_dimensions(source, target)
    if changed != {expected_dimension}:
        raise DatasetValidationError(
            f"Declared edit_axis={edit_axis!r} does not match actual differences={sorted(changed)!r}"
        )
    if edit_axis == "shader_quality":
        source_enabled = bool(source["shader_quality"]["shaderpacks"])
        target_enabled = bool(target["shader_quality"]["shaderpacks"])
        if source_enabled or not target_enabled:
            raise DatasetValidationError(
                "shader_quality requires a no-shader source and shader-enabled target"
            )
    elif edit_axis == "time_of_day":
        if {source["time_of_day"], target["time_of_day"]} != {"noon", "midnight"}:
            raise DatasetValidationError("time_of_day requires a noon/midnight pair")
    elif edit_axis == "weather":
        if {source["weather"], target["weather"]} != {"clear", "rain"}:
            raise DatasetValidationError("weather requires a clear/rain pair")
        if _is_explicit_snow_biome(source["biome"]):
            raise DatasetValidationError("Snow-biome precipitation must use edit_axis=snow_weather")
    elif edit_axis == "snow_weather":
        if source["biome"] != target["biome"] or not _is_explicit_snow_biome(source["biome"]):
            raise DatasetValidationError(
                "snow_weather requires one fixed biome with precipitation='snow'"
            )
        if {source["weather"], target["weather"]} not in (
            {"clear", "rain"},
            {"clear", "snow"},
        ):
            raise DatasetValidationError("snow_weather requires a clear/snow precipitation pair")


def _invariants(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    mc_version = _require_shared(source, target, "mc_version", "Minecraft version")
    commit = _require_shared(source, target, "git_commit", "capture commit")
    seed = _require_shared(source, target, "world_seed", "world seed")
    world_profile = _require_shared(source, target, "world_profile", "world profile")
    scene = _require_shared(source, target, "scene", "scene")
    spawn = _require_shared(source, target, "spawn", "spawn")
    trajectory = _require_shared(source, target, "trajectory", "trajectory")
    capture_spec = _require_shared(source, target, "capture_spec", "capture specification")
    static = _require_shared(source, target, "static", "static episode state")
    return {
        "mc_version": mc_version,
        "git_commit": commit,
        "world_seed": seed,
        "world_profile": world_profile,
        "scene_sha256": value_sha256(scene),
        "spawn_sha256": value_sha256(spawn),
        "trajectory_sha256": require_hash(trajectory.get("sha256"), 64, "pair trajectory sha256"),
        "trajectory_contract_sha256": value_sha256(trajectory),
        "capture_spec_sha256": value_sha256(capture_spec),
        "static_state_sha256": value_sha256(static),
        "qa_passed": True,
    }


def _validated_pair(
    declaration: dict[str, Any],
    episodes: dict[str, dict[str, Any]],
    manifests: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    prompt = require_nonempty_string(declaration.get("prompt"), "pair prompt").strip()
    source_id = require_nonempty_string(declaration.get("source_episode"), "source episode")
    target_id = require_nonempty_string(declaration.get("target_episode"), "target episode")
    edit_axis = require_nonempty_string(declaration.get("edit_axis"), "edit axis")
    if edit_axis not in EDIT_AXES:
        raise DatasetValidationError(f"Unsupported edit axis: {edit_axis!r}")
    if source_id == target_id:
        raise DatasetValidationError(f"Edit pair uses the same source and target: {source_id}")
    if source_id not in episodes or target_id not in episodes:
        raise DatasetValidationError(
            f"Edit pair references missing source/target: {source_id!r} -> {target_id!r}"
        )
    for episode_id in (source_id, target_id):
        episode = episodes[episode_id]
        if episode.get("accepted") is not True or episode.get("qa", {}).get("passed") is not True:
            raise DatasetValidationError(f"Edit pair episode did not pass QA: {episode_id}")
    source = _episode_state(manifests[source_id], episodes[source_id])
    target = _episode_state(manifests[target_id], episodes[target_id])
    invariants = _invariants(source, target)
    _validate_axis(edit_axis, source, target)
    source_value, target_value = _axis_values(edit_axis, source, target)
    identity = {
        "prompt": prompt,
        "source_episode": source_id,
        "target_episode": target_id,
        "edit_axis": edit_axis,
    }
    return {
        "pair_id": f"pair-{value_sha256(identity)[:16]}",
        **identity,
        "invariants": invariants,
        "axis_values": {
            "source": source_value,
            "target": target_value,
            "source_sha256": value_sha256(source_value),
            "target_sha256": value_sha256(target_value),
        },
    }


def build_edit_pairs(
    declarations: Sequence[dict[str, Any]],
    episodes: Sequence[dict[str, Any]],
    manifests: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate pair declarations against accepted episode manifests without I/O."""
    episode_map = {item.get("episode_id"): item for item in episodes}
    manifest_map = {item.get("run_id"): item for item in manifests}
    if len(episode_map) != len(episodes) or set(episode_map) != set(manifest_map):
        raise DatasetValidationError("Episode/manifest IDs are missing, duplicate, or inconsistent")
    target_owners: dict[str, tuple[str, str]] = {}
    for item in declarations:
        source_id = item.get("source_episode")
        target_id = item.get("target_episode")
        owner = (source_id, item.get("edit_axis"))
        if target_id in target_owners and target_owners[target_id] != owner:
            raise DatasetValidationError(
                f"Episode {target_id!r} is a conflicting target for multiple pair edits"
            )
        target_owners[target_id] = owner
    pairs = [_validated_pair(item, episode_map, manifest_map) for item in declarations]
    pair_ids = [item["pair_id"] for item in pairs]
    if len(pair_ids) != len(set(pair_ids)):
        raise DatasetValidationError("Edit-pair manifest contains duplicate pair declarations")
    covered = {
        episode_id
        for item in pairs
        for episode_id in (item["source_episode"], item["target_episode"])
    }
    if covered != set(episode_map):
        raise DatasetValidationError(
            f"Edit pairs do not cover every accepted episode: missing={sorted(set(episode_map) - covered)!r}"
        )
    return sorted(pairs, key=lambda item: item["pair_id"])


def edit_pairs(
    root: Path,
    pair_manifest: Path,
    episodes: Sequence[dict[str, Any]],
    manifests: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load, bind, and validate an edit-pair manifest inside a dataset root."""
    path = pair_manifest.resolve()
    pair_artifact = artifact(root, path)
    document = load_json(path)
    _validate_manifest_schema(document)
    return (
        {**pair_artifact, "schema_version": document["schema_version"]},
        build_edit_pairs(document["pairs"], episodes, manifests),
    )
