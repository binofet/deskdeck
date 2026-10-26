"""Explicit local planned wiring. Loading settings never discovers or controls hardware."""

import copy
import json
import os
import re
import stat

from .core import DEVICES

PORTS = frozenset({"Thunderbolt", "DP1", "DP2", "HDMI1", "HDMI2", "USB-C"})
MAX_SETUP_BYTES = 16384


def validate_setup(value):
    if not isinstance(value, dict):
        raise ValueError("Setup must be a JSON object")
    allowed = {"schema_version", "source_inputs", "reserved_inputs", "keyboard_owner",
               "input_attachment", "usb_upstreams"}
    if set(value) - allowed or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported setup fields or schema version")
    sources = value.get("source_inputs")
    if not isinstance(sources, dict) or set(sources) != set(DEVICES):
        raise ValueError("Source inputs must specify main, linux, mac and windows")
    reserved = value.get("reserved_inputs", {})
    if not isinstance(reserved, dict) or len(reserved) > len(PORTS):
        raise ValueError("Reserved inputs must be a small object")
    if any(not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", k)
           or k in DEVICES for k in reserved):
        raise ValueError("Reserved input names must be distinct local identifiers")
    ports = list(sources.values()) + list(reserved.values())
    if any(not isinstance(port, str) or port not in PORTS for port in ports):
        raise ValueError("Unrecognized U5226KW input name")
    if len(ports) != len(set(ports)):
        raise ValueError("Each source or reservation must use a different input")
    owner = value.get("keyboard_owner")
    if owner is not None and (not isinstance(owner, str) or owner not in DEVICES):
        raise ValueError("Keyboard owner must be a known computer or null")
    result = {"schema_version": 1, "source_inputs": sources,
              "reserved_inputs": reserved, "keyboard_owner": owner}
    if "input_attachment" in value:
        if value["input_attachment"] != "monitor":
            raise ValueError("Coordinated input plans currently support monitor-attached input only")
        result["input_attachment"] = value["input_attachment"]
    if "usb_upstreams" in value:
        usb = value["usb_upstreams"]
        if not isinstance(usb, dict) or set(usb) != set(DEVICES):
            raise ValueError("USB upstreams must specify every computer")
        if any(not isinstance(p, str) or p not in {"Thunderbolt", "USB2", "USB3", "USB4"}
               for p in usb.values()) or len(set(usb.values())) != len(DEVICES):
            raise ValueError("USB upstream ports must be recognized and unique")
        result["usb_upstreams"] = usb
    return copy.deepcopy(result)


def setup_summary(value):
    """Settings are a user plan, never monitor identity or live readback proof."""
    if value is None:
        return None
    return {**validate_setup(value), "evidence": "user_plan", "hardware_verified": False}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate setup field")
        result[key] = value
    return result


def load_setup(path):
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Setup must be a regular file")
            raw = stream.read(MAX_SETUP_BYTES + 1)
        if len(raw) > MAX_SETUP_BYTES:
            raise ValueError("Setup file exceeds 16 KiB")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_setup(value)
    except (OSError, UnicodeError, ValueError) as exc:
        # Do not echo file contents, identifiers, or parser excerpts.
        raise ValueError("Could not load a valid local setup file") from exc
