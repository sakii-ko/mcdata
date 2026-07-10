from pathlib import Path
import ast
import hashlib

from mcdata.actions.strategies import STRATEGY_BUILDERS, build_trajectory
from mcdata.config import load_profile, load_yaml
from mcdata.render.options import write_iris_config
from mcdata.render.scene import _scene_commands
from mcdata.scene_model import load_scene, scene_commands, scene_mapping, walk_obstacles

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SCENE_COMMAND_COUNT = 127
EXPECTED_SCENE_COMMANDS_SHA256 = (
    "840c0955ad63439135dd096fcfd54f57c11ad342b8ae3de9872372a91950aaac"
)


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
    names = ["preview_vanilla_high", "preview_texture_high", "preview_shader_high"]
    resolved = [load_profile(ROOT / "configs", name) for name in names]
    comparable = [
        {key: value for key, value in profile.items() if key not in {"description", "asset_set"}}
        for profile in resolved
    ]

    assert comparable[1:] == comparable[:-1]

    assert asset_sets[profiles["preview_vanilla_high"]["asset_set"]] == {
        "resourcepacks": [],
        "shaderpack": None,
    }
    assert asset_sets[profiles["preview_texture_high"]["asset_set"]]["resourcepacks"] == [
        "faithful-32x",
        "fresh-animations",
    ]
    assert asset_sets[profiles["preview_texture_high"]["asset_set"]]["shaderpack"] is None
    assert asset_sets[profiles["preview_shader_high"]["asset_set"]]["resourcepacks"] == [
        "faithful-32x",
        "fresh-animations",
    ]
    assert (
        asset_sets[profiles["preview_shader_high"]["asset_set"]]["shaderpack"]
        == "complementary-reimagined"
    )
    assert resolved[0]["world_state"]["player"] == {
        "x": -3,
        "y": 64,
        "z": -8,
        "yaw": 0,
        "pitch": 18,
    }
    assert resolved[0]["world_state"]["clear_non_player_entities"] is True
    assert resolved[0]["world_state"]["gamerules"]["spawn_mobs"] is False


def test_ten_minute_render_scan_is_stationary_and_covers_capture_duration() -> None:
    spec = load_yaml(ROOT / "configs" / "actions.yml")["strategies"][
        "render_comparison_scan_10min"
    ]
    trajectory = build_trajectory(
        "render_comparison_scan_10min",
        dict(spec),
    )

    assert trajectory["type"] == "look_scan"
    assert trajectory["duration_sec"] >= 600
    assert trajectory["events"]
    assert not any("key" in event for event in trajectory["events"])
    assert {event["duration"] for event in trajectory["events"]} == {6.0}
    final_event = max(trajectory["events"], key=lambda event: event["t"])
    assert final_event["t"] < 600 < final_event["t"] + final_event["duration"]


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


def test_feedback_visual_profiles_are_policy_aligned_at_1080p() -> None:
    names = [
        "feedback_vanilla_1080p",
        "feedback_modernarch_1080p",
        "feedback_modernarch_solas_1080p",
    ]
    resolved = [load_profile(ROOT / "configs", name) for name in names]
    invariant = [
        {
            key: value
            for key, value in profile.items()
            if key not in {"description", "asset_set", "shader_options"}
        }
        for profile in resolved
    ]

    assert invariant[1:] == invariant[:-1]
    assert {profile["game_version"] for profile in resolved} == {"26.2"}
    assert {(profile["width"], profile["height"]) for profile in resolved} == {
        (1920, 1080)
    }
    assert {profile["quality"] for profile in resolved} == {"high"}
    assert [profile["asset_set"] for profile in resolved] == [
        "vanilla",
        "modernarch_high_no_shader",
        "modernarch_solas",
    ]
    assert resolved[0]["shader_options"] == {}
    assert resolved[1]["shader_options"] == {}
    assert resolved[0]["world_state"]["player"] == {
        "x": 0,
        "y": 64,
        "z": -14,
        "yaw": 90,
        "pitch": 18,
    }
    assert resolved[0]["world_state"]["gamerules"] == {
        "advance_time": False,
        "advance_weather": False,
        "command_block_output": False,
        "keep_inventory": True,
        "random_tick_speed": 0,
        "send_command_feedback": False,
        "show_death_messages": False,
        "spawn_mobs": False,
    }


