from pathlib import Path
import ast

from mcdata.actions.strategies import STRATEGY_BUILDERS
from mcdata.config import load_profile, load_yaml
from mcdata.render.scene import _scene_commands
from mcdata.scene_model import load_scene, scene_commands, scene_mapping

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SCENE_COMMANDS = [
    "forceload add -32 -32 32 32",
    "fill -18 64 -18 18 86 18 minecraft:air",
    "fill -18 87 -18 18 92 18 minecraft:air",
    "fill -24 60 -24 24 62 24 minecraft:dirt",
    "fill -24 63 -24 24 63 24 minecraft:grass_block",
    "fill -15 63 -15 15 63 15 minecraft:smooth_stone",
    "fill -14 63 -2 -5 63 7 minecraft:water",
    "fill -14 62 -2 -5 62 7 minecraft:blue_concrete",
    "fill 5 64 -2 14 64 7 minecraft:glass",
    "fill 5 63 -2 14 63 7 minecraft:white_concrete",
    "fill -2 64 9 2 67 9 minecraft:oak_leaves",
    "fill -4 64 14 4 68 14 minecraft:white_concrete",
    "setblock -10 64 -10 minecraft:torch",
    "setblock -7 64 -10 minecraft:lantern",
    "setblock -4 64 -10 minecraft:redstone_torch",
    "setblock -1 64 -10 minecraft:redstone_lamp[lit=true]",
    "fill 1 64 -11 3 64 -9 minecraft:glass",
    "setblock 2 64 -10 minecraft:lava",
    "setblock 5 64 -10 minecraft:sea_lantern",
    "setblock 8 64 -10 minecraft:glowstone",
    "setblock 11 64 -10 minecraft:beacon",
    "setblock -14 64 12 minecraft:oak_log",
    "setblock -14 65 12 minecraft:oak_leaves",
    "setblock 14 64 12 minecraft:polished_deepslate",
    "setblock 14 65 12 minecraft:glass",
]


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


def test_three_way_preview_profiles_are_strictly_comparable() -> None:
    profiles = load_yaml(ROOT / "configs" / "profiles.yml")["profiles"]
    asset_sets = load_yaml(ROOT / "configs" / "asset_sets.yml")["asset_sets"]
    names = ["preview_vanilla_high", "preview_texture_high", "matrix_shader_high"]
    selected = [profiles[name] for name in names]

    comparable_keys = (
        "version_strategy",
        "loader",
        "quality",
        "mods",
        "width",
        "height",
        "server_port",
        "world_profile",
    )
    for key in comparable_keys:
        assert len({repr(profile[key]) for profile in selected}) == 1, key

    assert asset_sets[profiles["preview_vanilla_high"]["asset_set"]] == {
        "resourcepacks": [],
        "shaderpack": None,
    }
    assert asset_sets[profiles["preview_texture_high"]["asset_set"]]["resourcepacks"] == [
        "faithful-32x",
        "fresh-animations",
    ]
    assert asset_sets[profiles["preview_texture_high"]["asset_set"]]["shaderpack"] is None
    assert asset_sets[profiles["matrix_shader_high"]["asset_set"]]["resourcepacks"] == [
        "faithful-32x",
        "fresh-animations",
    ]
    assert (
        asset_sets[profiles["matrix_shader_high"]["asset_set"]]["shaderpack"]
        == "complementary-reimagined"
    )


def test_matrix_world_states_freeze_scene_and_suppress_recipe_toasts() -> None:
    names = load_yaml(ROOT / "configs" / "profiles.yml")["profiles"]

    for name in names:
        if not name.startswith("matrix_"):
            continue
        state = load_profile(ROOT / "configs", name)["world_state"]
        assert state["gamerules"]["random_tick_speed"] == 0, name
        assert state["clear_dropped_items"] is True, name
        assert state["clear_inventory"] is True, name
        assert state["pregrant_recipes"] is True, name


def test_iter03_supported_expansion_combinations() -> None:
    profiles = load_yaml(ROOT / "configs" / "profiles.yml")["profiles"]
    asset_sets = load_yaml(ROOT / "configs" / "asset_sets.yml")["asset_sets"]

    euphoria = profiles["matrix_euphoria_complementary"]
    assert euphoria["asset_set"] == "euphoria_complementary"
    assert "euphoria-patches" in euphoria["mods"]
    assert asset_sets["euphoria_complementary"] == {
        "description": "Euphoria Patches mod plus Complementary Reimagined for extended shader features.",
        "resourcepacks": [],
        "shaderpack": "complementary-reimagined",
    }

    solas_patrix = profiles["matrix_solas_patrix"]
    assert solas_patrix["asset_set"] == "solas_patrix"
    assert asset_sets["solas_patrix"]["resourcepacks"] == ["patrix-32x"]
    assert asset_sets["solas_patrix"]["shaderpack"] == "solas-shader"


def test_action_strategy_types_are_registered() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})

    for name, spec in strategies.items():
        assert spec.get("type") in STRATEGY_BUILDERS, name


def test_scene_lava_source_is_glass_contained_before_setblock() -> None:
    commands = _configured_scene_commands()

    glass = "fill 1 64 -11 3 64 -9 minecraft:glass"
    lava = "setblock 2 64 -10 minecraft:lava"

    assert glass in commands
    assert lava in commands
    assert commands.index(glass) < commands.index(lava)


def test_scene_air_clear_is_split_under_fill_limit() -> None:
    commands = _configured_scene_commands()

    assert "fill -18 64 -18 18 92 18 minecraft:air" not in commands
    assert "fill -18 64 -18 18 86 18 minecraft:air" in commands
    assert "fill -18 87 -18 18 92 18 minecraft:air" in commands
    assert commands.index("fill -18 64 -18 18 86 18 minecraft:air") < commands.index(
        "fill -18 87 -18 18 92 18 minecraft:air"
    )


def test_scene_pool_is_below_walk_surface() -> None:
    commands = _configured_scene_commands()

    assert "fill -14 64 -2 -5 64 7 minecraft:water" not in commands
    assert "fill -14 63 -2 -5 63 7 minecraft:water" in commands
    assert "fill -14 62 -2 -5 62 7 minecraft:blue_concrete" in commands


def test_scene_yml_commands_match_current_server_commands() -> None:
    spec = load_scene(ROOT / "configs")

    assert scene_commands(spec) == EXPECTED_SCENE_COMMANDS
    assert _scene_commands(scene_mapping(spec)) == EXPECTED_SCENE_COMMANDS


def _configured_scene_commands() -> list[str]:
    return scene_commands(load_scene(ROOT / "configs"))


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
