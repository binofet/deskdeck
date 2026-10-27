"""Explicit Apply adapter for one privately bound, reviewed two-state helper.

Import/construction performs no I/O. Only apply() may launch the pinned helper.
A verified receipt proves its target register state, not physical input, display
acceptance, Deskflow readiness or continued health after the observation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import threading
import time

MAX_OUTPUT = 4096
MAX_EXECUTABLE = 8 * 1024 * 1024
TIMEOUT_SECONDS = 65.0
# One local server actor, shared across its adapter instances and HTTP threads.
_APPLY_LOCK = threading.Lock()
# Keep the exact Popen object if its exit cannot be confirmed. No subsequent
# Apply may launch while this child remains alive or its poll is inconclusive.
_QUARANTINED_CHILD = None
_TARGETS = {
    "focus-main": ("fullscreen-darkmatter", 1, 1, 0x1919, 0),
    "shared-four": ("shared-four", 2, 3, 0x1912, 0x41),
}


class MonitorError(ValueError):
    pass


@dataclass(frozen=True)
class DirectMonitorBinding:
    executable: str = field(repr=False)
    sha256: str = field(repr=False)
    monitor_uuid: str = field(repr=False)

    def __post_init__(self):
        if (not isinstance(self.executable, str) or not Path(self.executable).is_absolute()
                or ".." in Path(self.executable).parts
                or any(ord(c) < 32 or ord(c) == 127 for c in self.executable)
                or not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
                or not isinstance(self.monitor_uuid, str)
                or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", self.monitor_uuid)):
            raise MonitorError("An explicit private helper path, digest and exact monitor UUID are required")


@dataclass(frozen=True)
class Captured:
    returncode: int
    stdout: bytes
    stderr: bytes


def _require_prior_exit():
    global _QUARANTINED_CHILD
    child = _QUARANTINED_CHILD
    if child is None:
        return
    try:
        code = child.poll()
    except OSError as exc:
        raise MonitorError("helper_exit_unconfirmed") from exc
    if type(code) is not int:
        raise MonitorError("helper_exit_unconfirmed")
    # Only this retained child's confirmed exit clears the quarantine.
    _QUARANTINED_CHILD = None


def _stop_owned_child(process):
    global _QUARANTINED_CHILD
    try:
        code = process.poll()
        if code is None:
            process.kill()
            code = process.wait(timeout=1)
        if type(code) is not int:
            raise MonitorError("helper_exit_unconfirmed")
    except BaseException as exc:
        _QUARANTINED_CHILD = process
        if isinstance(exc, (OSError, subprocess.TimeoutExpired, MonitorError)):
            raise MonitorError("helper_exit_unconfirmed") from exc
        raise


def _verify_executable(binding: DirectMonitorBinding):
    if not hasattr(os, "getuid"):
        raise MonitorError("helper_binding_refused")
    path = Path(binding.executable)
    for item in (path, *path.parents):
        if item.is_symlink():
            raise MonitorError("helper_binding_refused")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or not before.st_mode & stat.S_IXUSR or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID)
                or not 0 < before.st_size <= MAX_EXECUTABLE):
            raise MonitorError("helper_binding_refused")
        digest, count = hashlib.sha256(), 0
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            count += len(data)
            if count > MAX_EXECUTABLE:
                raise MonitorError("helper_binding_refused")
            digest.update(data)
        after = os.fstat(fd)
        current = path.lstat()
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                    info.st_mode, info.st_uid, info.st_nlink)
        if (identity(before) != identity(after) or identity(after) != identity(current)
                or count != before.st_size or digest.hexdigest() != binding.sha256):
            raise MonitorError("helper_binding_refused")
    finally:
        os.close(fd)


def _capture(argv: list[str], *, timeout: float, max_output: int) -> Captured:
    """Bound both pipes while running; never accumulate unbounded communicate()."""
    process = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MonitorError("helper_timeout")
                for key, _ in selector.select(remaining):
                    output = buffers[key.data]
                    chunk = os.read(key.fileobj.fileno(), min(65536, max_output + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > max_output:
                        raise MonitorError("helper_output_limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MonitorError("helper_timeout")
            try:
                code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired as exc:
                raise MonitorError("helper_timeout") from exc
        return Captured(code, bytes(buffers["stdout"]), bytes(buffers["stderr"]))
    finally:
        # Stop only this retained child. No process lookup, group signal or retry.
        try:
            _stop_owned_child(process)
        finally:
            process.stdout.close()
            process.stderr.close()


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MonitorError("helper_receipt_invalid")
        value[key] = item
    return value


def validate_receipt(raw: bytes, profile_id: str) -> dict:
    """Validate main.m's exact schema and transition.h's final state constants."""
    if profile_id not in _TARGETS or type(raw) is not bytes or len(raw) > MAX_OUTPUT:
        raise MonitorError("helper_receipt_invalid")
    try:
        def invalid_constant(_):
            raise MonitorError("helper_receipt_invalid")
        receipt = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=invalid_constant)
        _, direction, state, current, pbp = _TARGETS[profile_id]
        expected = {
            "schema": 1, "source_candidate": True, "hardware_acceptance": False,
            "status": "VERIFIED_TARGET_REGISTER_STATE", "direction": direction, "stage": 10, "error": 0,
            "identity_verified": True, "capabilities_verified": True, "preflight_verified": True,
            "last_verified_state": state, "last_read_mask": 15, "step_callbacks_entered": 2,
            "set_calls_entered": 2, "set_calls_returned": 2,
            "last_decoded_registers": {"60_current": current, "E8_current": 0x65F1,
                                       "E9_current": pbp, "E7_all_four_bytes": 0x010F1780},
            "reply_requested_bytes": 11, "reply_actual_bytes": None, "automatic_retry": False,
            "automatic_rollback": False, "usb_writes": 0, "e8_writes": 0,
        }
        def exact(value, reference):
            if type(value) is not type(reference):
                return False
            if type(reference) is dict:
                return value.keys() == reference.keys() and all(exact(value[k], v) for k, v in reference.items())
            return value == reference
        if not exact(receipt, expected):
            raise MonitorError("helper_receipt_invalid")
        return receipt
    except (ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise MonitorError("helper_receipt_invalid") from exc


class DirectMonitor:
    def __init__(self, binding: DirectMonitorBinding, *, runner=None):
        if not isinstance(binding, DirectMonitorBinding) or (runner is not None and not callable(runner)):
            raise MonitorError("An explicit helper binding is required")
        self.binding = binding
        self._runner = _capture if runner is None else runner

    def apply(self, profile_id: str) -> dict:
        """One explicit human Apply only; failures never infer a current profile."""
        result = {"requested_profile_id": profile_id, "profile_id": None, "verified": False,
                  "state": "unknown", "error": None, "receipt": None,
                  "physical_acceptance": False}
        if not isinstance(profile_id, str) or profile_id not in _TARGETS:
            result["error"] = "unsupported_profile"
            return result
        if not _APPLY_LOCK.acquire(blocking=False):
            result["error"] = "apply_busy"
            return result
        try:
            _require_prior_exit()
            _verify_executable(self.binding)
            captured = self._runner([self.binding.executable, "display", self.binding.monitor_uuid,
                                     _TARGETS[profile_id][0]], timeout=TIMEOUT_SECONDS, max_output=MAX_OUTPUT)
            if (not isinstance(captured, Captured) or type(captured.returncode) is not int
                    or type(captured.stdout) is not bytes or type(captured.stderr) is not bytes
                    or len(captured.stdout) > MAX_OUTPUT or len(captured.stderr) > MAX_OUTPUT):
                raise MonitorError("helper_output_invalid")
            if captured.returncode != 0:
                raise MonitorError("helper_nonzero_exit")
            receipt = validate_receipt(captured.stdout, profile_id)
            result.update(profile_id=profile_id, verified=True, state="verified_target_register_state", receipt=receipt)
        except MonitorError as exc:
            result["error"] = str(exc)
        except subprocess.TimeoutExpired:
            result["error"] = "helper_timeout"
        except (OSError, subprocess.SubprocessError):
            result["error"] = "helper_unavailable"
        finally:
            _APPLY_LOCK.release()
        return result