def test_feedback_visual_profiles_share_required_fabric_superset() -> None:
    names = [
        "feedback_vanilla_1080p",
        "feedback_modernarch_1080p",
        "feedback_modernarch_solas_1080p",
        "preview_patrix_full_solas_1080p",
    ]
    expected = [
        "fabric-api",
        "sodium",
        "iris",
        "modmenu",
        "advancementdisable",
        "no-chat-reports",
        "continuity",
        "polytone",
        "entity-model-features",
        "entitytexturefeatures",
    ]

    assert [load_profile(ROOT / "configs", name)["mods"] for name in names] == [
        expected
    ] * len(names)
    assert load_profile(
        ROOT / "configs", "preview_patrix_full_solas_1080p"
    )["game_version"] == "26.2"


def test_iter04_resource_pack_selection_and_priority_order() -> None:
    config = load_yaml(ROOT / "configs" / "asset_sets.yml")
    assets = config["assets"]["resourcepacks"]
    asset_sets = config["asset_sets"]

    assert assets["modernarch-128x"] == {
        "provider": "modrinth",
        "slug": "modernarch",
        "type": "resourcepack",
    }
    assert assets["patrix-32x-full"]["file_patterns"] == [
        "Patrix_*_32x_basic.zip",
        "Patrix_*_32x_addon.zip",
        "Patrix_*_32x_bonus.zip",
        "Patrix_*_models.zip",
    ]
    assert asset_sets["modernarch_high_no_shader"] == {
        "description": "ModernArch 128x realistic architecture materials without shaders.",
        "resourcepacks": ["modernarch-128x"],
        "shaderpack": None,
    }
    assert asset_sets["modernarch_solas"]["resourcepacks"] == ["modernarch-128x"]
    assert asset_sets["modernarch_solas"]["shaderpack"] == "solas-shader"
    assert asset_sets["patrix_full_solas"]["resourcepacks"] == ["patrix-32x-full"]
    assert asset_sets["patrix_full_solas"]["shaderpack"] == "solas-shader"


def test_solas_profiles_emit_verified_ultra_labpbr_options(tmp_path: Path) -> None:
    modernarch = load_profile(
        ROOT / "configs", "feedback_modernarch_solas_1080p"
    )["shader_options"]
    patrix = load_profile(
        ROOT / "configs", "preview_patrix_full_solas_1080p"
    )["shader_options"]
    legendary = load_profile(
        ROOT / "configs", "preview_legendary_rt_solas_1080p"
    )["shader_options"]

    assert modernarch == patrix == legendary
    assert modernarch["MATERIAL_FORMAT"] == "1"
    assert modernarch["ADVANCED_MATERIALS"] == "true"
    assert modernarch["GENERATED_NORMALS"] == "false"
    assert modernarch["GENERATED_SPECULAR"] == "false"
    assert modernarch["WATER_NORMALS"] == "3"
    assert modernarch["WATER_REFLECTIONS"] == "true"
    assert modernarch["WATER_CAUSTICS"] == "true"
    assert modernarch["REFRACTION"] == "true"
    assert modernarch["PARALLAX_QUALITY"] == "128"
    assert modernarch["SELF_SHADOW_QUALITY"] == "16"
    assert modernarch["VL_SAMPLES"] == "16"
    assert modernarch["VOXEL_VOLUME_SIZE"] == "512"
    assert modernarch["shadowMapResolution"] == "8192"
    assert modernarch["shadowDistance"] == "384.0"

    write_iris_config(
        tmp_path,
        shaderpack="Solas Shader V3.7.zip",
        enabled=True,
        shader_options=modernarch,
    )
    actual = (tmp_path / "shaderpacks" / "Solas Shader V3.7.zip.txt").read_bytes()
    expected = (
        ROOT / "tests" / "golden" / "solas_3_7_ultra_labpbr_options.txt"
    ).read_bytes()
    assert actual == expected


