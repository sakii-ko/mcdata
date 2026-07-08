from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Missing config: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Expected mapping in {path}")
    return data


def merge_profile(defaults: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in profile.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def load_profile(config_dir: Path, name: str) -> dict[str, Any]:
    data = load_yaml(config_dir / "profiles.yml")
    profiles = data.get("profiles", {})
    if name not in profiles:
        known = ", ".join(sorted(profiles))
        raise ConfigError(f"Unknown profile '{name}'. Known profiles: {known}")
    return merge_profile(data.get("defaults", {}), profiles[name])


def load_asset_config(config_dir: Path) -> dict[str, Any]:
    return load_yaml(config_dir / "asset_sets.yml")
