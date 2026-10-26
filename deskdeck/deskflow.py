"""Pure preparation and acknowledgement contracts for Deskflow 1.27.

This module never reads certificates, discovers a host, starts a process, sends
IPC, or operates USB. Readiness and transition observations belong to the live
operator; recording an observation here is not independently checking it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import ipaddress
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Iterable
import unicodedata

from .core import DEVICES, get_profile, logical_routes


class ConfigurationError(ValueError):
    """An explicit binding or observation cannot be used safely."""


class Platform(StrEnum):
    MACOS = "macos"
    WINDOWS = "windows"
    LINUX = "linux"


class Role(StrEnum):
    SERVER = "server"
    CLIENT = "client"


class Stage(StrEnum):
    PREPARED = "prepared"
    OLD_OWNED_STOPPED = "old_owned_stopped"
    USB_APPLIED = "usb_applied"
    SERVER_LISTENING = "server_listening"
    EXPECTED_ROSTER = "expected_roster"
    HUMAN_ACCEPTED = "human_accepted"
    FAILED = "failed"


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_USB = frozenset({"Thunderbolt", "USB2", "USB3", "USB4"})


def _text(value: str, label: str, maximum: int = 4096) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(unicodedata.category(c).startswith("C") or c in "\u2028\u2029" for c in value)):
        raise ConfigurationError(f"Invalid {label}")
    return value


def _device(value: str) -> str:
    if not isinstance(value, str) or value not in DEVICES:
        raise ConfigurationError("Unknown device ID")
    return value


def _fingerprint(value: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ConfigurationError("Expected a SHA-256 fingerprint")
    return value.lower()


def _items(values: Iterable[str], label: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ConfigurationError(f"Invalid {label}")
    result = tuple(values)
    if any(not isinstance(value, str) for value in result) or len(result) != len(set(result)):
        raise ConfigurationError(f"Invalid {label}")
    return result


def _path(value: str, platform: Platform):
    _text(value, "absolute path")
    path = PureWindowsPath(value) if platform == Platform.WINDOWS else PurePosixPath(value)
    if (not path.is_absolute() or ".." in path.parts
            or (platform == Platform.WINDOWS and not re.fullmatch(r"[A-Za-z]:", path.drive))
            or (platform != Platform.WINDOWS and value.startswith("//"))):
        raise ConfigurationError("An explicit local absolute path without parent traversal is required")
    if platform == Platform.WINDOWS:
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789\u00b9\u00b2\u00b3")
        for part in path.parts[1:]:
            if (re.search(r'[<>:"|?*]', part) or part.endswith((" ", "."))
                    or part.partition(".")[0].upper() in reserved):
                raise ConfigurationError("Reserved or ambiguous Windows path component")
    return path


def _ini(value: str | int | bool) -> str:
    """Quote QSettings strings, including literal quotes and backslashes."""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    _text(value, "INI value")
    # QSettings uses @@ to distinguish literal @ from its QVariant notation.
    value = ("@" + value) if value.startswith("@") else value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass(frozen=True)
class DeviceBinding:
    device_id: str
    computer_name: str
    lan_address: str
    platform: Platform
    usb_upstream: str
    executable: str
    private_directory: str
    server_settings: str
    client_settings: str
    topology: str
    certificate: str

    def __post_init__(self):
        _device(self.device_id)
        if not isinstance(self.platform, Platform):
            raise ConfigurationError("An explicit Platform enum is required")
        if not isinstance(self.computer_name, str) or not _NAME.fullmatch(self.computer_name):
            raise ConfigurationError("Unsafe Deskflow computer name")
        if not isinstance(self.usb_upstream, str) or self.usb_upstream not in _USB:
            raise ConfigurationError("Unknown USB upstream")
        _text(self.lan_address, "literal LAN address", 64)
        try:
            address = ipaddress.ip_address(self.lan_address)
        except ValueError as error:
            raise ConfigurationError("A literal unicast LAN address is required") from error
        if (address.is_unspecified or address.is_multicast or address.is_loopback or address.is_reserved
                or getattr(address, "scope_id", None) is not None
                or getattr(address, "ipv4_mapped", None) is not None):
            raise ConfigurationError("Unsupported LAN address")
        object.__setattr__(self, "lan_address", address.compressed)
        paths = {}
        for key in ("executable", "private_directory", "server_settings", "client_settings", "topology", "certificate"):
            paths[key] = _path(getattr(self, key), self.platform)
            object.__setattr__(self, key, paths[key].as_posix())
        base = paths["private_directory"]
        if base == base.parent:
            raise ConfigurationError("A private data directory is required")
        if self.platform == Platform.WINDOWS and tuple(part.casefold() for part in base.parts[1:]) != ("programdata", "deskflow"):
            raise ConfigurationError("Non-portable Windows requires its system-drive ProgramData/Deskflow TLS base")
        if any(not paths[key].is_relative_to(base) or paths[key] == base
               for key in ("server_settings", "client_settings", "topology", "certificate")):
            raise ConfigurationError("Configuration and certificate paths must stay inside the private directory")
        if any(paths[key].parent != base for key in ("server_settings", "client_settings")):
            raise ConfigurationError("Both role INIs must share the private directory and TLS base")
        if paths["certificate"].parent != base / "tls":
            raise ConfigurationError("The private certificate must use the shared TLS directory")
        files = [paths[key] for key in ("executable", "server_settings", "client_settings", "topology", "certificate")]
        if len(set(files)) != 5 or any(left != right and left.is_relative_to(right) for left in files for right in files):
            raise ConfigurationError("Executable and generated data files must have distinct paths")

    @property
    def digest(self) -> str:
        value = {key: getattr(self, key) for key in self.__dataclass_fields__}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeviceReadiness:
    """Public metadata supplied by the operator, never a certificate-body read."""

    device_id: str
    binding_digest: str
    certificate_sha256: str | None = None
    trusted_servers: frozenset[str] = frozenset()
    trusted_clients: frozenset[str] = frozenset()
    executable_verified: bool = False
    private_paths_verified: bool = False
    client_permission_granted: bool = False
    server_permission_granted: bool = False

    def __post_init__(self):
        _device(self.device_id)
        object.__setattr__(self, "binding_digest", _fingerprint(self.binding_digest))
        if self.certificate_sha256 is not None:
            object.__setattr__(self, "certificate_sha256", _fingerprint(self.certificate_sha256))
        for key in ("trusted_servers", "trusted_clients"):
            values = _items(getattr(self, key), "trust fingerprints")
            object.__setattr__(self, key, frozenset(_fingerprint(value) for value in values))
        for key in ("executable_verified", "private_paths_verified", "client_permission_granted", "server_permission_granted"):
            if type(getattr(self, key)) is not bool:
                raise ConfigurationError("Readiness observations must be explicit booleans")


@dataclass(frozen=True)
class Blocker:
    device_id: str
    code: str


@dataclass(frozen=True)
class RoleConfiguration:
    binding: DeviceBinding
    role: Role
    settings_path: str
    settings_ini: str

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.binding.executable, self.role.value, "-s", self.settings_path)

    @property
    def settings_digest(self) -> str:
        return hashlib.sha256(self.settings_ini.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Preparation:
    profile_id: str
    server_device: str
    usb_upstream: str
    roles: tuple[RoleConfiguration, ...]
    topology_text: str
    readiness: tuple[DeviceReadiness, ...] = ()
    blockers: tuple[Blocker, ...] = field(init=False)

    def __post_init__(self):
        profile = get_profile(self.profile_id)
        _device(self.server_device)
        if profile["mode"] == "focus" and self.server_device != profile["focus"]:
            raise ConfigurationError("A fullscreen server must be the focus owner")
        if (type(self.roles) is not tuple or len(self.roles) != len(DEVICES)
                or any(not isinstance(item, RoleConfiguration) for item in self.roles)):
            raise ConfigurationError("A complete immutable role roster is required")
        bindings = {item.binding.device_id: item.binding for item in self.roles}
        servers = [item for item in self.roles if item.role == Role.SERVER]
        if (set(bindings) != set(DEVICES) or len(servers) != 1
                or servers[0].binding.device_id != self.server_device
                or self.usb_upstream != servers[0].binding.usb_upstream):
            raise ConfigurationError("Exactly one planned server must match the USB owner")
        for values in ([item.computer_name.casefold() for item in bindings.values()],
                       [item.lan_address for item in bindings.values()],
                       [item.usb_upstream for item in bindings.values()]):
            if len(set(values)) != len(DEVICES):
                raise ConfigurationError("Prepared bindings must remain unique")
        for item in self.roles:
            expected_path = item.binding.server_settings if item.role == Role.SERVER else item.binding.client_settings
            if (not isinstance(item.role, Role) or item.settings_path != expected_path
                    or item.settings_ini != _settings(item.binding, item.role, servers[0].binding, bindings)):
                raise ConfigurationError("Role settings do not match the validated desired configuration")
        if self.topology_text != _topology(self.profile_id, bindings):
            raise ConfigurationError("Topology does not match the prepared profile")
        if type(self.readiness) is not tuple:
            raise ConfigurationError("Immutable readiness observations are required")
        observations = _readiness(self.readiness)
        object.__setattr__(self, "readiness", observations)
        object.__setattr__(self, "blockers", _prerequisite_blockers(bindings, self.server_device, observations))

    @property
    def prerequisites_satisfied(self) -> bool:
        return not self.blockers

    @property
    def expected_clients(self) -> tuple[str, ...]:
        return tuple(item.binding.device_id for item in self.roles if item.role == Role.CLIENT)

    @property
    def expected_computer_names(self) -> frozenset[str]:
        return frozenset(item.binding.computer_name for item in self.roles if item.role == Role.CLIENT)

    @property
    def server(self) -> RoleConfiguration:
        return next(item for item in self.roles if item.role == Role.SERVER)


def _topology(profile_id: str, bindings: dict[str, DeviceBinding]) -> str:
    names = {device: binding.computer_name for device, binding in bindings.items()}
    lines = ["# DRAFT: computer-edge geometry is unqualified for this Dell profile.",
             "# Deskflow 1.27: computer declarations live in the role INI.", "section: links"]
    for device, links in logical_routes(profile_id).items():
        lines.append(f"    {names[device]}:")
        for edge, target in links:
            target_device, _, suffix = target.partition("(")
            target_name = names[target_device] + (("(" + suffix) if suffix else "")
            lines.append(f"        {edge} = {target_name}")
    lines.extend(["end", "", "section: options"])
    for index, device in enumerate(DEVICES, 1):
        lines.append(f"    keystroke(Control+Alt+{index}) = switchToScreen({names[device]})")
    return "\n".join(lines + ["end", ""])


def _settings(binding: DeviceBinding, role: Role, server: DeviceBinding,
              bindings: dict[str, DeviceBinding]) -> str:
    sections = [
        ("core", [("computerName", binding.computer_name), ("coreMode", 2 if role == Role.SERVER else 1),
                  ("processMode", 1), ("port", 24800)]),
        ("security", [("tlsEnabled", True), ("checkPeerFingerprints", True), ("certificate", binding.certificate)]),
    ]
    if role == Role.SERVER:
        sections[0][1].append(("interface", binding.lan_address))
        sections.append(("server", [("externalConfig", True), ("externalConfigFile", binding.topology)]))
        sections.extend((f"computer_{bindings[device].computer_name}", [("name", bindings[device].computer_name)])
                        for device in DEVICES)
    else:
        sections.append(("client", [("remoteHost", server.lan_address), ("languageSync", False)]))
    lines = []
    for section, values in sections:
        lines.append(f"[{section}]")
        lines.extend(f"{key}={_ini(value)}" for key, value in values)
        lines.append("")
    return "\n".join(lines)


def _readiness(observations: tuple[DeviceReadiness, ...]) -> tuple[DeviceReadiness, ...]:
    if any(not isinstance(item, DeviceReadiness) for item in observations):
        raise ConfigurationError("Typed readiness observations are required")
    by_ready = {item.device_id: item for item in observations}
    if len(by_ready) != len(observations):
        raise ConfigurationError("Duplicate readiness observations")
    return tuple(by_ready[device] for device in DEVICES if device in by_ready)


def _prerequisite_blockers(by_device: dict[str, DeviceBinding], owner: str,
                           observations: tuple[DeviceReadiness, ...]) -> tuple[Blocker, ...]:
    by_ready = {item.device_id: item for item in observations}
    blockers = []
    for device in DEVICES:
        ready = by_ready.get(device)
        if ready is None:
            blockers.append(Blocker(device, "missing_readiness"))
            continue
        if ready.binding_digest != by_device[device].digest:
            blockers.append(Blocker(device, "binding_not_qualified"))
        for key, code in (("executable_verified", "executable_not_verified"),
                          ("private_paths_verified", "private_paths_not_verified")):
            if not getattr(ready, key):
                blockers.append(Blocker(device, code))
        if ready.certificate_sha256 is None:
            blockers.append(Blocker(device, "missing_local_certificate"))
        permission = ready.server_permission_granted if device == owner else ready.client_permission_granted
        if not permission:
            blockers.append(Blocker(device, "capture_permission_missing" if device == owner else "injection_permission_missing"))
        if device != owner:
            server_ready = by_ready.get(owner)
            if server_ready is not None and server_ready.certificate_sha256 is not None:
                if server_ready.certificate_sha256 not in ready.trusted_servers:
                    blockers.append(Blocker(device, "server_fingerprint_untrusted"))
            if ready.certificate_sha256 is not None and server_ready is not None:
                if ready.certificate_sha256 not in server_ready.trusted_clients:
                    blockers.append(Blocker(owner, f"client_fingerprint_untrusted:{device}"))
    fingerprints = [item.certificate_sha256 for item in observations if item.certificate_sha256 is not None]
    if len(fingerprints) != len(set(fingerprints)):
        blockers.append(Blocker(owner, "duplicate_local_certificate"))
    return tuple(blockers)


def prepare_profile(profile_id: str, bindings: Iterable[DeviceBinding], *,
                    readiness: Iterable[DeviceReadiness] = (), shared_owner: str = "main") -> Preparation:
    """Render a desired profile. Even satisfied prerequisites are not live health."""
    profile = get_profile(profile_id)
    _device(shared_owner)
    items = tuple(bindings)
    if any(not isinstance(item, DeviceBinding) for item in items):
        raise ConfigurationError("Typed device bindings are required")
    by_device = {item.device_id: item for item in items}
    if len(items) != len(DEVICES) or set(by_device) != set(DEVICES):
        raise ConfigurationError("Exactly one binding for each of the four devices is required")
    for label, values in (("computer names", [item.computer_name.casefold() for item in items]),
                          ("LAN addresses", [item.lan_address for item in items]),
                          ("USB upstreams", [item.usb_upstream for item in items])):
        if len(set(values)) != len(DEVICES):
            raise ConfigurationError(f"Device {label} must be unique")
    observations = _readiness(tuple(readiness))
    owner = profile["focus"] if profile["mode"] == "focus" else shared_owner
    server = by_device[owner]
    roles = tuple(RoleConfiguration(
        by_device[device], Role.SERVER if device == owner else Role.CLIENT,
        by_device[device].server_settings if device == owner else by_device[device].client_settings,
        _settings(by_device[device], Role.SERVER if device == owner else Role.CLIENT, server, by_device),
    ) for device in DEVICES)
    return Preparation(profile_id, owner, server.usb_upstream, roles, _topology(profile_id, by_device), observations)


class HandoffTransaction:
    """Ordered operator acknowledgements. This is deliberately not an executor."""

    def __init__(self, preparation: Preparation, transition_id: str):
        if not isinstance(preparation, Preparation):
            raise ConfigurationError("A prepared Deskflow plan is required")
        if not isinstance(transition_id, str) or not _TOKEN.fullmatch(transition_id):
            raise ConfigurationError("A fresh operator transition ID is required")
        self._plan = preparation
        self._transition_id = transition_id
        self._stage = Stage.PREPARED
        self._server_pid = None
        self._failure_code = None
        self._human_reference = None

    @property
    def stage(self) -> Stage:
        return self._stage

    @property
    def complete(self) -> bool:
        return self._stage == Stage.HUMAN_ACCEPTED

    def _expect(self, transition_id: str, stage: Stage):
        if transition_id != self._transition_id:
            raise ConfigurationError("Stale transition acknowledgement")
        if self._stage != stage:
            raise ConfigurationError("Out-of-order or terminal transition")
        if not self._plan.prerequisites_satisfied:
            raise ConfigurationError("Deskflow prerequisites are not satisfied")

    def old_owned_stopped(self, transition_id: str, stopped_devices: Iterable[str]):
        self._expect(transition_id, Stage.PREPARED)
        devices = _items(stopped_devices, "stopped device roster")
        if set(devices) != set(DEVICES):
            raise ConfigurationError("Every previous owned core must be stopped or verified absent")
        self._stage = Stage.OLD_OWNED_STOPPED

    def usb_applied(self, transition_id: str, device_id: str, upstream: str, *, verified: bool):
        self._expect(transition_id, Stage.OLD_OWNED_STOPPED)
        if verified is not True or device_id != self._plan.server_device or upstream != self._plan.usb_upstream:
            raise ConfigurationError("USB readback does not match the planned server")
        self._stage = Stage.USB_APPLIED

    def server_listening(self, transition_id: str, device_id: str, pid: int, settings_digest: str,
                         *, process_owned: bool):
        self._expect(transition_id, Stage.USB_APPLIED)
        if (device_id != self._plan.server_device or type(pid) is not int or pid <= 0
                or process_owned is not True or settings_digest != self._plan.server.settings_digest):
            raise ConfigurationError("The listening server does not match the owned planned core")
        self._server_pid = pid
        self._stage = Stage.SERVER_LISTENING

    def expected_roster(self, transition_id: str, server_pid: int, connected_computers: Iterable[str],
                        *, tls_verified: bool):
        self._expect(transition_id, Stage.SERVER_LISTENING)
        names = _items(connected_computers, "connected computer roster")
        if (type(server_pid) is not int or server_pid != self._server_pid or tls_verified is not True
                or set(names) != self._plan.expected_computer_names):
            raise ConfigurationError("The authenticated client roster is incomplete or unexpected")
        self._stage = Stage.EXPECTED_ROSTER

    def human_accepted(self, transition_id: str, *, accepted: bool, evidence_reference: str):
        self._expect(transition_id, Stage.EXPECTED_ROSTER)
        if accepted is not True:
            raise ConfigurationError("Human input acceptance is still required")
        self._human_reference = _text(evidence_reference, "human evidence reference", 512)
        self._stage = Stage.HUMAN_ACCEPTED

    def fail(self, transition_id: str, code: str):
        if transition_id != self._transition_id:
            raise ConfigurationError("Stale transition failure")
        if not isinstance(code, str) or not _TOKEN.fullmatch(code):
            raise ConfigurationError("A bounded failure code is required")
        if self._stage == Stage.FAILED:
            raise ConfigurationError("The first transition failure is terminal")
        self._failure_code = code
        self._stage = Stage.FAILED

    def status(self) -> dict:
        return {
            "transition_id": self._transition_id, "profile_id": self._plan.profile_id,
            "stage": self._stage.value, "complete": self.complete,
            "planned_server": self._plan.server_device, "expected_clients": list(self._plan.expected_clients),
            "prerequisites_satisfied": self._plan.prerequisites_satisfied,
            "blockers": [{"device_id": item.device_id, "code": item.code} for item in self._plan.blockers],
            "failure_code": self._failure_code, "human_evidence_reference": self._human_reference,
            "geometry_qualified": False, "execution_available": False,
            "evidence": "operator_acknowledgements_only",
        }