def test_realistic_lookdev_profiles_are_aligned_and_use_exact_26_2_assets() -> None:
    config = load_yaml(ROOT / "configs" / "asset_sets.yml")
    assets = config["assets"]["resourcepacks"]
    asset_sets = config["asset_sets"]
    names = [
        "feedback_vanilla_1080p",
        "lookdev_vanilla_unbound_1080p",
        "feedback_legendary_rt_1080p",
        "feedback_legendary_rt_unbound_1080p",
        "lookdev_legendary_rt_bliss_1080p",
        "feedback_modernarch_1080p",
        "lookdev_modernarch_unbound_1080p",
        "lookdev_optimum_1080p",
        "preview_optimum_unbound_1080p",
        "lookdev_patrix_full_1080p",
        "lookdev_patrix_full_unbound_1080p",
        "lookdev_stylista_1080p",
        "preview_stylista_unbound_1080p",
        "lookdev_prettyrealistic_1080p",
        "lookdev_prettyrealistic_unbound_1080p",
        "lookdev_yitalith_1080p",
        "lookdev_yitalith_unbound_1080p",
    ]
    resolved = [load_profile(ROOT / "configs", name) for name in names]
    invariant = [
        {
            key: value
            for key, value in profile.items()
            if key not in {"description", "asset_set", "shader_options"}
        }
        for profile in resolved
    ]

    assert invariant[1:] == invariant[:-1]
    assert assets["legendary-rt-128x"]["slug"] == "legendary-rt-textures"
    assert assets["optimum-realism-64x"]["slug"] == "optimum-realism"
    assert assets["prettyrealistic-32x"]["slug"] == "prettyrealistic"
    assert assets["yitalith-128x"]["slug"] == "yitalith"
    assert asset_sets["legendary_rt_high_no_shader"]["shaderpack"] is None
    assert asset_sets["legendary_rt_unbound"]["shaderpack"] == (
        "complementary-unbound"
    )
    assert asset_sets["optimum_realism_unbound"]["resourcepacks"] == [
        "optimum-realism-64x"
    ]
    assert asset_sets["patrix_full_high_no_shader"]["resourcepacks"] == [
        "patrix-32x-full"
    ]
    assert asset_sets["patrix_full_unbound"]["shaderpack"] == (
        "complementary-unbound"
    )
    assert asset_sets["stylista_unbound"]["resourcepacks"] == ["stylista"]


def test_unbound_profiles_emit_verified_cinematic_labpbr_options(tmp_path: Path) -> None:
    names = [
        "feedback_legendary_rt_unbound_1080p",
        "lookdev_vanilla_unbound_1080p",
        "lookdev_modernarch_unbound_1080p",
        "preview_optimum_unbound_1080p",
        "lookdev_patrix_full_unbound_1080p",
        "preview_stylista_unbound_1080p",
        "lookdev_yitalith_unbound_1080p",
    ]
    options = [
        load_profile(ROOT / "configs", name)["shader_options"] for name in names
    ]

    assert options[1:] == options[:-1]
    assert options[0]["RP_MODE"] == "3"
    assert options[0]["POM_QUALITY"] == "256"
    assert options[0]["SHADOW_QUALITY"] == "5"
    assert options[0]["WATER_REFLECT_QUALITY"] == "2"
    assert options[0]["WORLD_SPACE_REFLECTIONS"] == "1"
    assert options[0]["REFLECTION_RES"] == "1.0"
    assert options[0]["MOTION_BLUR_EFFECT"] == "-1"
    assert load_profile(
        ROOT / "configs", "lookdev_prettyrealistic_unbound_1080p"
    )["shader_options"]["RP_MODE"] == "2"

    write_iris_config(
        tmp_path,
        shaderpack="ComplementaryUnbound_r5.8.1.zip",
        enabled=True,
        shader_options=options[0],
    )
    actual = (
        tmp_path / "shaderpacks" / "ComplementaryUnbound_r5.8.1.zip.txt"
    ).read_bytes()
    expected = (
        ROOT / "tests" / "golden" / "complementary_unbound_5_8_cinematic_options.txt"
    ).read_bytes()
    assert actual == expected


