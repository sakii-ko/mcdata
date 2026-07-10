from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from mcdata.action_placement import (
    EPISODE_RESET_BASE_PHASES,
    PLACEMENT_RECEIPT_PHASES,
    expected_input_events,
    placement_spec,
    placement_specs,
    receipt_marker,
    server_log_binding,
)
from mcdata.render.scene import write_commands

InputDispatch = Callable[[dict[str, Any]], bool]
_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class PlacementExecutor:
    """Execute L3 inputs and bind pre/post-capture world probes to server.log."""

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        server_log_path: Path,
        username: str,
        poll_sec: float = 0.05,
    ) -> None:
        if not _USERNAME.fullmatch(username):
            raise ValueError(f"Unsafe Minecraft username for placement executor: {username!r}")
        self._proc = proc
        self._log_path = server_log_path
        self._username = username
        self._poll_sec = poll_sec
        self._specs: list[dict[str, Any]] = []

    def prepare(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Reset and provision the complete action arena before capture starts."""
        self._specs = placement_specs(events)
        reset_commands = [
            f"clear {self._username}",
            "kill @e[type=minecraft:item]",
            "kill @e[type=!minecraft:player]",
        ]
        write_commands(self._proc, reset_commands)
        receipts = [
            self._probe(
                f"execute unless items entity {self._username} inventory.* * "
                f"unless items entity {self._username} hotbar.* * "
                f"unless items entity {self._username} armor.* * "
                f"unless items entity {self._username} weapon.offhand * run say {{marker}}",
                "episode_reset",
                EPISODE_RESET_BASE_PHASES[0],
                timeout=self._max_timeout(),
            ),
            self._probe(
                "execute unless entity @e[type=minecraft:item] run say {marker}",
                "episode_reset",
                EPISODE_RESET_BASE_PHASES[1],
                timeout=self._max_timeout(),
            ),
            self._probe(
                "execute unless entity @e[type=!minecraft:player] run say {marker}",
                "episode_reset",
                EPISODE_RESET_BASE_PHASES[2],
                timeout=self._max_timeout(),
            ),
        ]
        for spec in self._specs:
            write_commands(
                self._proc,
                [
                    f"setblock {_coords(spec['target'])} minecraft:air",
                    f"setblock {_coords(spec['support'])} {spec['support_block']}",
                ],
            )
            phase = f"arena_{spec['action_id']}"
            receipts.append(
                self._probe(
                    "execute if block "
                    f"{_coords(spec['target'])} minecraft:air if block "
                    f"{_coords(spec['support'])} {spec['support_block']} run say {{marker}}",
                    "episode_reset",
                    phase,
                    timeout=float(spec["receipt_timeout_sec"]),
                )
            )
        for spec in self._specs:
            slot = int(spec["hotbar_slot"]) - 1
            write_commands(
                self._proc,
                [
                    f"item replace entity {self._username} hotbar.{slot} with "
                    f"{spec['block']} {spec['item_count']}"
                ],
            )
            phase = f"inventory_{spec['action_id']}"
            receipts.append(
                self._probe(
                    f"execute if items entity {self._username} hotbar.{slot} "
                    f"{spec['block']} run say {{marker}}",
                    "episode_reset",
                    phase,
                    timeout=float(spec["receipt_timeout_sec"]),
                )
            )
        return {
            "kind": "l3_episode_reset",
            "action_ids": [spec["action_id"] for spec in self._specs],
            "reset_command_count": 3 + 3 * len(self._specs),
            "probe_command_count": sum(item["probe_attempts"] for item in receipts),
            "receipts": [_public_receipt(item) for item in receipts],
            "server_log": server_log_binding(self._log_path),
        }

    def dispatch(self, event: dict[str, Any], send_input: InputDispatch) -> dict[str, Any]:
        """Send only the declared hotbar key and real use-button click during capture."""
        spec = placement_spec(event)
        inputs = expected_input_events(spec)
        if not send_input(inputs[0]):
            raise RuntimeError(f"Placement {spec['action_id']} hotbar input dispatch failed")
        time.sleep(float(spec["input_settle_sec"]))
        if not send_input(inputs[1]):
            raise RuntimeError(f"Placement {spec['action_id']} use-button dispatch failed")
        return {
            "kind": "deterministic_block_placement_input",
            "action_id": spec["action_id"],
            "block": spec["block"],
            "hotbar_slot": spec["hotbar_slot"],
            "target": spec["target"],
            "support": spec["support"],
            "face": spec["face"],
            "input_events": inputs,
        }

    def finalize(self) -> dict[str, Any]:
        """After capture, verify every result and then restore arena and inventory."""
        placements: list[dict[str, Any]] = []
        probe_count = 0
        verification_error: BaseException | None = None
        try:
            for spec in self._specs:
                receipt = self._probe(
                    f"execute if block {_coords(spec['target'])} {spec['block']} run say {{marker}}",
                    str(spec["action_id"]),
                    PLACEMENT_RECEIPT_PHASES[0],
                    timeout=float(spec["receipt_timeout_sec"]),
                )
                probe_count += int(receipt["probe_attempts"])
                placements.append(
                    {
                        "action_id": spec["action_id"],
                        "block": spec["block"],
                        "target": spec["target"],
                        "support": spec["support"],
                        "face": spec["face"],
                        "receipts": [_public_receipt(receipt)],
                    }
                )
        except BaseException as exc:
            verification_error = exc

        cleanup_error: BaseException | None = None
        try:
            by_id = {item["action_id"]: item for item in placements}
            for spec in self._specs:
                write_commands(
                    self._proc,
                    [
                        f"clear {self._username} {spec['block']}",
                        f"setblock {_coords(spec['target'])} minecraft:air",
                        f"setblock {_coords(spec['support'])} minecraft:air",
                    ],
                )
                slot = int(spec["hotbar_slot"]) - 1
                receipt = self._probe(
                    "execute if block "
                    f"{_coords(spec['target'])} minecraft:air if block "
                    f"{_coords(spec['support'])} minecraft:air unless items entity "
                    f"{self._username} hotbar.{slot} {spec['block']} run say {{marker}}",
                    str(spec["action_id"]),
                    PLACEMENT_RECEIPT_PHASES[1],
                    timeout=float(spec["receipt_timeout_sec"]),
                )
                probe_count += int(receipt["probe_attempts"])
                if spec["action_id"] in by_id:
                    by_id[spec["action_id"]]["receipts"].append(_public_receipt(receipt))
        except BaseException as exc:
            cleanup_error = exc

        if verification_error is not None or cleanup_error is not None:
            details = "; ".join(
                f"{label}: {type(error).__name__}: {error}"
                for label, error in (
                    ("verification", verification_error),
                    ("cleanup", cleanup_error),
                )
                if error is not None
            )
            raise RuntimeError(f"L3 post-capture verification failed; {details}")
        return {
            "kind": "l3_post_capture_verification",
            "action_ids": [spec["action_id"] for spec in self._specs],
            "probe_command_count": probe_count,
            "cleanup_command_count": 3 * len(self._specs),
            "placements": placements,
            "server_log": server_log_binding(self._log_path),
        }

    def cleanup_after_failure(self) -> dict[str, Any]:
        """Best-effort reset for rejected runs; every arena is attempted before raising."""
        receipts = []
        errors = []
        for spec in self._specs:
            write_commands(
                self._proc,
                [
                    f"clear {self._username} {spec['block']}",
                    f"setblock {_coords(spec['target'])} minecraft:air",
                    f"setblock {_coords(spec['support'])} minecraft:air",
                ],
            )
            slot = int(spec["hotbar_slot"]) - 1
            try:
                receipts.append(
                    self._probe(
                        "execute if block "
                        f"{_coords(spec['target'])} minecraft:air if block "
                        f"{_coords(spec['support'])} minecraft:air unless items entity "
                        f"{self._username} hotbar.{slot} {spec['block']} run say {{marker}}",
                        str(spec["action_id"]),
                        "failure_cleanup_complete",
                        timeout=float(spec["receipt_timeout_sec"]),
                    )
                )
            except Exception as exc:
                errors.append(f"{spec['action_id']}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("L3 rejected-run cleanup failed; " + "; ".join(errors))
        return {
            "action_ids": [spec["action_id"] for spec in self._specs],
            "cleanup_command_count": 3 * len(self._specs),
            "receipts": [_public_receipt(item) for item in receipts],
        }

    def _probe(
        self,
        command: str,
        action_id: str,
        phase: str,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        marker = receipt_marker(action_id, phase)
        after_byte = self._log_path.stat().st_size if self._log_path.exists() else 0
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() <= deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"Minecraft server exited while probing placement {action_id}/{phase}; "
                    f"see {self._log_path}"
                )
            attempts += 1
            write_commands(self._proc, [command.format(marker=marker)])
            line = _find_marker_line(self._log_path, marker, after_byte=after_byte)
            if line is not None:
                return {
                    "phase": phase,
                    "marker": marker,
                    "line": line,
                    "probe_attempts": attempts,
                }
            time.sleep(self._poll_sec)
        raise TimeoutError(
            f"Timed out waiting for placement receipt {action_id}/{phase}; see {self._log_path}"
        )

    def _max_timeout(self) -> float:
        return max(float(spec["receipt_timeout_sec"]) for spec in self._specs)


def _find_marker_line(log_path: Path, marker: str, *, after_byte: int = 0) -> str | None:
    if not log_path.exists():
        return None
    payload = log_path.read_bytes()
    if after_byte < 0 or after_byte > len(payload):
        return None
    text = payload[after_byte:].decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        if f"[Server] {marker}" in line:
            return line
    return None


def _public_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("phase", "marker", "line")}


def _coords(value: list[int]) -> str:
    return " ".join(str(item) for item in value)
