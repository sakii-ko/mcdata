import json
from pathlib import Path

from mcdata.actions.strategies import build_trajectory
from mcdata.config import load_yaml
from mcdata.scene_model import load_scene, walk_obstacles

ROOT = Path(__file__).resolve().parents[1]


def test_all_non_external_strategies_have_golden_files() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    expected = {name for name, spec in strategies.items() if spec.get("type") != "external"}
    actual = {path.stem for path in (ROOT / "tests" / "golden").glob("*.json")}

    assert actual == expected


def test_configured_trajectories_match_golden_bytes() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    for name, spec in sorted(strategies.items()):
        if spec.get("type") == "external":
            continue
        trajectory = build_trajectory(name, dict(spec), scene_obstacles=walk_obstacles(load_scene(ROOT / "configs")))
        actual = json.dumps(trajectory, indent=2, sort_keys=True) + "\n"
        expected = (ROOT / "tests" / "golden" / f"{name}.json").read_text(encoding="utf-8")

        assert actual == expected
