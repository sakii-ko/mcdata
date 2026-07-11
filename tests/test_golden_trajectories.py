import hashlib
import json
from pathlib import Path

from mcdata.actions.strategies import build_trajectory
from mcdata.config import load_yaml
from mcdata.scene_model import load_scene, walk_obstacles

ROOT = Path(__file__).resolve().parents[1]

COMPACT_TRAJECTORY_SHA256 = {
    "celestial_cardinal_scan_diagnostic": "6a319c1eff7f2c31d397d545bb0788aa7985a07053ef3f929ea00160bbeb9b01",
    "feedback_roam_10min_seed402": "015d9ba6a3705bc959cc96dd043211ba08cee67734af292b80efe4654c7a1f42",
    "feedback_roam_10min_seed403": "1cbcdc70cf21d300b94865b612c9bd5927eece57d654682ccd0a2a3b6068708e",
    "feedback_roam_10min_seed404": "c39b9772df9439d452f686c1603bd9e88acf44997597faf306f865361dedcf1a",
    "feedback_roam_10min_seed405": "29a163c3e94435b438d22b147e426ba0550e2ef90debaa40693787f5ca0b3849",
    "feedback_roam_10min_seed406": "00745f40c3c25824c3938d07cbf589d1f32c99665238259e906141b258987ffd",
    "lookdev_lighting_showcase_60s": "1a988af3be4f9404cb12fb1ea60f0f8f9afefd81b1b5707ca8b66400ada8646b",
}


def test_all_non_external_strategies_have_golden_or_compact_hash_evidence() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    expected = {name for name, spec in strategies.items() if spec.get("type") != "external"}
    golden = {path.stem for path in (ROOT / "tests" / "golden").glob("*.json")}

    assert not (golden & COMPACT_TRAJECTORY_SHA256.keys())
    assert golden | COMPACT_TRAJECTORY_SHA256.keys() == expected


def test_configured_trajectories_match_golden_bytes() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    for path in sorted((ROOT / "tests" / "golden").glob("*.json")):
        name = path.stem
        spec = strategies[name]
        trajectory = build_trajectory(
            name, dict(spec), scene_obstacles=walk_obstacles(load_scene(ROOT / "configs"))
        )
        actual = json.dumps(trajectory, indent=2, sort_keys=True) + "\n"
        expected = path.read_text(encoding="utf-8")

        assert actual == expected


def test_compact_trajectory_hash_evidence_matches() -> None:
    strategies = load_yaml(ROOT / "configs" / "actions.yml").get("strategies", {})
    obstacles = walk_obstacles(load_scene(ROOT / "configs"))

    for name, expected in COMPACT_TRAJECTORY_SHA256.items():
        trajectory = build_trajectory(name, dict(strategies[name]), scene_obstacles=obstacles)
        payload = json.dumps(trajectory, indent=2, sort_keys=True) + "\n"

        assert hashlib.sha256(payload.encode()).hexdigest() == expected
