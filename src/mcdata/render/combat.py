from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from mcdata.action_combat import (
    COMBAT_FINAL_PHASES,
    COMBAT_KNOCKBACK_SCALE,
    COMBAT_RESET_PHASES,
    COMBAT_SCORE_OBJECTIVE,
    COMBAT_SCORE_SCALE,
    COMBAT_SEMANTIC,
    combat_spec,
    combat_specs,
    expected_combat_input_events,
    health_score,
    uuid_int_array,
)
from mcdata.action_placement import placement_specs, receipt_marker, server_log_binding
from mcdata.render.placement import PlacementExecutor
from mcdata.render.scene import write_commands

InputDispatch = Callable[[dict[str, Any]], bool]
_SCORE_LINE = re.compile(r"(?P<holder>#[A-Za-z0-9_]+) has (?P<value>-?\d+) \[(?P<objective>[A-Za-z0-9_]+)\]")


class CombatExecutor:
    """Run one deterministic L4 encounter on top of the complete L3 executor."""

    post_capture_control = "l4_post_capture_verification"
    level_label = "L4"

    def __init__(
        self,
        proc: subprocess.Popen,
        *,
        server_log_path: Path,
        username: str,
        poll_sec: float = 0.05,
    ) -> None:
        self._proc = proc
        self._log_path = server_log_path
        self._username = username
        self._poll_sec = poll_sec
        self._placement = PlacementExecutor(
            proc,
            server_log_path=server_log_path,
            username=username,
            poll_sec=poll_sec,
        )
        self._spec: dict[str, Any] | None = None
        self._combat_cleanup_evidence: dict[str, Any] | None = None

    def prepare(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Reset L1-L4 state, then prove the fixed entity and equipment snapshot."""
        combat = combat_specs(events)
        if len(combat) != 1:
            raise RuntimeError("L4 executor requires exactly one controlled combat encounter")
        if not placement_specs(events):
            raise RuntimeError("L4 executor requires cumulative L3 placement inputs")
        self._spec = combat[0]
        self._combat_cleanup_evidence = None
        placement_evidence = self._placement.prepare(events)
        combat_evidence = self._prepare_combat()
        return _cumulative_reset_evidence(
            events,
            placement_evidence=placement_evidence,
            combat_evidence=combat_evidence,
            log_path=self._log_path,
        )

    def dispatch(self, event: dict[str, Any], send_input: InputDispatch) -> dict[str, Any]:
        if event.get("semantic_action") != COMBAT_SEMANTIC:
            return self._placement.dispatch(event, send_input)
        spec = combat_spec(event)
        inputs = expected_combat_input_events(spec)
        if not send_input(inputs[0]):
            raise RuntimeError(f"Combat {spec['action_id']} hotbar input dispatch failed")
        time.sleep(float(spec["input_settle_sec"]))
        if not send_input(inputs[1]):
            raise RuntimeError(f"Combat {spec['action_id']} attack input dispatch failed")
        time.sleep(float(spec["attack_probe_delay_sec"]))
        write_commands(
            self._proc,
            [
                f"scoreboard players set #attacker_ok {COMBAT_SCORE_OBJECTIVE} 0",
                f"execute as {_target_selector(spec)} on attacker "
                f"if entity @s[name={self._username}] run scoreboard players set "
                f"#attacker_ok {COMBAT_SCORE_OBJECTIVE} 1",
            ],
        )
        attacker_query = self._query_score(
            "#attacker_ok",
            timeout=float(spec["receipt_timeout_sec"]),
        )
        attacker_receipt = self._probe(
            f"execute if score #attacker_ok {COMBAT_SCORE_OBJECTIVE} matches 1 "
            f"run say {{marker}}",
            str(spec["action_id"]),
            "player_attacker",
            timeout=float(spec["receipt_timeout_sec"]),
        )
        return {
            "kind": "controlled_combat_input",
            "action_id": spec["action_id"],
            "target_entity": spec["target_entity"],
            "target_tag": spec["target_tag"],
            "target_uuid": spec["target_uuid"],
            "spawn": spec["spawn"],
            "weapon": spec["weapon"],
            "hotbar_slot": spec["hotbar_slot"],
            "input_events": inputs,
            "probe_command_count": 2
            + int(attacker_query["probe_attempts"])
            + int(attacker_receipt["probe_attempts"]),
            "attacker_receipt": _public_receipt(attacker_receipt),
            "attacker_score_query": attacker_query,
            "server_log": server_log_binding(self._log_path),
        }

    def finalize(self) -> dict[str, Any]:
        """Verify every L3 mutation and L4 health delta, then clean both arenas."""
        placement_evidence = None
        placement_error: BaseException | None = None
        try:
            placement_evidence = self._placement.finalize()
        except BaseException as exc:
            placement_error = exc
        combat_evidence = None
        combat_error: BaseException | None = None
        try:
            combat_evidence = self._finalize_combat()
        except BaseException as exc:
            combat_error = exc
        if placement_error is not None or combat_error is not None:
            details = _error_details(
                (("placement", placement_error), ("combat", combat_error))
            )
            raise RuntimeError(f"L4 cumulative post-capture verification failed; {details}")
        assert placement_evidence is not None and combat_evidence is not None
        return _cumulative_final_evidence(
            placement_evidence=placement_evidence,
            combat_evidence=combat_evidence,
            log_path=self._log_path,
        )

    def cleanup_after_failure(self) -> dict[str, Any]:
        """Attempt both cleanups even when either lower-level cleanup fails."""
        combat_evidence = None
        combat_error: BaseException | None = None
        try:
            combat_evidence = self._cleanup_combat(phase="failure_cleanup_complete")
        except BaseException as exc:
            combat_error = exc
        placement_evidence = None
        placement_error: BaseException | None = None
        try:
            placement_evidence = self._placement.cleanup_after_failure()
        except BaseException as exc:
            placement_error = exc
        if combat_error is not None or placement_error is not None:
            details = _error_details(
                (("combat", combat_error), ("placement", placement_error))
            )
            raise RuntimeError(f"L4 rejected-run cleanup failed; {details}")
        assert placement_evidence is not None and combat_evidence is not None
        return {
            "kind": "l4_rejected_cleanup",
            "action_ids": [
                *placement_evidence["action_ids"],
                combat_evidence["action_id"],
            ],
            "cleanup_command_count": (
                placement_evidence["cleanup_command_count"]
                + combat_evidence["cleanup_command_count"]
            ),
            "receipt_count": (
                len(placement_evidence["receipts"])
                + len(combat_evidence["receipts"])
            ),
        }

    def _prepare_combat(self) -> dict[str, Any]:
        spec = self._require_spec()
        timeout = float(spec["receipt_timeout_sec"])
        absent = self._probe(
            f"execute unless entity {_identity_selector(spec)} "
            f"unless entity @e[tag={spec['target_tag']}] run say {{marker}}",
            str(spec["action_id"]),
            COMBAT_RESET_PHASES[0],
            timeout=timeout,
        )
        objective_created = self._objective_mutation("add", timeout=timeout)
        write_commands(
            self._proc,
            [
                _summon_command(spec),
                f"attribute {_target_selector(spec)} minecraft:knockback_resistance "
                f"base set {_number(spec['knockback_resistance'])}",
                f"item replace entity {self._username} hotbar.{int(spec['hotbar_slot']) - 1} "
                f"with {spec['weapon']} {spec['item_count']}",
                f"execute store result score #target_count {COMBAT_SCORE_OBJECTIVE} "
                f"run execute if entity {_count_selector(spec)}",
                f"execute store result score #health_before {COMBAT_SCORE_OBJECTIVE} "
                f"run data get entity {_target_selector(spec)} Health {COMBAT_SCORE_SCALE}",
                f"execute store result score #knockback {COMBAT_SCORE_OBJECTIVE} "
                f"run attribute {_target_selector(spec)} minecraft:knockback_resistance "
                f"base get {COMBAT_KNOCKBACK_SCALE}",
                f"execute store result score #mob_spawning {COMBAT_SCORE_OBJECTIVE} "
                "run gamerule spawn_mobs",
            ],
        )
        queries = [
            self._query_score("#target_count", timeout=timeout),
            self._query_score("#health_before", timeout=timeout),
            self._query_score("#knockback", timeout=timeout),
            self._query_score("#mob_spawning", timeout=timeout),
        ]
        receipts = [
            absent,
            self._probe(
                f"execute if score #mob_spawning {COMBAT_SCORE_OBJECTIVE} matches 0 "
                f"run say {{marker}}",
                str(spec["action_id"]),
                COMBAT_RESET_PHASES[1],
                timeout=timeout,
            ),
            self._probe(
                f"execute if score #target_count {COMBAT_SCORE_OBJECTIVE} matches 1 "
                f"if score #health_before {COMBAT_SCORE_OBJECTIVE} matches {health_score(spec)} "
                f"if entity {_snapshot_selector(spec)} run say {{marker}}",
                str(spec["action_id"]),
                COMBAT_RESET_PHASES[2],
                timeout=timeout,
            ),
            self._probe(
                f"execute if score #knockback {COMBAT_SCORE_OBJECTIVE} matches "
                f"{COMBAT_KNOCKBACK_SCALE} run say {{marker}}",
                str(spec["action_id"]),
                COMBAT_RESET_PHASES[3],
                timeout=timeout,
            ),
            self._probe(
                f"execute if items entity {self._username} "
                f"hotbar.{int(spec['hotbar_slot']) - 1} {spec['weapon']} run say {{marker}}",
                str(spec["action_id"]),
                COMBAT_RESET_PHASES[4],
                timeout=timeout,
            ),
        ]
        return {
            "kind": "l4_combat_reset",
            **_evidence_projection(spec),
            "initial_health_score": health_score(spec),
            "knockback_score": COMBAT_KNOCKBACK_SCALE,
            "mob_spawning_score": 0,
            "reset_command_count": 4,
            "probe_command_count": 4 + _probe_count(receipts, queries),
            "objective_created": objective_created,
            "receipts": [_public_receipt(item) for item in receipts],
            "score_queries": queries,
            "server_log": server_log_binding(self._log_path),
        }

    def _finalize_combat(self) -> dict[str, Any]:
        spec = self._require_spec()
        timeout = float(spec["receipt_timeout_sec"])
        verification_error: BaseException | None = None
        queries: list[dict[str, Any]] = []
        damage_receipt: dict[str, Any] | None = None
        try:
            write_commands(
                self._proc,
                [
                    f"execute store result score #target_count_after {COMBAT_SCORE_OBJECTIVE} "
                    f"run execute if entity {_count_selector(spec)}",
                    f"execute store result score #health_after {COMBAT_SCORE_OBJECTIVE} "
                    f"run data get entity {_target_selector(spec)} Health {COMBAT_SCORE_SCALE}",
                ],
            )
            queries = [
                self._query_score("#target_count_after", timeout=timeout),
                self._query_score("#health_after", timeout=timeout),
            ]
            damage_receipt = self._probe(
                f"execute if score #target_count_after {COMBAT_SCORE_OBJECTIVE} matches 1 "
                f"if score #health_after {COMBAT_SCORE_OBJECTIVE} matches 1.. "
                f"if score #health_after {COMBAT_SCORE_OBJECTIVE} < "
                f"#health_before {COMBAT_SCORE_OBJECTIVE} if entity {_snapshot_selector(spec)} "
                f"run say {{marker}}",
                str(spec["action_id"]),
                COMBAT_FINAL_PHASES[0],
                timeout=timeout,
            )
        except BaseException as exc:
            verification_error = exc
        cleanup_evidence = None
        cleanup_error: BaseException | None = None
        try:
            cleanup_evidence = self._cleanup_combat(phase=COMBAT_FINAL_PHASES[-1])
        except BaseException as exc:
            cleanup_error = exc
        if verification_error is not None or cleanup_error is not None:
            details = _error_details(
                (("verification", verification_error), ("cleanup", cleanup_error))
            )
            raise RuntimeError(f"L4 combat post-capture verification failed; {details}")
        assert damage_receipt is not None and cleanup_evidence is not None
        remaining = next(item["value"] for item in queries if item["holder"] == "#health_after")
        queries.extend(cleanup_evidence["score_queries"])
        receipts = [damage_receipt, *cleanup_evidence["receipts"]]
        return {
            "kind": "l4_combat_post_capture_verification",
            **_evidence_projection(spec),
            "initial_health_score": health_score(spec),
            "remaining_health_score": remaining,
            "probe_command_count": 3 + _probe_count(receipts, queries),
            "cleanup_command_count": cleanup_evidence["cleanup_command_count"],
            "objective_removed": cleanup_evidence["objective_removed"],
            "receipts": [_public_receipt(item) for item in receipts],
            "score_queries": queries,
            "server_log": server_log_binding(self._log_path),
        }

    def _cleanup_combat(self, *, phase: str) -> dict[str, Any]:
        if self._combat_cleanup_evidence is not None:
            return self._combat_cleanup_evidence
        spec = self._require_spec()
        timeout = float(spec["receipt_timeout_sec"])
        write_commands(
            self._proc,
            [
                f"clear {self._username} {spec['weapon']}",
                f"kill {_identity_selector(spec)}",
            ],
        )
        target_phase = (
            "target_removed" if phase == COMBAT_FINAL_PHASES[-1] else "failure_target_removed"
        )
        errors: list[tuple[str, BaseException | None]] = []
        target_receipt = None
        try:
            target_receipt = self._probe(
                f"execute unless entity {_identity_selector(spec)} "
                f"unless entity @e[tag={spec['target_tag']}] run say {{marker}}",
                str(spec["action_id"]),
                target_phase,
                timeout=timeout,
            )
        except BaseException as exc:
            errors.append(("target", exc))
        write_commands(
            self._proc,
            [
                "kill @e[type=minecraft:item]",
                f"execute store result score #spawn_mobs_final "
                f"{COMBAT_SCORE_OBJECTIVE} run gamerule spawn_mobs",
            ],
        )
        query = None
        try:
            query = self._query_score("#spawn_mobs_final", timeout=timeout)
        except BaseException as exc:
            errors.append(("spawn_mobs", exc))
        receipt = None
        try:
            receipt = self._probe(
                f"execute unless entity {_identity_selector(spec)} "
                f"unless entity @e[tag={spec['target_tag']}] "
                f"unless entity @e[type=minecraft:item] "
                f"unless items entity {self._username} inventory.* {spec['weapon']} "
                f"unless items entity {self._username} hotbar.* {spec['weapon']} "
                f"unless items entity {self._username} armor.* {spec['weapon']} "
                f"unless items entity {self._username} weapon.offhand {spec['weapon']} "
                f"if score #spawn_mobs_final {COMBAT_SCORE_OBJECTIVE} matches 0 "
                f"run say {{marker}}",
                str(spec["action_id"]),
                phase,
                timeout=timeout,
            )
        except BaseException as exc:
            errors.append(("arena", exc))
        objective_removed = None
        try:
            objective_removed = self._objective_mutation("remove", timeout=timeout)
        except BaseException as exc:
            errors.append(("objective", exc))
        if errors:
            raise RuntimeError("L4 combat cleanup failed; " + _error_details(tuple(errors)))
        assert target_receipt is not None
        assert query is not None
        assert receipt is not None
        assert objective_removed is not None
        evidence = {
            "action_id": spec["action_id"],
            "cleanup_command_count": 4,
            "receipts": [target_receipt, receipt],
            "score_queries": [query],
            "objective_removed": objective_removed,
        }
        self._combat_cleanup_evidence = evidence
        return evidence

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
            self._ensure_server(action_id, phase)
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
            f"Timed out waiting for combat receipt {action_id}/{phase}; see {self._log_path}"
        )

    def _query_score(self, holder: str, *, timeout: float) -> dict[str, Any]:
        after_byte = self._log_path.stat().st_size if self._log_path.exists() else 0
        deadline = time.monotonic() + timeout
        attempts = 0
        while time.monotonic() <= deadline:
            self._ensure_server(holder, "score_query")
            attempts += 1
            write_commands(
                self._proc,
                [f"scoreboard players get {holder} {COMBAT_SCORE_OBJECTIVE}"],
            )
            result = _find_score_line(
                self._log_path,
                holder=holder,
                objective=COMBAT_SCORE_OBJECTIVE,
                after_byte=after_byte,
            )
            if result is not None:
                value, line = result
                return {
                    "holder": holder,
                    "objective": COMBAT_SCORE_OBJECTIVE,
                    "value": value,
                    "line": line,
                    "probe_attempts": attempts,
                }
            time.sleep(self._poll_sec)
        raise TimeoutError(
            f"Timed out reading combat score {holder}/{COMBAT_SCORE_OBJECTIVE}; "
            f"see {self._log_path}"
        )

    def _objective_mutation(self, action: str, *, timeout: float) -> dict[str, Any]:
        expected = {
            "add": f"Created new objective [{COMBAT_SCORE_OBJECTIVE}]",
            "remove": f"Removed objective [{COMBAT_SCORE_OBJECTIVE}]",
        }
        if action not in expected:
            raise ValueError(f"Unknown scoreboard objective mutation: {action}")
        after_byte = self._log_path.stat().st_size if self._log_path.exists() else 0
        command = f"scoreboard objectives {action} {COMBAT_SCORE_OBJECTIVE}"
        if action == "add":
            command += " dummy"
        write_commands(self._proc, [command])
        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            self._ensure_server(COMBAT_SCORE_OBJECTIVE, f"objective_{action}")
            line = _find_text_line(
                self._log_path,
                expected[action],
                after_byte=after_byte,
            )
            if line is not None:
                return {"objective": COMBAT_SCORE_OBJECTIVE, "line": line}
            time.sleep(self._poll_sec)
        raise TimeoutError(
            f"Timed out waiting for scoreboard objective {action}; see {self._log_path}"
        )

    def _ensure_server(self, action_id: str, phase: str) -> None:
        if self._proc.poll() is not None:
            raise RuntimeError(
                f"Minecraft server exited while probing combat {action_id}/{phase}; "
                f"see {self._log_path}"
            )

    def _require_spec(self) -> dict[str, Any]:
        if self._spec is None:
            raise RuntimeError("L4 combat executor was not prepared")
        return self._spec


def _cumulative_reset_evidence(
    events: list[dict[str, Any]],
    *,
    placement_evidence: dict[str, Any],
    combat_evidence: dict[str, Any],
    log_path: Path,
) -> dict[str, Any]:
    action_ids = [
        *(spec["action_id"] for spec in placement_specs(events)),
        combat_evidence["action_id"],
    ]
    return {
        "kind": "l4_cumulative_episode_reset",
        "action_ids": action_ids,
        "reset_command_count": (
            placement_evidence["reset_command_count"]
            + combat_evidence["reset_command_count"]
        ),
        "probe_command_count": (
            placement_evidence["probe_command_count"]
            + combat_evidence["probe_command_count"]
        ),
        "placement": placement_evidence,
        "combat": combat_evidence,
        "server_log": server_log_binding(log_path),
    }


def _cumulative_final_evidence(
    *,
    placement_evidence: dict[str, Any],
    combat_evidence: dict[str, Any],
    log_path: Path,
) -> dict[str, Any]:
    return {
        "kind": "l4_cumulative_post_capture_verification",
        "action_ids": [
            *placement_evidence["action_ids"],
            combat_evidence["action_id"],
        ],
        "probe_command_count": (
            placement_evidence["probe_command_count"]
            + combat_evidence["probe_command_count"]
        ),
        "cleanup_command_count": (
            placement_evidence["cleanup_command_count"]
            + combat_evidence["cleanup_command_count"]
        ),
        "placement": placement_evidence,
        "combat": combat_evidence,
        "server_log": server_log_binding(log_path),
    }


def _snapshot_selector(spec: dict[str, Any]) -> str:
    spawn = spec["spawn"]
    rotation = spec["rotation"]
    nbt = (
        "{UUID:"
        f"{_uuid_snbt(spec['target_uuid'])},NoAI:1b,PersistenceRequired:1b,"
        "Silent:1b,Invulnerable:0b,"
        f"Rotation:[{_float_snbt(rotation[0])},{_float_snbt(rotation[1])}]"
        "}"
    )
    return (
        f"@e[type={spec['target_entity']},tag={spec['target_tag']},limit=1,"
        f"x={_number(spawn[0])},y={_number(spawn[1])},z={_number(spawn[2])},"
        f"distance=..0.25,nbt={nbt}]"
    )


def _target_selector(spec: dict[str, Any]) -> str:
    return (
        f"@e[type={spec['target_entity']},tag={spec['target_tag']},limit=1,"
        f"nbt={{UUID:{_uuid_snbt(spec['target_uuid'])}}}]"
    )


def _count_selector(spec: dict[str, Any]) -> str:
    return f"@e[tag={spec['target_tag']}]"


def _identity_selector(spec: dict[str, Any]) -> str:
    return f"@e[nbt={{UUID:{_uuid_snbt(spec['target_uuid'])}}}]"


def _summon_command(spec: dict[str, Any]) -> str:
    spawn = " ".join(_number(item) for item in spec["spawn"])
    rotation = spec["rotation"]
    nbt = (
        "{UUID:"
        f"{_uuid_snbt(spec['target_uuid'])},Tags:[\"{spec['target_tag']}\"],"
        "NoAI:1b,PersistenceRequired:1b,Silent:1b,Invulnerable:0b,"
        f"Health:{_float_snbt(spec['initial_health'])},"
        f"Rotation:[{_float_snbt(rotation[0])},{_float_snbt(rotation[1])}],"
        "Motion:[0.0d,0.0d,0.0d]}"
    )
    return f"summon {spec['target_entity']} {spawn} {nbt}"


def _uuid_snbt(value: str) -> str:
    return "[I;" + ",".join(str(item) for item in uuid_int_array(value)) + "]"


def _float_snbt(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number)}.0f"
    return f"{number:.6f}".rstrip("0") + "f"


def _number(value: Any) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _evidence_projection(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        key: spec[key]
        for key in (
            "action_id",
            "target_entity",
            "target_tag",
            "target_uuid",
            "spawn",
            "rotation",
            "weapon",
            "hotbar_slot",
        )
    }


def _find_marker_line(log_path: Path, marker: str, *, after_byte: int) -> str | None:
    for line in reversed(_new_log_lines(log_path, after_byte=after_byte)):
        if f"[Server] {marker}" in line:
            return line
    return None


def _find_score_line(
    log_path: Path,
    *,
    holder: str,
    objective: str,
    after_byte: int,
) -> tuple[int, str] | None:
    for line in reversed(_new_log_lines(log_path, after_byte=after_byte)):
        match = _SCORE_LINE.search(line)
        if match and match.group("holder") == holder and match.group("objective") == objective:
            return int(match.group("value")), line
    return None


def _find_text_line(log_path: Path, text: str, *, after_byte: int) -> str | None:
    for line in reversed(_new_log_lines(log_path, after_byte=after_byte)):
        if text in line:
            return line
    return None


def _new_log_lines(log_path: Path, *, after_byte: int) -> list[str]:
    if not log_path.exists():
        return []
    payload = log_path.read_bytes()
    if after_byte < 0 or after_byte > len(payload):
        return []
    return payload[after_byte:].decode("utf-8", errors="replace").splitlines()


def _public_receipt(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in ("phase", "marker", "line")}


def _probe_count(
    receipts: list[dict[str, Any]],
    queries: list[dict[str, Any]],
) -> int:
    return sum(int(item["probe_attempts"]) for item in [*receipts, *queries])


def _error_details(
    values: tuple[tuple[str, BaseException | None], ...],
) -> str:
    return "; ".join(
        f"{label}: {type(error).__name__}: {error}"
        for label, error in values
        if error is not None
    )
