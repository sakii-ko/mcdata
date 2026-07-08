from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    configs: Path
    main_dir: Path
    work_base: Path
    output_dir: Path

    @classmethod
    def from_root(
        cls,
        root: Path | None = None,
        main_dir: str = ".mcdata/launcher",
        work_dir: str = ".mcdata/instances",
        output_dir: str = "runs",
    ) -> "ProjectPaths":
        base = (root or Path.cwd()).resolve()
        main_dir = os.environ.get("MCDATA_MAIN_DIR", main_dir)
        work_dir = os.environ.get("MCDATA_WORK_DIR", work_dir)
        output_dir = os.environ.get("MCDATA_OUTPUT_DIR", output_dir)
        return cls(
            root=base,
            configs=base / "configs",
            main_dir=_resolve_path(base, main_dir),
            work_base=_resolve_path(base, work_dir),
            output_dir=_resolve_path(base, output_dir),
        )

    def instance_dir(self, profile: str) -> Path:
        return self.work_base / profile


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()
