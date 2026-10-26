"""Opt-in, UNQUALIFIED lifecycle experiment for the official Deskflow macOS GUI.

No native modules are imported, discovered or called on import/construction.
The preview/UI does not use this module. Observers and an exclusive transition
lease must be supplied by a separately qualified local operator.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import threading
import time
from typing import Callable, ContextManager, Protocol

from .deskflow import ConfigurationError, Platform, Role, RoleConfiguration, _ini


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _absolute(value: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ConfigurationError("An explicit local absolute path is required")
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ConfigurationError("Parent traversal is not supported")
    return path


def _qt_string(value: str) -> str:
    """Conservative QSettings QString subset used by the prepared role INIs."""
    value = value.strip()
    if value.startswith('"'):
        if not value.endswith('"'):
            raise ValueError
        body, result, index = value[1:-1], [], 0
        while index < len(body):
            character = body[index]
            if character == "\\":
                index += 1
                if index >= len(body) or body[index] not in ('"', "\\"):
                    raise ValueError
                character = body[index]
            result.append(character)
            index += 1
        value = "".join(result)
    elif any(c in value for c in ('"', "\\", ",", ";")):
        raise ValueError
    if value.startswith("@@"):
        value = value[1:]
    elif value.startswith("@"):
        raise ValueError
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError
    return value


def role_semantics_sha256(raw: bytes, binding: MacGuiBinding, role: Role) -> str:
    """Hash independently read critical effective values, not QSettings bytes.

    The observer must read the actual active GUI/core configuration. It must not
    copy the expected plan hash. Extra GUI/logging/state fields are irrelevant;
    the server topology content remains a separate prerequisite observation.
    Unsupported QString encodings refuse rather than guessing equivalence.
    """
    if not isinstance(raw, bytes) or len(raw) > 131072 or role not in (Role.CLIENT, Role.SERVER):
        raise ConfigurationError("Invalid bounded role settings")
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(raw.decode("utf-8-sig"))
        if parser.defaults():
            raise ValueError
        def value(section, key, default=None):
            return _qt_string(parser.get(section, key, fallback=default))
        def boolean(section, key, default):
            found = value(section, key, default)
            if found not in {"true", "false"}:
                raise ValueError
            return found == "true"
        expected_mode = 2 if role == Role.SERVER else 1
        result = {
            "computerName": value("core", "computerName"),
            "coreMode": int(value("core", "coreMode")),
            "processMode": int(value("core", "processMode", "1")),
            "port": int(value("core", "port", "24800")),
            "tlsEnabled": boolean("security", "tlsEnabled", "true"),
            "checkPeerFingerprints": boolean("security", "checkPeerFingerprints", "true"),
            "certificate": value("security", "certificate", str(PurePosixPath(binding.settings_path).parent / "tls/deskflow.pem")),
            "keySize": int(value("security", "keySize", "2048")),
            "enableEnterCommand": boolean("core", "enableEnterCommand", "false"),
            "enableExitCommand": boolean("core", "enableExitCommand", "false"),
            "startCoreWithGui": boolean("gui", "startCoreWithGui", "false"),
            "closeToTray": boolean("gui", "closeToTray", "true"),
            "enableUpdateCheck": boolean("gui", "enableUpdateCheck", "false"),
        }
        if (result["coreMode"] != expected_mode or result["processMode"] != 1
                or not result["tlsEnabled"] or not result["checkPeerFingerprints"]
                or result["enableEnterCommand"] or result["enableExitCommand"]):
            raise ValueError
        if role == Role.CLIENT:
            result["remoteHost"] = value("client", "remoteHost")
            result["languageSync"] = boolean("client", "languageSync", "false")
        else:
            result["interface"] = value("core", "interface", "")
            result["externalConfig"] = boolean("server", "externalConfig", "false")
            result["externalConfigFile"] = value("server", "externalConfigFile")
            result["enableClipboard"] = boolean("server", "enableClipboard", "true")
            result["computers"] = {section: value(section, "name")
                                   for section in parser.sections() if section.startswith("computer_")}
            if not result["externalConfig"]:
                raise ValueError
        return digest(json.dumps(result, sort_keys=True, ensure_ascii=True).encode())
    except (configparser.Error, ValueError, TypeError, AttributeError) as error:
        raise ConfigurationError("Cannot establish critical role settings semantics") from error


@dataclass(frozen=True)
class MacGuiBinding:
    """Paths/identity admitted by the caller, not inferred from a process name."""

    bundle_path: str
    settings_path: str
    owner_uid: int
    bundle_id: str = "org.deskflow.deskflow"

    def __post_init__(self):
        bundle, settings = _absolute(self.bundle_path), _absolute(self.settings_path)
        if bundle.name != "Deskflow.app" or settings.name != "Deskflow.conf":
            raise ConfigurationError("Pin the official bundle and its active GUI INI")
        if type(self.owner_uid) is not int or self.owner_uid < 0:
            raise ConfigurationError("An explicit owner UID is required")
        if self.bundle_id != "org.deskflow.deskflow":
            raise ConfigurationError("Only the official Deskflow bundle ID is supported")

    @property
    def gui_path(self) -> str:
        return self.bundle_path + "/Contents/MacOS/Deskflow"

    @property
    def core_path(self) -> str:
        return self.bundle_path + "/Contents/MacOS/deskflow-core"


@dataclass(frozen=True)
class Process:
    pid: int
    start_identity: str
    executable: str
    uid: int
    ownership_verified: bool
    parent_pid: int | None = None
    role_semantics_sha256: str | None = None
    role: Role | None = None


@dataclass(frozen=True)
class Observation:
    """A complete inventory of these exact GUI/core paths, including conflicts.

    Ownership requires executable/bundle admission, UID, process start identity
    and GUI/core lineage. A basename search or an IPC response is insufficient.
    ``ready`` additionally requires the real active config, TLS and role status;
    it does not represent human input acceptance or whole-desk completion.
    """

    observed_at: float
    complete: bool
    prerequisites_verified: bool
    gui: tuple[Process, ...] = ()
    core: tuple[Process, ...] = ()
    ready: bool = False


@dataclass(frozen=True)
class Plan:
    binding: MacGuiBinding
    role: Role
    current_sha256: str
    ini: bytes
    certificate_path: str

    @property
    def settings_sha256(self) -> str:
        return digest(self.ini)

    @property
    def role_semantics_sha256(self) -> str:
        return role_semantics_sha256(self.ini, self.binding, self.role)


@dataclass(frozen=True)
class WriteReceipt:
    backup_name: str
    before_sha256: str
    after_sha256: str


@dataclass(frozen=True)
class Result:
    status: str
    phase: str
    failure: str | None = None
    write: WriteReceipt | None = None
    gui_pid: int | None = None
    core_pid: int | None = None
    qualified: bool = False
    human_accepted: bool = False
    cleanup_failed: bool = False


class LifecycleFailure(Exception):
    """Only stable, non-private codes are returned to the caller."""

    def __init__(self, code: str):
        super().__init__(code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
                         else "operation_failed_outcome_requires_observation")


class WriteFailure(LifecycleFailure):
    def __init__(self, receipt: WriteReceipt | None, *, replaced: bool):
        super().__init__("config_write_failed_replaced" if replaced else "config_write_failed_not_replaced")
        self.receipt = receipt
        self.cleanup_failed = False


class Actions(Protocol):
    def quit(self, binding: MacGuiBinding, process: Process, deadline: float) -> bool: ...
    def launch(self, binding: MacGuiBinding, deadline: float) -> int: ...


class IniStore(Protocol):
    def read(self, path: str) -> bytes: ...
    def replace(self, plan: Plan, assert_stopped: Callable[[], None]) -> WriteReceipt: ...


class MacGuiLifecycleAdapter:
    """Serial local transition only; never an Apply implementation.

    All injected operations must honor their deadline. Native-call admission,
    observation completeness/freshness, and the exclusive lease remain unqualified.
    One adapter may run only one attempt; retries need a fresh observed baseline.
    """

    def __init__(self, binding: MacGuiBinding, *, actions: Actions, store: IniStore,
                 observe: Callable[[float], Observation],
                 lease: Callable[[], ContextManager], experimental_opt_in: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 pause: Callable[[float], None] = time.sleep,
                 cancelled: Callable[[], bool] = lambda: False,
                 shutdown_timeout: float = 5, readiness_timeout: float = 10,
                 poll_interval: float = 0.1):
        for value in (shutdown_timeout, readiness_timeout, poll_interval):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 30:
                raise ConfigurationError("Finite lifecycle bounds up to 30 seconds are required")
        if type(experimental_opt_in) is not bool or not isinstance(binding, MacGuiBinding):
            raise ConfigurationError("An explicit binding and opt-in boolean are required")
        self.binding, self.actions, self.store = binding, actions, store
        self.observe, self.lease, self.opt_in = observe, lease, experimental_opt_in
        self.clock, self.pause = clock, pause
        self.cancelled = cancelled
        self.shutdown_timeout, self.readiness_timeout = shutdown_timeout, readiness_timeout
        self.poll_interval = poll_interval
        self._plans: list[Plan] = []
        self._attempted = False

    def capabilities(self) -> dict:
        return {"adapter": "deskflow-macos-gui-lifecycle", "qualified": False,
                "live_profile_apply": False, "experimental_opt_in": self.opt_in,
                "evidence": "injected_observations_only"}

    def prepare(self, configuration: RoleConfiguration, current_sha256: str) -> Plan:
        """Reuse a prepared role; do not write topology, TLS or trust files.

        Server topology must already exist and be admitted by the observer.
        The active GUI file must share the role's TLS/config directory.
        """
        if (not isinstance(configuration, RoleConfiguration)
                or configuration.binding.platform != Platform.MACOS
                or configuration.binding.executable != self.binding.core_path
                or PurePosixPath(self.binding.settings_path).parent.as_posix()
                != configuration.binding.private_directory
                or configuration.role not in (Role.CLIENT, Role.SERVER)
                or not isinstance(current_sha256, str)
                or not re.fullmatch("[0-9a-f]{64}", current_sha256)):
            raise ConfigurationError("Role and active GUI binding do not match")
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        try:
            parser.read_string(configuration.settings_ini)
            if (parser["core"]["coreMode"] != ("2" if configuration.role == Role.SERVER else "1")
                    or parser["core"]["processMode"] != "1"
                    or parser["security"]["tlsEnabled"] != "true"
                    or parser["security"]["checkPeerFingerprints"] != "true"
                    or parser["security"]["certificate"] != _ini(configuration.binding.certificate)
                    or set(parser["security"]) != {"tlsEnabled", "checkPeerFingerprints", "certificate"}
                    or parser["core"]["computerName"] != _ini(configuration.binding.computer_name)
                    or set(parser["core"]) - {"computerName", "coreMode", "processMode", "port", "interface"}
                    or any(section not in {"core", "security", "client", "server"}
                           and not section.startswith("computer_") for section in parser.sections())
                    or parser.defaults()):
                raise ValueError
        except (configparser.Error, KeyError, ValueError) as error:
            raise ConfigurationError("Expected a prepared TLS/Desktop role INI") from error
        raw = (configuration.settings_ini.rstrip() + "\n\n[gui]\n"
               "startCoreWithGui=true\ncloseToTray=false\nenableUpdateCheck=false\n").encode()
        if len(raw) > 131072:
            raise ConfigurationError("Role INI exceeds the bounded size")
        plan = Plan(self.binding, configuration.role, current_sha256, raw, configuration.binding.certificate)
        self._plans = (self._plans + [plan])[-8:]
        return plan

    def _sample(self, deadline: float) -> Observation:
        self._check_cancelled()
        if self.clock() >= deadline:
            raise LifecycleFailure("timeout")
        sample = self.observe(deadline)
        now = self.clock()
        if now >= deadline:
            raise LifecycleFailure("timeout")
        if (not isinstance(sample, Observation) or type(sample.complete) is not bool
                or type(sample.prerequisites_verified) is not bool or not sample.complete
                or not sample.prerequisites_verified or type(sample.ready) is not bool
                or type(sample.observed_at) not in (int, float)
                or not math.isfinite(sample.observed_at)
                or not 0 <= now - sample.observed_at <= self.poll_interval * 2
                or type(sample.gui) is not tuple or type(sample.core) is not tuple
                or len(sample.gui) > 1 or len(sample.core) > 1):
            raise LifecycleFailure("observation_not_admitted")
        for processes, path in ((sample.gui, self.binding.gui_path), (sample.core, self.binding.core_path)):
            for process in processes:
                if (not isinstance(process, Process) or type(process.pid) is not int or process.pid <= 0
                        or not isinstance(process.start_identity, str) or not process.start_identity
                        or process.executable != path or process.uid != self.binding.owner_uid
                        or process.ownership_verified is not True):
                    raise LifecycleFailure("process_ownership_ambiguous")
        if sample.core and (not sample.gui or sample.core[0].parent_pid != sample.gui[0].pid
                            or sample.core[0].pid == sample.gui[0].pid):
            raise LifecycleFailure("core_owner_missing")
        return sample

    def _check_cancelled(self):
        if self.cancelled() is not False:
            raise LifecycleFailure("cancelled_outcome_requires_observation")

    def _check_baseline(self, raw: bytes, plan: Plan):
        if digest(raw) != plan.current_sha256:
            raise LifecycleFailure("baseline_config_changed")
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        try:
            parser.read_string(raw.decode("utf-8"))
            certificate = parser.get("security", "certificate", fallback=None)
            default = PurePosixPath(self.binding.settings_path).parent / "tls/deskflow.pem"
            if certificate is None:
                if plan.certificate_path != str(default):
                    raise ValueError
            elif certificate not in {plan.certificate_path, _ini(plan.certificate_path)}:
                raise ValueError
            if parser.get("security", "keySize", fallback="2048") != "2048":
                raise ValueError
        except (ValueError, configparser.Error) as error:
            raise LifecycleFailure("existing_tls_settings_require_separate_preparation") from error

    def run(self, plan: Plan) -> Result:
        if not self.opt_in:
            return Result("blocked", "preflight", "experimental_opt_in_required")
        if plan not in self._plans or plan.binding != self.binding or self._attempted:
            return Result("blocked", "preflight", "fresh_unchanged_plan_required")
        self._attempted = True
        phase, receipt, launched_pid = "preflight", None, None
        try:
            with self.lease():
                deadline = self.clock() + self.shutdown_timeout
                initial = self._sample(deadline)
                self._check_baseline(self.store.read(self.binding.settings_path), plan)
                phase = "shutdown"
                self._check_cancelled()
                if initial.gui:
                    if self.actions.quit(self.binding, initial.gui[0], deadline) is not True:
                        raise LifecycleFailure("normal_quit_refused")
                old = {(p.pid, p.start_identity) for p in (*initial.gui, *initial.core)}
                absent = 0
                while absent < 2:
                    sample = self._sample(deadline)
                    if any((p.pid, p.start_identity) not in old for p in (*sample.gui, *sample.core)):
                        raise LifecycleFailure("process_replaced_during_shutdown")
                    absent = absent + 1 if not sample.gui and not sample.core else 0
                    if absent < 2:
                        self.pause(min(self.poll_interval, max(0, deadline - self.clock())))

                def assert_stopped():
                    sample = self._sample(deadline)
                    if sample.gui or sample.core:
                        raise LifecycleFailure("process_reappeared")

                phase = "write_config"
                assert_stopped()
                receipt = self.store.replace(plan, assert_stopped)
                if (not isinstance(receipt, WriteReceipt) or receipt.before_sha256 != plan.current_sha256
                        or receipt.after_sha256 != plan.settings_sha256):
                    raise LifecycleFailure("config_receipt_invalid")
                assert_stopped()
                phase = "launch"
                deadline = self.clock() + self.readiness_timeout
                self._check_cancelled()
                launched_pid = self.actions.launch(self.binding, deadline)
                if type(launched_pid) is not int or launched_pid <= 0:
                    raise LifecycleFailure("launch_not_confirmed")
                phase = "readiness"
                gui_identity = None
                while True:
                    sample = self._sample(deadline)
                    if sample.gui:
                        gui = sample.gui[0]
                        identity = (gui.pid, gui.start_identity)
                        if gui.pid != launched_pid or (gui_identity is not None and identity != gui_identity):
                            raise LifecycleFailure("unexpected_gui_after_launch")
                        gui_identity = identity
                    if any((p.pid, p.start_identity) in old for p in (*sample.gui, *sample.core)):
                        raise LifecycleFailure("old_process_returned_after_launch")
                    if sample.core:
                        core = sample.core[0]
                        if core.role_semantics_sha256 != plan.role_semantics_sha256 or core.role != plan.role:
                            raise LifecycleFailure("unexpected_core_after_launch")
                        if sample.ready:
                            return Result("observed_ready", phase, write=receipt,
                                          gui_pid=launched_pid, core_pid=core.pid)
                    self.pause(min(self.poll_interval, max(0, deadline - self.clock())))
        except WriteFailure as error:
            return Result("failed", phase, str(error), error.receipt, launched_pid,
                          cleanup_failed=error.cleanup_failed)
        except LifecycleFailure as error:
            return Result("failed", phase, str(error), receipt, launched_pid)
        except Exception:
            # No raw native errors, paths, IPs or config text escape this boundary.
            return Result("failed", phase, "operation_failed_outcome_requires_observation", receipt, launched_pid)


class CocoaActions:
    """Injected AppKit/Foundation objects; no bridge dependency or discovery.

    Caller supplies NSRunningApplication, NSWorkspace instance, NSURL and
    NSWorkspaceOpenConfiguration classes on a qualified native runtime/thread.
    Normal termination may be denied; LaunchServices completion may arrive late.
    No force quit, signals, shell, subprocess, AppleScript or UI automation exists.
    """

    def __init__(self, running_application, workspace, url_class, configuration_class,
                 *, clock: Callable[[], float] = time.monotonic):
        self.running_application, self.workspace = running_application, workspace
        self.url_class, self.configuration_class = url_class, configuration_class
        self.clock = clock

    def quit(self, binding: MacGuiBinding, process: Process, deadline: float) -> bool:
        if self.clock() >= deadline:
            raise LifecycleFailure("timeout")
        app = self.running_application.runningApplicationWithProcessIdentifier_(process.pid)
        if app is None:
            return True  # Absence is still independently checked by the adapter.
        if (app.processIdentifier() != process.pid or app.bundleIdentifier() != binding.bundle_id
                or app.bundleURL().path() != binding.bundle_path
                or app.executableURL().path() != binding.gui_path
                or app.launchDate() is None
                or f"{app.launchDate().timeIntervalSince1970():.6f}" != process.start_identity):
            raise LifecycleFailure("quit_target_changed")
        return bool(app.terminate())

    def launch(self, binding: MacGuiBinding, deadline: float) -> int:
        if self.clock() >= deadline:
            raise LifecycleFailure("timeout")
        event, outcome = threading.Event(), []

        def completed(app, error):
            outcome.append(app.processIdentifier() if app is not None and error is None else None)
            event.set()

        configuration = self.configuration_class.configuration()
        configuration.setActivates_(False)
        configuration.setCreatesNewApplicationInstance_(False)
        configuration.setAllowsRunningApplicationSubstitution_(False)
        self.workspace.openApplicationAtURL_configuration_completionHandler_(
            self.url_class.fileURLWithPath_(binding.bundle_path), configuration, completed)
        if not event.wait(max(0, deadline - self.clock())):
            raise LifecycleFailure("launch_completion_timeout_outcome_unknown")
        if not outcome or type(outcome[0]) is not int or outcome[0] <= 0:
            raise LifecycleFailure("launch_failed")
        return outcome[0]


class AtomicIniStore:
    """Bounded active-INI-only I/O, with a private durable backup before replace.

    Uses only existing directories. Refuses symlinks in every path component,
    non-private parents/files, non-owner files and changed baselines. An exclusive
    caller lease is required: macOS has no file/process atomic transaction.
    """

    LIMIT = 131072

    def _directory(self, path: str) -> tuple[int, str]:
        parsed = _absolute(path)
        if parsed.name != "Deskflow.conf":
            raise LifecycleFailure("only_active_ini_supported")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in parsed.parent.parts[1:]:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise LifecycleFailure("config_directory_not_private")
            return fd, parsed.name
        except Exception:
            os.close(fd)
            raise

    def _read_at(self, fd: int, name: str) -> bytes:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise LifecycleFailure("config_file_not_private")
            raw = stream.read(self.LIMIT + 1)
        if len(raw) > self.LIMIT:
            raise LifecycleFailure("config_too_large")
        return raw

    def read(self, path: str) -> bytes:
        fd, name = self._directory(path)
        try:
            return self._read_at(fd, name)
        finally:
            os.close(fd)

    def replace(self, plan: Plan, assert_stopped: Callable[[], None]) -> WriteReceipt:
        if len(plan.ini) > self.LIMIT:
            raise LifecycleFailure("config_too_large")
        fd, name = self._directory(plan.binding.settings_path)
        temporary, receipt, replaced, failure = None, None, False, None
        try:
            original = self._read_at(fd, name)
            if digest(original) != plan.current_sha256:
                raise LifecycleFailure("baseline_config_changed")
            assert_stopped()
            suffix = secrets.token_hex(12)
            backup, temporary = name + ".backup-" + suffix, ".Deskflow-role-" + suffix

            def write_new(filename, data):
                new_fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
                with os.fdopen(new_fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())

            write_new(backup, original)
            receipt = WriteReceipt(backup, plan.current_sha256, plan.settings_sha256)
            os.fsync(fd)
            write_new(temporary, plan.ini)
            if digest(self._read_at(fd, name)) != plan.current_sha256:
                raise LifecycleFailure("baseline_config_changed")
            assert_stopped()
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            replaced = True
            temporary = None
            os.fsync(fd)
            if self._read_at(fd, name) != plan.ini:
                raise LifecycleFailure("config_readback_failed")
            return receipt
        except Exception as error:
            failure = WriteFailure(receipt, replaced=replaced)
            raise failure from error
        finally:
            try:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                    except OSError:
                        if failure is not None:
                            failure.cleanup_failed = True
            finally:
                os.close(fd)
