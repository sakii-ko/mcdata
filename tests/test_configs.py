from pathlib import Path
import ast

from mcdata.actions.strategies import STRATEGY_BUILDERS
from mcdata.config import load_yaml

ROOT = Path(__file__).resolve().parents[1]


def test_profile_asset_sets_exist() -> None:
    profiles = load_yaml(ROOT / "configs" / "profiles.yml").get("profiles", {})
    asset_sets = load_yaml(ROOT / "configs" / "asset_sets.yml").get("asset_sets", {})

    for name, profile in profiles.items():
        assert profile.get("asset_set") in asset_sets, name


def test_matrix_profiles_share_world_and_server_port() -> None:
    profiles = load_yaml(ROOT / "configs" / "profiles.yml").get("profiles", {})
    matrix_profiles = {
        name: profile for name, profile in profiles.items() if name.startswith("matrix_")
    }

    assert matrix_profiles
    assert {profile.get("world_profile") for profile in matrix_profiles.values()} == {
        "render_matrix_base"
    }
    assert {profile.get("server_port") for profile in matrix_profiles.values()} == {25570}


def test_action_strategy_types_are_registered() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})

    for name, spec in strategies.items():
        assert spec.get("type") in STRATEGY_BUILDERS, name


def test_qa_package_does_not_import_render_or_actions() -> None:
    for path in (ROOT / "src" / "mcdata" / "qa").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            assert not any(name.startswith("mcdata.render") for name in names), path
            assert not any(name.startswith("mcdata.actions") for name in names), path
