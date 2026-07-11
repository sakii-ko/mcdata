from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mcdata.action_trace import (
    ActionTraceError,
    _clean_float,
    _derive_edge_events,
    _finite_number,
    _mapping,
    _raw_tick,
    _scalar,
)

ADAPTER_VERSION = 1

_MINESTUDIO_KEYS = {
    "forward": "forward",
    "back": "back",
    "left": "left",
    "right": "right",
    "jump": "jump",
    "sneak": "sneak",
    "sprint": "sprint",
}
_VPT_KEYBOARD_KEYS = {
    "key.keyboard.w": "forward",
    "key.keyboard.s": "back",
    "key.keyboard.a": "left",
    "key.keyboard.d": "right",
    "key.keyboard.space": "jump",
    "key.keyboard.left.shift": "sneak",
    "key.keyboard.left.control": "sprint",
}
_VPT_IMPULSE_KEYS = {
    "key.keyboard.e",
    *(f"key.keyboard.{slot}" for slot in range(1, 10)),
}
_SOLARIS_HELD_KEYS = (
    "forward",
    "back",
    "left",
    "right",
    "jump",
    "sneak",
    "sprint",
)
_SOLARIS_ANNOTATION_ONLY_ACTIONS = {
    "mount",
    "dismount",
    "mine",
    "place_entity",
    "place_block_success",
}


class MineStudioVPTEnvAdapter:
    """Convert MineStudio's decoded ``action_type='env'`` dictionaries.

    Hierarchical ``buttons``/``camera`` VPT output is deliberately rejected: it must first be
    decoded by the matching MineStudio action mapper, whose version/config belongs in provenance.
    """

    action_source = "learned_visual_policy"

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": "minestudio_vpt_env",
            "version": ADAPTER_VERSION,
            "source_format": "minestudio_env_action_v1",
            "parameters": {"camera_order": ["pitch", "yaw"], "hotbar_index_base": 1},
        }

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        raw_ticks = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise ActionTraceError(f"MineStudio action {index} is not a mapping")
            if "buttons" in record:
                raise ActionTraceError(
                    "hierarchical MineStudio/VPT action requires version-matched "
                    "agent_action_to_env_action decoding before import"
                )
            _reject_unknown_minestudio_controls(record, index)
            if _active(record.get("drop", 0), f"action {index}.drop"):
                raise ActionTraceError(
                    f"MineStudio action {index}.drop cannot be represented by canonical trace v1"
                )
            held = [
                native
                for source, native in _MINESTUDIO_KEYS.items()
                if _active(record.get(source, 0), f"action {index}.{source}")
            ]
            raw_ticks.append(
                _raw_tick(
                    index=index,
                    source_tick=_optional_source_tick(record, index),
                    held=held,
                    attack=_active(record.get("attack", 0), f"action {index}.attack"),
                    use=_active(record.get("use", 0), f"action {index}.use"),
                    inventory=_active(
                        record.get("inventory", 0), f"action {index}.inventory"
                    ),
                    hotbar=_minestudio_hotbar(record, index),
                    camera=_camera_pair(record.get("camera"), f"action {index}.camera"),
                    annotations=_annotations(record, index, semantic_annotations),
                )
            )
        return _derive_edge_events(raw_ticks)


