from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcdata.render.probe import parse_entity_data_values


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    z: float
    yaw: float
    observed_mono: float
    sequence: int


class PoseSource(Protocol):
    def latest(self) -> Pose | None: ...


class ServerLogPoseSource:
    """Incrementally pair position and rotation command replies from server.log."""

    def __init__(
        self,
        path: Path,
        *,
        username: str,
        query_sent_at: list[float] | None = None,
    ) -> None:
        self._path = path
        self._username = username
        self._offset = 0
        self._carry = b""
        self._pending_position: tuple[float, float, float] | None = None
        self._latest: Pose | None = None
        self._sequence = 0
        self._query_sent_at = query_sent_at

    def latest(self) -> Pose | None:
        self._poll()
        return self._latest

    def _poll(self) -> None:
        if not self._path.exists():
            return
        size = self._path.stat().st_size
        if size < self._offset:
            self._offset = 0
            self._carry = b""
            self._pending_position = None
        with self._path.open("rb") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
        self._offset += len(chunk)
        if not chunk:
            return
        parts = (self._carry + chunk).split(b"\n")
        self._carry = parts.pop()
        for raw_line in parts:
            self._consume_line(raw_line.decode("utf-8", errors="replace"))

    def _consume_line(self, line: str) -> None:
        values = parse_entity_data_values(line, username=self._username)
        if values is None:
            return
        if len(values) == 3:
            self._pending_position = values
            return
        if len(values) != 2 or self._pending_position is None:
            return
        x, y, z = self._pending_position
        self._sequence += 1
        query_index = self._sequence - 1
        observed_mono = time.monotonic()
        if self._query_sent_at is not None and query_index < len(self._query_sent_at):
            observed_mono = self._query_sent_at[query_index]
        self._latest = Pose(
            x=x,
            y=y,
            z=z,
            yaw=values[0],
            observed_mono=observed_mono,
            sequence=self._sequence,
        )
        self._pending_position = None
