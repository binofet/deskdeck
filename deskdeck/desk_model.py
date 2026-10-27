"""A bounded user-authored desk plan; no discovery, activation or file I/O.

Display ownership/placement and keyboard/mouse ownership are separate facts.
Coordinates describe a unit desk canvas, x right and y down, not live OS pixels.
"""
from __future__ import annotations

import copy
import json
import math
import re
import unicodedata

MAX_MODEL_BYTES = 128 * 1024
MAX_COMPUTERS = 16
MAX_DISPLAYS = 16
DELL_INPUTS = frozenset({"Thunderbolt", "DP1", "DP2", "HDMI1", "HDMI2", "USB-C"})
USB_UPSTREAMS = frozenset({"Thunderbolt", "USB2", "USB3", "USB4"})
ANCHORS = frozenset({"left", "right", "above", "below", "dell", "unplaced"})


class DeskModelError(ValueError):
    pass


def _fields(value, required, optional, label):
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - optional:
        raise DeskModelError(f"Invalid {label} fields")


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value):
        raise DeskModelError("IDs must be lowercase local identifiers of at most 32 characters")
    return value


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 64 or value != value.strip()
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise DeskModelError("Names must contain 1–64 printable characters without surrounding whitespace")
    return value


def _optional_port(value, allowed):
    if value is not None and (not isinstance(value, str) or value not in allowed):
        raise DeskModelError("Unrecognized planned monitor port")
    return value


def _placement(value):
    if value is None:
        return None
    _fields(value, {"x", "y", "width", "height"}, set(), "display placement")
    if any(type(number) not in (int, float) or not 0 <= number <= 1 or not math.isfinite(number)
           for number in value.values()):
        raise DeskModelError("Placement values must be finite normalized numbers")
    if (value["width"] <= 0 or value["height"] <= 0 or value["x"] + value["width"] > 1
            or value["y"] + value["height"] > 1):
        raise DeskModelError("Display placement must fit inside the unit desk canvas")
    return dict(value)


def validate_desk_model(value) -> dict:
    """Return a fresh canonical plan, preserving human names exactly.

    Computers can have no displays and can still own input. They cannot provide
    cursor anchors without a placed display. Port assignments are plans, never
    evidence of connected hardware; duplicate physical assignments are refused.
    """
    _fields(value, {"schema_version", "computers", "input_owner"}, set(), "desk model")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeskModelError("Unsupported desk model version")
    computers = value["computers"]
    if type(computers) is not list or len(computers) > MAX_COMPUTERS:
        raise DeskModelError("A desk supports at most 16 computers")
    result, ids, display_ids, inputs, upstreams = [], set(), set(), set(), set()
    for computer in computers:
        _fields(computer, {"id", "name", "displays"}, {"dell_input", "usb_upstream"}, "computer")
        identifier, name = _identifier(computer["id"]), _name(computer["name"])
        if identifier in ids:
            raise DeskModelError("Duplicate computer ID")
        ids.add(identifier)
        source = _optional_port(computer.get("dell_input"), DELL_INPUTS)
        # USB-C video uses the same physical Thunderbolt upstream, not any of
        # the monitor's separate data-only USB upstreams. Keep one identity.
        if source == "USB-C":
            source = "Thunderbolt"
        upstream = _optional_port(computer.get("usb_upstream"), USB_UPSTREAMS)
        if (source is not None and source in inputs) or (upstream is not None and upstream in upstreams):
            raise DeskModelError("A physical monitor port cannot be assigned to two computers")
        if source is not None:
            inputs.add(source)
        if upstream is not None:
            upstreams.add(upstream)
        if type(computer["displays"]) is not list or len(computer["displays"]) > MAX_DISPLAYS:
            raise DeskModelError("A desk supports at most 16 displays in total")
        displays = []
        for display in computer["displays"]:
            _fields(display, {"id", "name", "kind", "anchor"}, {"placement"}, "display")
            display_id = _identifier(display["id"])
            if display_id in display_ids or len(display_ids) >= MAX_DISPLAYS:
                raise DeskModelError("Display IDs must be unique; at most 16 displays in total")
            display_ids.add(display_id)
            kind, anchor = display["kind"], display["anchor"]
            if not isinstance(kind, str) or kind not in {"built-in", "external"}:
                raise DeskModelError("Display kind must be built-in or external")
            if not isinstance(anchor, str) or anchor not in ANCHORS:
                raise DeskModelError("Unrecognized display anchor")
            if anchor == "dell" and (source is None or kind != "external"):
                raise DeskModelError("A Dell anchor requires an external display and that computer's Dell input")
            placement = _placement(display.get("placement"))
            if anchor == "unplaced" and placement is not None:
                raise DeskModelError("An unplaced display cannot have a placement rectangle")
            displays.append({"id": display_id, "name": _name(display["name"]),
                             "kind": kind, "anchor": anchor, "placement": placement})
        result.append({"id": identifier, "name": name, "displays": displays,
                       "dell_input": source, "usb_upstream": upstream})
    owner = value["input_owner"]
    if owner is not None and (not isinstance(owner, str) or owner not in ids):
        raise DeskModelError("Input owner must reference a known computer or be null")
    return {"schema_version": 1, "computers": result, "input_owner": owner}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeskModelError("Duplicate JSON field")
        result[key] = value
    return result


def parse_desk_model(raw: str | bytes) -> dict:
    """Validate bounded JSON; no file access and no permissive NaN/Infinity."""
    if type(raw) not in (str, bytes):
        raise DeskModelError("Desk model must be UTF-8 JSON text")
    try:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(data) > MAX_MODEL_BYTES:
            raise DeskModelError("Desk model exceeds 128 KiB")
        def invalid_constant(_):
            raise DeskModelError("Non-finite JSON number")
        return validate_desk_model(json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                                              parse_constant=invalid_constant))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DeskModelError("Invalid bounded desk model JSON") from exc


def cursor_anchors(value) -> list[dict]:
    """Return planned display endpoints, never infer one for a headless host.

    A directional anchor is a human placement description; it is not a qualified
    Deskflow edge map. Overlap, scaling and physical continuity remain separate.
    """
    model = validate_desk_model(value)
    return [{"computer_id": computer["id"], "display_id": display["id"],
             "anchor": display["anchor"], "placement": copy.deepcopy(display["placement"])}
            for computer in model["computers"] for display in computer["displays"]
            if display["anchor"] != "unplaced"]


def remove_computer(value, computer_id: str, *, reassign_to: str | None = None) -> dict:
    """Remove a computer without silently deleting referenced displays/input.

    Explicit reassignment transfers displays and input ownership to the target.
    Planned video/USB wiring is never migrated implicitly. A target receiving a
    Dell display must already have its own Dell input, or validation refuses.
    """
    model = validate_desk_model(value)
    _identifier(computer_id)
    computers = {computer["id"]: computer for computer in model["computers"]}
    if computer_id not in computers:
        raise DeskModelError("Cannot remove an unknown computer")
    if reassign_to is not None:
        _identifier(reassign_to)
        if reassign_to == computer_id or reassign_to not in computers:
            raise DeskModelError("Reassignment requires another known computer")
    source = computers[computer_id]
    if (source["displays"] or model["input_owner"] == computer_id) and reassign_to is None:
        raise DeskModelError("Computer is referenced; explicitly reassign displays/input before removal")
    if reassign_to is not None:
        computers[reassign_to]["displays"].extend(source["displays"])
        if model["input_owner"] == computer_id:
            model["input_owner"] = reassign_to
    model["computers"] = [computer for computer in model["computers"] if computer["id"] != computer_id]
    return validate_desk_model(model)