def test_lighting_weather_pair_profiles_are_single_axis_and_renderer_fixed() -> None:
    names = [
        "lookdev_pair_legendary_unbound_noon_1080p",
        "lookdev_pair_legendary_unbound_golden_hour_1080p",
        "lookdev_pair_legendary_unbound_midnight_1080p",
        "lookdev_pair_legendary_unbound_rain_1080p",
        "lookdev_pair_legendary_unbound_snow_clear_1080p",
        "lookdev_pair_legendary_unbound_snow_1080p",
    ]
    profiles = [load_profile(ROOT / "configs", name) for name in names]
    renderer_contracts = [
        {key: value for key, value in profile.items() if key not in {"description", "world_state"}}
        for profile in profiles
    ]

    assert renderer_contracts[1:] == renderer_contracts[:-1]
    assert {profile["asset_set"] for profile in profiles} == {"legendary_rt_unbound"}
    assert all(
        profile["shader_options"] == profiles[0]["shader_options"] for profile in profiles[1:]
    )
    assert {
        key: profiles[0]["shader_options"][key]
        for key in (
            "LIGHT_NOON_I",
            "LIGHT_MORNING_I",
            "LIGHT_NIGHT_I",
            "LIGHT_RAIN_I",
            "IMPROVED_RAIN_DEFINE",
            "WEATHER_TEX_OPACITY",
        )
    } == {
        "LIGHT_NOON_I": "1.30",
        "LIGHT_MORNING_I": "1.30",
        "LIGHT_NIGHT_I": "1.50",
        "LIGHT_RAIN_I": "0.80",
        "IMPROVED_RAIN_DEFINE": "1",
        "WEATHER_TEX_OPACITY": "150",
    }
    capture_specs = {
        (profile["width"], profile["height"], profile["capture_fps"]) for profile in profiles
    }
    assert capture_specs == {(1920, 1080, 24)}

    noon, golden, midnight, rain, snow_clear, snow = [
        profile["world_state"] for profile in profiles
    ]

    def without_time(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "time"}

    def without_weather(state: dict) -> dict:
        return {key: value for key, value in state.items() if key != "weather"}

    assert noon["time"] == "noon"
    assert golden["time"] == 12000
    assert midnight["time"] == "midnight"
    assert without_time(golden) == without_time(noon)
    assert without_time(midnight) == without_time(noon)
    assert rain["weather"] == "rain"
    assert without_weather(rain) == without_weather(noon)
    assert snow_clear["biome"] == snow["biome"]
    assert snow_clear["biome"]["id"] == "minecraft:snowy_plains"
    assert snow_clear["biome"]["precipitation"] == "snow"
    assert snow_clear["weather"] == "clear"
    assert snow["weather"] == "rain"
    assert without_weather(snow) == without_weather(snow_clear)


def test_bliss_profile_emits_verified_max_realism_motion_options(tmp_path: Path) -> None:
    profile = load_profile(
        ROOT / "configs", "lookdev_legendary_rt_bliss_1080p"
    )
    options = profile["shader_options"]

    assert profile["asset_set"] == "legendary_rt_bliss"
    assert options["LPV_ENABLED"] == "true"
    assert options["POM"] == "true"
    assert options["Screen_Space_Reflections"] == "true"
    assert options["SCREENSPACE_REFLECTIONS"] == "true"
    assert options["WATER_REFLECTIONS"] == "true"
    assert options["shadowMapResolution"] == "8192"
    assert options["MOTION_BLUR"] == "false"
    assert options["DOF_QUALITY"] == "-1"
    assert options["HQ_SSGI"] == "false"
    assert options["indirect_effect"] == "2"

    write_iris_config(
        tmp_path,
        shaderpack="Bliss_v2.1.2_(Chocapic13_Shaders_edit).zip",
        enabled=True,
        shader_options=options,
    )
    actual = (
        tmp_path
        / "shaderpacks"
        / "Bliss_v2.1.2_(Chocapic13_Shaders_edit).zip.txt"
    ).read_bytes()
    expected = (
        ROOT / "tests" / "golden" / "bliss_2_1_2_max_realism_options.txt"
    ).read_bytes()
    assert actual == expected


def test_legendary_rt_material_format_calibration_changes_only_decoder() -> None:
    unbound_lab = load_profile(
        ROOT / "configs", "feedback_legendary_rt_unbound_1080p"
    )
    unbound_seus = load_profile(
        ROOT / "configs", "lookdev_legendary_rt_unbound_seuspbr_1080p"
    )
    solas_lab = load_profile(
        ROOT / "configs", "preview_legendary_rt_solas_1080p"
    )
    solas_seus = load_profile(
        ROOT / "configs", "lookdev_legendary_rt_solas_seuspbr_1080p"
    )

    assert unbound_lab["shader_options"]["RP_MODE"] == "3"
    assert unbound_seus["shader_options"]["RP_MODE"] == "2"
    assert solas_lab["shader_options"]["MATERIAL_FORMAT"] == "1"
    assert solas_seus["shader_options"]["MATERIAL_FORMAT"] == "0"
    for lab, seus, option in (
        (unbound_lab, unbound_seus, "RP_MODE"),
        (solas_lab, solas_seus, "MATERIAL_FORMAT"),
    ):
        lab_options = dict(lab["shader_options"])
        seus_options = dict(seus["shader_options"])
        lab_options.pop(option)
        seus_options.pop(option)
        assert lab_options == seus_options
        for key in lab:
            if key not in {"description", "shader_options"}:
                assert lab[key] == seus[key]


