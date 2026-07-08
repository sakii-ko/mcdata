import json
from pathlib import Path

from mcdata.render.pipeline import _copy_trajectory


def test_copy_trajectory_uses_final_path_without_tmp_leftover(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"events": [{"t": 0.0}]}) + "\n", encoding="utf-8")
    run_dir = tmp_path / "run"

    copied = _copy_trajectory(run_dir, source)

    assert copied == run_dir / "trajectory.json"
    assert copied.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not (run_dir / "trajectory.json.tmp").exists()
