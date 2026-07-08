from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def build_run_manifest(
    *,
    run_id: str,
    profile_name: str,
    profile: dict[str, Any],
    mc_version: str,
    resources: dict[str, Any],
    trajectory: dict[str, Any] | None,
    capture: dict[str, Any],
    env: dict[str, Any],
    git: dict[str, Any],
    started_at: str,
    ended_at: str | None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "profile": {
            "name": profile_name,
            "loader": profile.get("loader", "vanilla"),
            "quality": profile.get("quality"),
            "asset_set": profile.get("asset_set", "vanilla"),
            "width": profile.get("width"),
            "height": profile.get("height"),
            "server_port": profile.get("server_port"),
            "config": profile,
        },
        "mc_version": mc_version,
        "resources": resources,
        "world": {
            "seed": profile.get("world_seed"),
            "profile": profile.get("world_profile"),
            "state": profile.get("world_state", {}),
        },
        "trajectory": trajectory,
        "capture": capture,
        "env": env,
        "git": git,
        "started_at": started_at,
        "ended_at": ended_at,
        "error": error,
    }


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    path = run_dir / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