def test_style_lookdev_profiles_are_aligned_and_distinct() -> None:
    names = [
        "lookdev_style_vanilla_1080p",
        "lookdev_style_stylista_1080p",
        "lookdev_style_reimagined_1080p",
        "lookdev_style_ashen_1080p",
        "lookdev_style_simplified_1080p",
        "lookdev_style_quadral_1080p",
        "lookdev_style_bare_bones_pbr_1080p",
        "lookdev_style_natural_1080p",
    ]
    resolved = [load_profile(ROOT / "configs", name) for name in names]
    invariant = [
        {
            key: value
            for key, value in profile.items()
            if key not in {"description", "asset_set"}
        }
        for profile in resolved
    ]
    expected_mods = [
        "fabric-api",
        "sodium",
        "iris",
        "modmenu",
        "advancementdisable",
        "no-chat-reports",
        "continuity",
        "polytone",
        "respackopts",
        "entity-model-features",
        "entitytexturefeatures",
    ]
    config = load_yaml(ROOT / "configs" / "asset_sets.yml")
    assets = config["assets"]["resourcepacks"]

    assert invariant[1:] == invariant[:-1]
    assert resolved[0]["mods"] == expected_mods
    assert len({profile["asset_set"] for profile in resolved}) == len(resolved)
    assert assets["reimagined-style"]["slug"] == "reimagined"
    assert assets["ashen-16x"]["slug"] == "ashen"
    assert assets["jeelh-simplified"]["slug"] == "jeelh-simplified"
    assert assets["quadral"]["slug"] == "quadral"
    assert assets["bare-bones-pbr-128x"]["slug"] == "bare-bones-pbr-x128"


def test_action_strategy_types_are_registered() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})

    for name, spec in strategies.items():
        assert spec.get("type") in STRATEGY_BUILDERS, name


def test_scene_has_no_uncontrolled_fire_or_lava() -> None:
    commands = _configured_scene_commands()

    assert not any("minecraft:lava" in command for command in commands)
    assert not any("minecraft:fire" in command for command in commands)


def test_scene_air_clear_is_split_under_fill_limit() -> None:
    commands = _configured_scene_commands()

    air_commands = [command for command in commands if command.endswith(" minecraft:air")]

    assert air_commands == [
        "fill -30 64 -30 30 71 30 minecraft:air",
        "fill -30 72 -30 30 79 30 minecraft:air",
        "fill -30 80 -30 30 87 30 minecraft:air",
        "fill -30 88 -30 30 94 30 minecraft:air",
    ]


def test_scene_reflecting_basins_are_contained_beside_a_full_block_bridge() -> None:
    commands = _configured_scene_commands()

    assert "fill -13 62 -2 -6 62 8 minecraft:dark_prismarine" in commands
    assert "fill 6 62 -2 13 62 8 minecraft:dark_prismarine" in commands
    assert "fill -13 63 -2 -6 63 8 minecraft:water" in commands
    assert "fill 6 63 -2 13 63 8 minecraft:water" in commands
    assert "fill -4 63 -3 4 63 9 minecraft:dark_oak_planks" in commands
    assert "fill -14 64 -3 -14 64 9 minecraft:polished_blackstone_bricks" in commands
    assert "fill 14 64 -3 14 64 9 minecraft:polished_blackstone_bricks" in commands
    assert not any(
        command.endswith("65 9 minecraft:polished_blackstone_bricks") for command in commands
    )


def test_showcase_scene_has_a_continuous_varied_walk_surface() -> None:
    spec = load_scene(ROOT / "configs")
    blocks = {entry.block for entry in spec.entries}
    plaza = next(entry for entry in spec.entries if entry.region == "plaza_base")

    assert plaza.start == (-22, -1, -22)
    assert plaza.end == (22, -1, 22)
    assert {
        "minecraft:polished_andesite",
        "minecraft:mud_bricks",
        "minecraft:cut_sandstone",
        "minecraft:waxed_oxidized_cut_copper",
        "minecraft:mossy_stone_bricks",
        "minecraft:deepslate_tiles",
        "minecraft:stone_bricks",
        "minecraft:polished_blackstone_bricks",
        "minecraft:dark_oak_planks",
    } <= blocks


def test_showcase_scene_obstacle_footprint_is_exact() -> None:
    spec = load_scene(ROOT / "configs")

    assert len(walk_obstacles(spec)) == 429


def test_scene_yml_commands_match_current_server_commands() -> None:
    spec = load_scene(ROOT / "configs")
    commands = scene_commands(spec)
    encoded = ("\n".join(commands) + "\n").encode("utf-8")

    assert len(commands) == EXPECTED_SCENE_COMMAND_COUNT
    assert hashlib.sha256(encoded).hexdigest() == EXPECTED_SCENE_COMMANDS_SHA256
    assert _scene_commands(scene_mapping(spec)) == commands


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
