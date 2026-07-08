from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console


class RunLogger:
    def __init__(self, run_dir: Path, *, console: Console | None = None) -> None:
        self.path = run_dir / "pipeline.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._console = console

    def log(self, stage: str, event: str, **detail: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "event": event,
            **detail,
        }
        self._fh.write(json.dumps(record, sort_keys=True) + "\n")
        self._fh.flush()
        if self._console is not None:
            self._console.print(f"{stage}: {event}")

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "RunLogger":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()