class OpenAIVPTRecorderV7Adapter:
    """Convert OpenAI VPT 7.x contractor action dictionaries without replaying GUI input."""

    action_source = "human_demo"

    def __init__(self, *, hotbar_index_base: int) -> None:
        if hotbar_index_base not in {0, 1}:
            raise ActionTraceError("VPT recorder hotbar_index_base must be explicitly 0 or 1")
        self.hotbar_index_base = hotbar_index_base

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": "openai_vpt_recorder_v7",
            "version": ADAPTER_VERSION,
            "source_format": "openai_vpt_recorder_7x_jsonl",
            "parameters": {
                "camera_source": "absolute_yaw_pitch_delta",
                "hotbar_index_base": self.hotbar_index_base,
                "mouse_button_map": {"0": "attack", "1": "use"},
            },
        }

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        raw_ticks: list[dict[str, Any]] = []
        previous_yaw: float | None = None
        previous_pitch: float | None = None
        previous_hotbar: int | None = None
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise ActionTraceError(f"VPT recorder action {index} is not a mapping")
            if record.get("isGuiOpen") is True:
                raise ActionTraceError(
                    f"VPT recorder action {index} opens a GUI, which this native replay spike "
                    "cannot preserve"
                )
            keyboard = _mapping(record.get("keyboard"), f"action {index}.keyboard")
            source_keys = _string_sequence(keyboard.get("keys", []), f"action {index}.keys")
            new_keys = _string_sequence(
                keyboard.get("newKeys", []), f"action {index}.newKeys"
            )
            if not set(new_keys) <= set(source_keys):
                raise ActionTraceError(
                    f"VPT recorder action {index}.newKeys are not present in held keys"
                )
            unsupported_keys = sorted(
                (set(source_keys) | set(new_keys))
                - set(_VPT_KEYBOARD_KEYS)
                - _VPT_IMPULSE_KEYS
            )
            if unsupported_keys:
                raise ActionTraceError(
                    f"VPT recorder action {index} has unsupported held keys {unsupported_keys!r}"
                )
            held = [_VPT_KEYBOARD_KEYS[key] for key in source_keys if key in _VPT_KEYBOARD_KEYS]
            mouse = _mapping(record.get("mouse"), f"action {index}.mouse")
            buttons = _mouse_buttons(mouse.get("buttons", []), index)
            yaw = _finite_number(record.get("yaw"), f"action {index}.yaw")
            pitch = _finite_number(record.get("pitch"), f"action {index}.pitch")
            camera = (
                {"pitch": 0.0, "yaw": 0.0}
                if previous_yaw is None or previous_pitch is None
                else {
                    "pitch": _clean_float(pitch - previous_pitch),
                    "yaw": _clean_float(_shortest_degrees(yaw - previous_yaw)),
                }
            )
            previous_yaw, previous_pitch = yaw, pitch
            current_hotbar = _vpt_hotbar(record.get("hotbar"), self.hotbar_index_base, index)
            hotbar = current_hotbar if current_hotbar != previous_hotbar else None
            previous_hotbar = current_hotbar
            raw_ticks.append(
                _raw_tick(
                    index=index,
                    source_tick=_optional_source_tick(record, index, field="tick"),
                    held=held,
                    attack=0 in buttons,
                    use=1 in buttons,
                    inventory="key.keyboard.e" in new_keys,
                    hotbar=hotbar,
                    camera=camera,
                    annotations=_annotations(record, index, semantic_annotations),
                )
            )
        return _derive_edge_events(raw_ticks)


