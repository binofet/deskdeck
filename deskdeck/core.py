"""A profile is a complete desired state, never a toggle of remembered state."""

import copy
import re

DEVICES = ("main", "linux", "mac", "windows")
HOTKEY_ORDER = ("main", "mac", "linux", "windows")
FOCUS_NAMES = {"main": "Main laptop", "mac": "Mac", "linux": "Linux", "windows": "Windows"}


def profiles():
    shared = {
        "id": "shared", "name": "Shared desk", "mode": "shared", "focus": None,
        "description": "Main on the left. Linux above Mac on the right.",
        "regions": [{"device": "main", "position": "left"},
                    {"device": "linux", "position": "top_right"},
                    {"device": "mac", "position": "bottom_right"}],
        "monitor": {"mode": "pbp3", "layout": "left-large-right-stack",
                    "regions": {"left": "main", "top_right": "linux", "bottom_right": "mac"}},
        "left": "main", "right": "windows",
    }
    grid = {
        "id": "shared-four", "name": "Four-way desk", "mode": "grid", "focus": None,
        "description": "Mac and Linux above Main and Windows.",
        "regions": [{"device": "mac", "position": "top_left"},
                    {"device": "linux", "position": "top_right"},
                    {"device": "main", "position": "bottom_left"},
                    {"device": "windows", "position": "bottom_right"}],
        "monitor": {"mode": "pbp4", "layout": "2x2",
                    "regions": {"top_left": "mac", "top_right": "linux",
                                "bottom_left": "main", "bottom_right": "windows"}},
        "left": "main", "right": "windows",
    }
    result = [shared, grid]
    for device in ("main", "mac", "linux", "windows"):
        result.append({
            "id": "focus-" + device, "name": FOCUS_NAMES[device] + " focus",
            "mode": "focus", "focus": device,
            "description": FOCUS_NAMES[device] + " takes the whole Dell. Both laptops stay at the ends.",
            "regions": [{"device": device, "position": "full"}],
            "monitor": {"mode": "fullscreen", "regions": {"main": device}},
            "left": "main", "right": "windows",
        })
    return result


def get_profile(profile_id):
    for profile in profiles():
        if profile["id"] == profile_id:
            return copy.deepcopy(profile)
    raise ValueError("Unknown profile")


def status():
    return {
        "mode": "preview", "hardware_ready": False, "deskflow_ready": False,
        "current_profile": None,
        "message": "Preview your desk. Monitor control and Deskflow calibration are not connected yet.",
    }


def plan_profile(profile_id, setup=None):
    profile = get_profile(profile_id)
    monitor = copy.deepcopy(profile["monitor"])
    result = {
        "profile": profile, "ready": False,
        "steps": [
            {"phase": "monitor", "action": "Restore the complete Dell layout", "details": monitor},
            {"phase": "video", "action": "Set every region's video source", "details": monitor["regions"]},
            {"phase": "verify", "action": "Read back the monitor layout and inputs"},
            {"phase": "displays", "action": "Reconcile each computer's visible display arrangement"},
            {"phase": "deskflow", "action": "Load this profile's calibrated cursor routes"},
            {"phase": "verify", "action": "Confirm monitor and input routing together"},
        ],
        "blockers": [
            "The Dell's exact input ports and complete layout readback need verification.",
            "Deskflow's multi-monitor edges and safe profile activation need calibration.",
        ],
    }
    if setup is not None:
        from .setup import setup_summary
        planned = setup_summary(setup)
        result["setup"] = planned
        result["steps"][1]["details"] = {
            region: {"device": device, "port": planned["source_inputs"][device]}
            for region, device in monitor["regions"].items()
        }
        result["blockers"][0] = "Planned ports are recorded; actual monitor identity, input assignment and complete layout readback still need verification."
        if planned["keyboard_owner"] is None:
            result["blockers"].append("The computer that will serve the shared keyboard and mouse is not selected.")
    shared_owner = setup.get("keyboard_owner") if setup else None
    owner = profile["focus"] if profile["mode"] == "focus" else (shared_owner or "main")
    result["input_handoff"] = {
        "planned_server": owner,
        "planned_clients": [d for d in DEVICES if d != owner],
        "planned_usb_upstream": setup.get("usb_upstreams", {}).get(owner) if setup else None,
        "usb_upstream_verified": False,
        "deskflow_roles_verified": False,
    }
    result["steps"][4]["details"] = {
        "server": owner, "clients": [d for d in DEVICES if d != owner],
        "usb_upstream": result["input_handoff"]["planned_usb_upstream"],
        "requirement": "Coordinate Dell USB ownership and one Deskflow role per computer; verify trust and reachability before handing input over.",
    }
    return result


def logical_routes(profile_id):
    """Draft computer-edge routing; physical monitor geometry still needs calibration."""
    p = get_profile(profile_id)
    if p["mode"] == "grid":
        return {
            "mac": [("right", "linux"), ("down", "main")],
            "linux": [("left", "mac"), ("down", "windows")],
            "main": [("up", "mac"), ("right", "windows")],
            "windows": [("up", "linux"), ("left", "main")],
        }
    if p["mode"] == "shared":
        return {
            "main": [("right(0,50)", "linux"), ("right(50,100)", "mac")],
            "linux": [("left", "main(0,50)"), ("down", "mac"), ("right", "windows(0,50)")],
            "mac": [("left", "main(50,100)"), ("up", "linux"), ("right", "windows(50,100)")],
            "windows": [("left(0,50)", "linux"), ("left(50,100)", "mac")],
        }
    focus = p["focus"]
    if focus in ("main", "windows"):
        return {"main": [("right", "windows")], "windows": [("left", "main")]}
    return {"main": [("right", focus)], focus: [("left", "main"), ("right", "windows")],
            "windows": [("left", focus)]}


def render_deskflow(profile_id, names=None):
    """Export a draft only. Does not install, start, reload or signal Deskflow."""
    names = dict(names or {device: device for device in DEVICES})
    if set(names) != set(DEVICES):
        raise ValueError("Names must specify exactly main, linux, mac and windows")
    if any(not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", n) for n in names.values()):
        raise ValueError("Unsafe Deskflow screen name")
    if len(set(names.values())) != len(DEVICES):
        raise ValueError("Deskflow screen names must be unique")
    lines = ["# DRAFT: calibrate actual OS display bounds before use.",
             "# DeskDeck does not activate this configuration.", "section: screens"]
    lines.extend("    " + names[d] + ":" for d in DEVICES)
    lines.extend(["end", "", "section: links"])
    for device, links in logical_routes(profile_id).items():
        lines.append("    " + names[device] + ":")
        for edge, target in links:
            target_device, _, suffix = target.partition("(")
            target_name = names[target_device] + (("(" + suffix) if suffix else "")
            lines.append("        " + edge + " = " + target_name)
    lines.extend(["end", "", "section: options"])
    for index, device in enumerate(HOTKEY_ORDER, 1):
        lines.append(f"    keystroke(Control+Alt+{index}) = switchToScreen({names[device]})")
    lines.extend(["end", ""])
    return "\n".join(lines)
