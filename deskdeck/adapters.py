"""Capability-gated Dell DDPM plans. Importing this module never probes hardware.

Only documented macOS DDPM 2.3.0.2 commands are represented. Complete monitor
profile writes deliberately remain blocked: the published guide does not establish
the complete layout/region readback needed to verify a restoration. See HARDWARE.md.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
from typing import Any


class AdapterError(Exception):
    """An adapter could not safely prepare or complete an operation."""

    code = "adapter_error"


class UnsupportedFeature(AdapterError):
    code = "unsupported_feature"


class BindingError(AdapterError):
    code = "invalid_binding"


class ExecutionDisabled(AdapterError):
    code = "execution_disabled"


class InvalidPlan(AdapterError):
    code = "invalid_plan"


class CommandTimeout(AdapterError):
    code = "command_timeout"


class OutputLimitExceeded(AdapterError):
    code = "output_limit_exceeded"


class CommandFailed(AdapterError):
    code = "command_failed"


class HardwareAdapter:
    """An adapter prepares plans separately from explicit execution."""

    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError

    def prepare(self, profile: dict, bindings: dict) -> list[dict]:
        raise NotImplementedError

    def apply(self, steps: list[dict]) -> dict:
        raise NotImplementedError


_PORTS = frozenset({
    "HDMI", "HDMI1", "HDMI2", "DP", "DP1", "DP2", "USB-C", "USB-C1",
    "USB-C2", "Thunderbolt", "VGA",
})
_PBP3_REASON = (
    "Complete PBP3 restore is unsupported: the exact U5226KW layout mapping, "
    "all three ordered region input assignments, and semantic state readback "
    "have not been verified. No monitor commands were executed."
)
_FULLSCREEN_REASON = (
    "Live fullscreen restore is unsupported: documented ActiveInputSource "
    "readback cannot verify that PxP is off. No monitor commands were executed."
)


def _capture(argv: list[str], timeout: float, limit: int) -> dict:
    """Capture bounded bytes without a shell or unbounded communicate() buffer."""
    try:
        process = subprocess.Popen(
            argv, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True,
        )
    except OSError as error:
        raise CommandFailed("The configured executable could not be started.") from error
    streams = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout
    try:
        for name in buffers:
            stream = getattr(process, name)
            assert stream is not None
            streams.register(stream, selectors.EVENT_READ, name)
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandTimeout("The DDPM command exceeded its time limit.")
            for key, _ in streams.select(min(remaining, 0.05)):
                chunk = os.read(key.fileobj.fileno(), min(8192, limit - total + 1))
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > limit:
                    raise OutputLimitExceeded("The DDPM response exceeded its byte limit.")
                buffers[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CommandTimeout("The DDPM command exceeded its time limit.")
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise CommandTimeout("The DDPM command exceeded its time limit.") from error
        if returncode != 0:
            raise CommandFailed(f"The DDPM command exited with status {returncode}.")
        return {name: bytes(value).decode("utf-8", errors="replace")
                for name, value in buffers.items()}
    finally:
        streams.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        for name in buffers:
            stream = getattr(process, name)
            if stream is not None:
                stream.close()


class DDPMAdapter(HardwareAdapter):
    """A macOS DDPM adapter with preview by default and explicit read opt-in.

    No executable discovery, installation, probing, or shell command passthrough
    exists. ``allow_writes`` cannot override an unsupported restoration contract.
    Plans are valid only on the instance that prepared them and cannot be edited.
    """

    def __init__(self, executable: str | None = None, *, allow_execute: bool = False,
                 allow_writes: bool = False, timeout: float = 5.0,
                 max_output_bytes: int = 65536):
        if executable is not None and (
            not isinstance(executable, str) or not Path(executable).is_absolute()
            or "\x00" in executable
        ):
            raise BindingError("DDPM requires an explicit absolute executable path.")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise BindingError("The command timeout must be between 0 and 60 seconds.")
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or not 256 <= max_output_bytes <= 1048576:
            raise BindingError("The response byte limit must be between 256 and 1048576.")
        self.executable = executable
        self.allow_execute = bool(allow_execute)
        self.allow_writes = bool(allow_writes)
        self.timeout = float(timeout)
        self.max_output_bytes = max_output_bytes
        self._plans: list[tuple[list[dict], dict | None]] = []

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter": "dell-ddpm-macos",
            "documentation_version": "2.3.0.2",
            "hardware_verified": False,
            "preview": True,
            "execution_enabled": self.allow_execute,
            "executable_configured": self.executable is not None,
            "read_only": ["identity", "firmware", "active_input"],
            "live_profile_apply": False,
            "fullscreen": {"preview": True, "apply": False, "reason": _FULLSCREEN_REASON},
            "pbp3": {"preview": False, "apply": False, "reason": _PBP3_REASON},
            "allowed_ports": sorted(_PORTS),
            "unsupported": ["pxp_mode_readback", "complete_region_readback", "pbp3_restore"],
        }

    @staticmethod
    def _binding(bindings: dict, required: set[str]) -> dict:
        if not isinstance(bindings, dict) or not isinstance(bindings.get("monitor"), dict):
            raise BindingError("A verified local monitor binding is required.")
        monitor = bindings["monitor"]
        if monitor.get("verified") is not True:
            raise BindingError("Monitor identity and capabilities must be verified locally first.")
        if monitor.get("model") != "U5226KW":
            raise UnsupportedFeature("This adapter has no verified profile contract for that monitor model.")
        capabilities = monitor.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise BindingError("Monitor capabilities must be an explicit list of verified feature names.")
        missing = required - set(capabilities)
        if missing:
            raise UnsupportedFeature("Missing verified monitor capabilities: " + ", ".join(sorted(missing)))
        selector = monitor.get("selector")
        if not isinstance(selector, dict) or len(selector) != 1:
            raise BindingError("Select exactly one local monitor by service_tag or index.")
        if "service_tag" in selector:
            tag = selector["service_tag"]
            if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9]{7}", tag):
                raise BindingError("A service_tag selector must contain seven ASCII letters or digits.")
        elif "index" in selector:
            index = selector["index"]
            if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= 99:
                raise BindingError("A monitor index must be an integer from 1 to 99.")
        else:
            raise BindingError("Only service_tag and index monitor selectors are supported.")
        return copy.deepcopy(monitor)

    @staticmethod
    def _target(monitor: dict) -> str:
        selector = monitor["selector"]
        if "service_tag" in selector:
            return "-ServiceTag=" + selector["service_tag"]
        return "-Index=" + str(selector["index"])

    def _argv(self, step: dict) -> list[str]:
        operation = step.get("operation")
        if operation == "read_identity":
            args = ["/get", "-app=ConnectedDevices"]
        else:
            options = {
                "read_firmware": ["/get", "-Display=FWVersion"],
                "read_active_input": ["/get", "-Display=ActiveInputSource"],
                "set_pxp_off": ["/set", "-Display=PxP", "-value=off"],
                "set_active_input": ["/set", "-Display=ActiveInputSource"],
            }
            if operation not in options:
                raise InvalidPlan("The plan contains an operation outside the documented allowlist.")
            args = list(options[operation])
            args.append(self._target(step["monitor"]))
            if operation == "set_active_input":
                if step.get("value") not in _PORTS:
                    raise InvalidPlan("The plan input is outside the documented port allowlist.")
                args.append("-value=" + step["value"])
        # A placeholder is presentation only; it is never executed.
        return [self.executable or "<explicit-absolute-DDPM-path>"] + args

    def _remember(self, steps: list[dict], binding: dict | None) -> list[dict]:
        for step in steps:
            step["argv"] = self._argv(step)
        self._plans.append((copy.deepcopy(steps), copy.deepcopy(binding)))
        # Keep preparation memory bounded, including repeated browser previews.
        self._plans = self._plans[-32:]
        return copy.deepcopy(steps)

    def prepare_read(self, feature: str, bindings: dict | None = None) -> list[dict]:
        """Prepare a documented read; discovery alone needs no assumed identity."""
        operations = {"identity": "read_identity", "firmware": "read_firmware",
                      "active_input": "read_active_input"}
        if feature not in operations:
            raise UnsupportedFeature("No documented read operation is enabled for that feature.")
        monitor = None
        if bindings is not None or feature != "identity":
            monitor = self._binding(bindings, {"identity", feature})
        steps = []
        if feature != "identity":
            steps.append({"operation": "read_identity", "kind": "read"})
        step = {"operation": operations[feature], "kind": "read"}
        if monitor is not None and feature != "identity":
            step["monitor"] = monitor
        steps.append(step)
        return self._remember(steps, monitor)

    def prepare(self, profile: dict, bindings: dict) -> list[dict]:
        if not isinstance(profile, dict) or not isinstance(profile.get("monitor"), dict):
            raise BindingError("A profile must provide its complete monitor state.")
        desired = profile["monitor"]
        mode = desired.get("mode")
        if mode == "pbp3":
            # No partial fallback and no speculative letter-to-geometry mapping.
            raise UnsupportedFeature(_PBP3_REASON)
        if mode != "fullscreen":
            raise UnsupportedFeature("Only fullscreen previews and documented reads are available.")
        monitor = self._binding(bindings, {"identity", "firmware", "active_input", "pxp_off"})
        regions = desired.get("regions")
        if not isinstance(regions, dict) or set(regions) != {"main"}:
            raise BindingError("A fullscreen profile requires exactly one main region.")
        machine = regions["main"]
        if not isinstance(machine, str) or not machine:
            raise BindingError("The main region must name a bound machine.")
        inputs = bindings.get("inputs")
        port = inputs.get(machine) if isinstance(inputs, dict) else None
        if not isinstance(port, dict) or port.get("verified") is not True or port.get("port") not in _PORTS:
            raise BindingError("The machine needs a verified physical input from the documented port allowlist.")
        if "service_tag" not in monitor["selector"]:
            raise BindingError("Profile previews require a stable service_tag monitor selector.")
        steps = [
            {"operation": "read_identity", "kind": "read"},
            {"operation": "read_firmware", "kind": "read", "monitor": monitor},
            {"operation": "set_pxp_off", "kind": "write", "monitor": monitor,
             "blocked_reason": _FULLSCREEN_REASON},
            {"operation": "set_active_input", "kind": "write", "monitor": monitor,
             "value": port["port"], "blocked_reason": _FULLSCREEN_REASON},
            {"operation": "read_active_input", "kind": "read", "monitor": monitor},
        ]
        return self._remember(steps, monitor)

    @staticmethod
    def _response(output: dict) -> list[dict]:
        try:
            value = json.loads(output["stdout"])
        except (ValueError, TypeError) as error:
            raise CommandFailed("DDPM returned an unrecognized JSON response.") from error
        records = value if isinstance(value, list) else [value]
        if not records or not all(isinstance(record, dict) for record in records):
            raise CommandFailed("DDPM returned an unrecognized device response.")
        results = [record["Result"] for record in records if "Result" in record]
        if not results or any(result != "PASS" for result in results):
            raise CommandFailed("DDPM did not report PASS for the read operation.")
        return records

    @staticmethod
    def _verify_identity(records: list[dict], binding: dict) -> None:
        selector = binding["selector"]
        def matches(record: dict) -> bool:
            if record.get("DeviceType") != "Display" or record.get("Model") != binding["model"]:
                return False
            if "service_tag" in selector:
                return record.get("ServiceTag") == selector["service_tag"]
            return str(record.get("Index")) == str(selector["index"])
        if sum(matches(record) for record in records) != 1:
            raise BindingError("The bound monitor identity was not uniquely confirmed by DDPM.")

    @staticmethod
    def _verify_read(records: list[dict], step: dict) -> None:
        """Do not mistake a PASS for another device/feature for the requested read."""
        expected = {"read_firmware": "FWVersion", "read_active_input": "ActiveInputSource"}
        feature = expected[step["operation"]]
        binding = step["monitor"]
        selector = binding["selector"]
        matches = []
        for record in records:
            if record.get("Model") != binding["model"]:
                continue
            if "service_tag" in selector and record.get("ServiceTag") != selector["service_tag"]:
                continue
            if "index" in selector and str(record.get("Index")) != str(selector["index"]):
                continue
            if record.get("Command") != "GET" or record.get("TargetFeature") != feature:
                continue
            value = record.get("Value")
            if not isinstance(value, str) or not value or value == "N/A":
                continue
            if record.get("Result") == "PASS":
                matches.append(record)
        if len(matches) != 1:
            raise CommandFailed("DDPM did not uniquely confirm the requested monitor read.")

    def apply(self, steps: list[dict]) -> dict:
        prepared = next(((plan, binding) for plan, binding in reversed(self._plans)
                         if plan == steps), None)
        if prepared is None:
            raise InvalidPlan("Only an unchanged plan prepared by this adapter instance may be applied.")
        plan, binding = copy.deepcopy(prepared)
        # Rebuild every argv before any process starts; returned argv is never passthrough.
        for step in plan:
            if step["argv"] != self._argv(step):
                raise InvalidPlan("The executable or prepared command changed after preparation.")
        if not self.allow_execute:
            return {"status": "preview", "executed": False, "hardware_verified": False,
                    "steps": plan, "blocked": any(step["kind"] == "write" for step in plan)}
        if any(step["kind"] == "write" for step in plan):
            # This is deliberately prior to read probes as well as all mutations.
            raise UnsupportedFeature(_FULLSCREEN_REASON)
        if self.executable is None:
            raise ExecutionDisabled("An explicit absolute DDPM executable path is required for reads.")
        if not Path(self.executable).is_file() or not os.access(self.executable, os.X_OK):
            raise ExecutionDisabled("The configured DDPM executable is not an executable file.")
        results = []
        for step in plan:
            output = _capture(self._argv(step), self.timeout, self.max_output_bytes)
            records = self._response(output)
            if step["operation"] == "read_identity" and binding is not None:
                self._verify_identity(records, binding)
            elif step["operation"] != "read_identity":
                self._verify_read(records, step)
            results.append({"operation": step["operation"], "records": records})
        return {"status": "read_complete", "executed": True, "hardware_verified": False,
                "results": results}


DDPMMacAdapter = DDPMAdapter
