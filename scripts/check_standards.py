#!/usr/bin/env python3
"""Mechanical enforcement of docs/CODE_STANDARDS.md ([checker] rules).

Run: python3 scripts/check_standards.py
Exit 0 = pass (warnings allowed), 1 = hard violations.

Maintained by planner. Baseline entries are a shrink-only ratchet: they mark
known violations that predate the rule and MUST be removed by the noted
iteration; adding new entries requires planner approval.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mcdata"
SCRIPTS = ROOT / "scripts"

# --- R2: os.environ/os.getenv only at designated boundaries -----------------
ENV_ALLOWED_FILES = {"paths.py", "doctor.py", "settings.py"}
# shrink-only ratchet: file -> deadline note
ENV_TEMP_BASELINE = {}
ENV_PATTERN = re.compile(r"os\.(environ|getenv)")

# --- R15: absolute-path literals; doctor.py diagnoses the machine, so
# absolute paths are its subject matter, not a violation ---------------------
ABS_PATH_ALLOWED_FILES = {"doctor.py"}

# --- R12/R14: cross-module import whitelist (mirrors ARCHITECTURE.md) -------
# importer key -> allowed imported module keys ("*" = anything under mcdata)
IMPORT_WHITELIST: dict[str, set[str]] = {
    "_pkg_root": set(),  # src/mcdata/__init__.py: version only, no submodule imports
    "cli": {"*"},
    "doctor": set(),
    "config": set(),
    "paths": set(),
    "net": set(),
    "mojang": {"net"},
    "modrinth": {"net"},
    "packs": {"net", "paths", "modrinth", "config"},
    "manifest": {"paths"},
    "runlog": {"paths"},
    "settings": {"config", "paths"},
    "schemas": set(),
    "actions": {"config", "paths"},
    "actions.replay": set(),  # replay must stay dependency-free (runtime input backend)
    "render": {
        "config", "paths", "packs", "mojang", "modrinth", "net",
        "manifest", "runlog", "settings", "actions.replay", "qa.probe", "render",
    },
    "qa": {"paths", "qa"},
}


def importer_key(rel: Path) -> str:
    parts = rel.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return "_pkg_root"
    if parts == ("actions", "replay"):
        return "actions.replay"
    return parts[0]


def imported_key(dotted: str) -> str | None:
    if dotted == "mcdata" or not dotted.startswith("mcdata."):
        return None
    tail = dotted[len("mcdata."):]
    parts = tail.split(".")
    if parts[:2] in (["actions", "replay"], ["qa", "probe"]):
        return ".".join(parts[:2])
    return parts[0]


def collect_mcdata_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                key = imported_key(alias.name)
                if key:
                    found.add(key)
        elif isinstance(node, ast.ImportFrom) and node.module:
            key = imported_key(node.module)
            if key:
                found.add(key)
    return found


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    py_files = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)

    for path in py_files:
        rel = path.relative_to(SRC)
        text = path.read_text(encoding="utf-8")

        # R2: env boundary
        env_hits = [
            i + 1
            for i, line in enumerate(text.splitlines())
            if ENV_PATTERN.search(line) and not line.lstrip().startswith("#")
        ]
        if env_hits and rel.name not in ENV_ALLOWED_FILES:
            entry = str(rel)
            if entry in ENV_TEMP_BASELINE:
                warnings.append(
                    f"R2 baseline: {rel} reads env at lines {env_hits} -- {ENV_TEMP_BASELINE[entry]}"
                )
            else:
                failures.append(
                    f"R2: {rel} reads os.environ/getenv at lines {env_hits}; "
                    f"only {sorted(ENV_ALLOWED_FILES)} may (see CODE_STANDARDS.md)"
                )
        elif not env_hits and str(rel) in ENV_TEMP_BASELINE:
            warnings.append(f"R2: {rel} is clean now -- remove it from ENV_TEMP_BASELINE (ratchet)")

        # R15: no literal absolute paths
        if rel.name not in ABS_PATH_ALLOWED_FILES:
            for i, line in enumerate(text.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if re.search(r"""["']/(tmp|root|home)(/|["'])""", line):
                    failures.append(f"R15: {rel}:{i} literal absolute path: {stripped[:80]}")

        # R12/R14: import whitelist
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            failures.append(f"syntax error in {rel}: {exc}")
            continue
        key = importer_key(rel)
        if key not in IMPORT_WHITELIST:
            failures.append(
                f"R12: {rel} belongs to unregistered module '{key}'; "
                f"register it in check_standards.py and ARCHITECTURE.md"
            )
        else:
            allowed = IMPORT_WHITELIST[key]
            if "*" not in allowed:
                for target in sorted(collect_mcdata_imports(tree)):
                    if target == key or target in allowed:
                        continue
                    if target.split(".")[0] == key.split(".")[0] and key != "actions.replay":
                        continue  # intra-package import (render->render.options etc.)
                    failures.append(f"R12: {key} ({rel}) imports mcdata.{target}, not in whitelist")

        # R19: size guidance (warn only)
        n_lines = text.count("\n") + 1
        if n_lines > 600:
            warnings.append(f"R19: {rel} has {n_lines} lines (>600) -- justify in report")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (getattr(node, "end_lineno", node.lineno) or node.lineno) - node.lineno + 1
                if span > 80:
                    warnings.append(f"R19: {rel}:{node.lineno} function {node.name} spans {span} lines (>80)")

    # R21: bash discipline
    for sh in sorted(SCRIPTS.glob("*.sh")):
        if sh.name == "mcdata_env.sh" or sh.name.endswith(".example.sh"):
            continue
        if "set -euo pipefail" not in sh.read_text(encoding="utf-8"):
            failures.append(f"R21: scripts/{sh.name} missing 'set -euo pipefail'")

    for msg in warnings:
        print(f"WARN  {msg}")
    for msg in failures:
        print(f"FAIL  {msg}")
    print(f"check_standards: {len(failures)} failure(s), {len(warnings)} warning(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