class SolarisNormalizedControllerAdapter:
    """Convert a future, normalized Solaris controller-boundary artifact.

    The adapter intentionally does *not* consume Solaris' current render-sampled action JSON.
    Inputs must already be contiguous 20 Hz records with Minecraft-input degree deltas.  Semantic
    success receipts and canonical-v1-inexpressible actions stay annotations; they never synthesize
    a ``use``/``attack`` input or a curriculum level.
    """

    action_source = "scripted_skill_agent"

    def descriptor(self) -> dict[str, Any]:
        return {
            "name": "solaris_normalized_controller",
            "version": ADAPTER_VERSION,
            "source_format": "solaris_normalized_controller_boundary_v1",
            "parameters": {
                "camera_convention": "minecraft_input_degrees_yaw_right_pitch_down",
                "attack_use_semantics": "single_tick_impulse",
                "hotbar_index_base": 1,
                "unsupported_action_policy": "explicit_annotation_only_or_reject",
            },
        }

    def adapt(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        semantic_annotations: Mapping[int, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        raw_ticks: list[dict[str, Any]] = []
        previous_impulses = {"attack": False, "use": False}
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise ActionTraceError(f"Solaris action {index} is not a mapping")
            expected = {
                "tick",
                "held",
                "camera_delta_degrees",
                "attack",
                "use",
                "hotbar",
                "semantic_annotations",
                "annotation_only_events",
            }
            if set(record) != expected:
                raise ActionTraceError(
                    f"Solaris action {index} has an unstable normalized field set"
                )
            source_tick = _optional_source_tick(record, index, field="tick")
            if source_tick != index:
                raise ActionTraceError(
                    "Solaris normalized ticks must be contiguous and zero-based"
                )
            held_map = _mapping(record["held"], f"action {index}.held")
            if set(held_map) != set(_SOLARIS_HELD_KEYS):
                raise ActionTraceError(
                    f"Solaris action {index}.held must name every canonical held control"
                )
            held = [
                key
                for key in _SOLARIS_HELD_KEYS
                if _strict_bool(held_map[key], f"action {index}.held.{key}")
            ]
            attack = _strict_bool(record["attack"], f"action {index}.attack")
            use = _strict_bool(record["use"], f"action {index}.use")
            for control, active in (("attack", attack), ("use", use)):
                if active and previous_impulses[control]:
                    raise ActionTraceError(
                        f"Solaris action {index} repeats one-shot {control} on consecutive ticks; "
                        "canonical trace v1 cannot distinguish repeated impulses"
                    )
                previous_impulses[control] = active
            annotations = _solaris_annotations(record, index, semantic_annotations)
            raw_ticks.append(
                _raw_tick(
                    index=index,
                    source_tick=source_tick,
                    held=held,
                    attack=attack,
                    use=use,
                    inventory=False,
                    hotbar=_solaris_hotbar(record["hotbar"], index),
                    camera=_solaris_camera(record["camera_delta_degrees"], index),
                    annotations=annotations,
                )
            )
        return _derive_edge_events(raw_ticks)


def _reject_unknown_minestudio_controls(record: Mapping[str, Any], index: int) -> None:
    allowed = {
        *_MINESTUDIO_KEYS,
        "attack",
        "use",
        "inventory",
        "drop",
        "camera",
        "source_tick",
        "semantic_annotations",
        *(f"hotbar.{slot}" for slot in range(1, 10)),
    }
    unknown = sorted(set(record) - allowed)
    if unknown:
        raise ActionTraceError(
            f"MineStudio action {index} uses fields outside env-action v1: {unknown!r}"
        )


def _minestudio_hotbar(record: Mapping[str, Any], index: int) -> int | None:
    selected = [
        slot
        for slot in range(1, 10)
        if _active(record.get(f"hotbar.{slot}", 0), f"action {index}.hotbar.{slot}")
    ]
    if len(selected) > 1:
        raise ActionTraceError(f"MineStudio action {index} selects multiple hotbar slots")
    return selected[0] if selected else None


def _solaris_camera(value: Any, index: int) -> dict[str, float]:
    camera = _mapping(value, f"action {index}.camera_delta_degrees")
    if set(camera) != {"pitch", "yaw"}:
        raise ActionTraceError(
            f"Solaris action {index}.camera_delta_degrees must contain pitch and yaw"
        )
    return {
        "pitch": _clean_float(
            _finite_number(camera["pitch"], f"action {index}.camera.pitch")
        ),
        "yaw": _clean_float(
            _finite_number(camera["yaw"], f"action {index}.camera.yaw")
        ),
    }


def _solaris_hotbar(value: Any, index: int) -> int | None:
    value = _scalar(value)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 9:
        raise ActionTraceError(f"Solaris action {index}.hotbar must be null or 1..9")
    return value


def _solaris_annotations(
    record: Mapping[str, Any],
    index: int,
    sidecar: Mapping[int, Sequence[str]] | None,
) -> list[str]:
    source_annotations = record["semantic_annotations"]
    if (
        not isinstance(source_annotations, list)
        or source_annotations != sorted(set(source_annotations))
        or any(not isinstance(item, str) or not item for item in source_annotations)
    ):
        raise ActionTraceError(
            f"Solaris action {index} semantic annotations are not canonical"
        )
    annotations = _annotations(record, index, sidecar)
    events = record["annotation_only_events"]
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ActionTraceError(
            f"Solaris action {index}.annotation_only_events must be a sequence"
        )
    rendered: list[str] = []
    canonical_keys: list[tuple[str, str]] = []
    for event_index, event in enumerate(events):
        if not isinstance(event, Mapping) or set(event) != {
            "action",
            "mode",
            "receipt_id",
        }:
            raise ActionTraceError(
                f"Solaris action {index} annotation-only event {event_index} has an "
                "unstable field set"
            )
        action = event["action"]
        if action not in _SOLARIS_ANNOTATION_ONLY_ACTIONS:
            raise ActionTraceError(
                f"Solaris action {index} has unsupported semantic action {action!r}"
            )
        if event["mode"] != "annotation_only":
            raise ActionTraceError(
                f"Solaris semantic action {action!r} must be annotation_only"
            )
        receipt_id = event["receipt_id"]
        if (
            not isinstance(receipt_id, str)
            or not receipt_id
            or any(not (character.isalnum() or character in "._-") for character in receipt_id)
        ):
            raise ActionTraceError(
                f"Solaris action {index} annotation receipt_id is not canonical"
            )
        canonical_keys.append((str(action), receipt_id))
        rendered.append(f"solaris_annotation_only:{action}:{receipt_id}")
    if canonical_keys != sorted(set(canonical_keys)):
        raise ActionTraceError(
            f"Solaris action {index} annotation-only events are not canonical"
        )
    return sorted(set([*annotations, *rendered]))


def _camera_pair(value: Any, label: str) -> dict[str, float]:
    sequence = _plain_sequence(value, label)
    if len(sequence) != 2:
        raise ActionTraceError(f"{label} must contain [pitch_delta_deg, yaw_delta_deg]")
    return {
        "pitch": _clean_float(_finite_number(sequence[0], f"{label}[0]")),
        "yaw": _clean_float(_finite_number(sequence[1], f"{label}[1]")),
    }


def _annotations(
    record: Mapping[str, Any],
    index: int,
    sidecar: Mapping[int, Sequence[str]] | None,
) -> list[str]:
    values: list[Any] = []
    record_values = record.get("semantic_annotations", [])
    if not isinstance(record_values, Sequence) or isinstance(record_values, (str, bytes)):
        raise ActionTraceError(f"action {index}.semantic_annotations must be a sequence")
    values.extend(record_values)
    if sidecar and index in sidecar:
        sidecar_values = sidecar[index]
        if not isinstance(sidecar_values, Sequence) or isinstance(sidecar_values, (str, bytes)):
            raise ActionTraceError(f"semantic annotation sidecar {index} must be a sequence")
        values.extend(sidecar_values)
    if any(not isinstance(item, str) or not item for item in values):
        raise ActionTraceError(f"action {index} semantic annotations must be non-empty strings")
    return sorted(set(values))


def _optional_source_tick(
    record: Mapping[str, Any], index: int, *, field: str = "source_tick"
) -> int:
    value = _scalar(record.get(field, index))
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ActionTraceError(f"action {index}.{field} must be a nonnegative integer")
    return value


def _vpt_hotbar(value: Any, index_base: int, index: int) -> int:
    value = _scalar(value)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ActionTraceError(f"VPT recorder action {index}.hotbar must be an integer")
    slot = value + 1 if index_base == 0 else value
    if not 1 <= slot <= 9:
        raise ActionTraceError(f"VPT recorder action {index}.hotbar is outside slots 1..9")
    return slot


def _mouse_buttons(value: Any, index: int) -> set[int]:
    buttons = set()
    for raw in _plain_sequence(value, f"action {index}.mouse.buttons"):
        raw = _scalar(raw)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw not in {0, 1}:
            raise ActionTraceError(
                f"VPT recorder action {index} has unsupported mouse button {raw!r}"
            )
        buttons.add(raw)
    return buttons


def _active(value: Any, label: str) -> bool:
    value = _scalar(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ActionTraceError(f"{label} must be a binary scalar")


def _strict_bool(value: Any, label: str) -> bool:
    value = _scalar(value)
    if not isinstance(value, bool):
        raise ActionTraceError(f"{label} must be boolean")
    return value


def _plain_sequence(value: Any, label: str) -> list[Any]:
    if hasattr(value, "tolist") and callable(value.tolist):
        value = value.tolist()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ActionTraceError(f"{label} must be a sequence")
    return list(value)


def _string_sequence(value: Any, label: str) -> list[str]:
    values = _plain_sequence(value, label)
    if any(not isinstance(item, str) for item in values):
        raise ActionTraceError(f"{label} must contain only strings")
    return values


def _shortest_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0
