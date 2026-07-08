import json
from pathlib import Path

from mcdata.runlog import RunLogger


def test_run_logger_writes_jsonl(tmp_path: Path) -> None:
    with RunLogger(tmp_path) as logger:
        logger.log("launch", "command", cmd=["mcdata", "run"])

    lines = (tmp_path / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["stage"] == "launch"
    assert record["event"] == "command"
    assert record["cmd"] == ["mcdata", "run"]
    assert record["ts"]
