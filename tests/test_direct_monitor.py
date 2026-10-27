import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deskdeck.direct_monitor import (
    Captured, DirectMonitor, DirectMonitorBinding, MAX_OUTPUT, MonitorError,
    TIMEOUT_SECONDS, _capture, validate_receipt,
)

UUID = "11111111-2222-3333-4444-555555555555"


def receipt(profile="focus-main"):
    shared = profile == "shared-four"
    return {
        "schema": 1, "source_candidate": True, "hardware_acceptance": False,
        "status": "VERIFIED_TARGET_REGISTER_STATE", "direction": 2 if shared else 1,
        "stage": 10, "error": 0, "identity_verified": True, "capabilities_verified": True,
        "preflight_verified": True, "last_verified_state": 3 if shared else 1,
        "last_read_mask": 15, "step_callbacks_entered": 2, "set_calls_entered": 2,
        "set_calls_returned": 2,
        "last_decoded_registers": {"60_current": 0x1912 if shared else 0x1919,
                                   "E8_current": 0x65F1, "E9_current": 0x41 if shared else 0,
                                   "E7_all_four_bytes": 0x010F1780},
        "reply_requested_bytes": 11, "reply_actual_bytes": None, "automatic_retry": False,
        "automatic_rollback": False, "usb_writes": 0, "e8_writes": 0,
    }


def captured(profile="focus-main", code=0):
    return Captured(code, json.dumps(receipt(profile)).encode() + b"\n", b"")


class DirectMonitorTests(unittest.TestCase):
    def setUp(self):
        quarantine = patch("deskdeck.direct_monitor._QUARANTINED_CHILD", None)
        quarantine.start()
        self.addCleanup(quarantine.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.helper = self.folder / "reviewed-helper"
        self.helper.write_bytes(b"fixture only, never executed")
        self.helper.chmod(0o700)
        self.binding = DirectMonitorBinding(str(self.helper), hashlib.sha256(self.helper.read_bytes()).hexdigest(), UUID)

    def test_constructor_is_pure_and_private_binding_repr_does_not_leak(self):
        with patch("deskdeck.direct_monitor.os.open", side_effect=AssertionError("no reads")), \
                patch("deskdeck.direct_monitor.subprocess.Popen", side_effect=AssertionError("no process")):
            binding = DirectMonitorBinding(str(self.helper), self.binding.sha256, UUID)
            DirectMonitor(binding)
        self.assertNotIn(UUID, repr(binding))
        self.assertNotIn(str(self.helper), repr(binding))
        for values in (("relative", self.binding.sha256, UUID),
                       (str(self.folder / ".." / "other"), self.binding.sha256, UUID),
                       (str(self.helper), "bad", UUID), (str(self.helper), self.binding.sha256, "bad")):
            with self.assertRaises(MonitorError):
                DirectMonitorBinding(*values)

    def test_only_fixed_commands_and_exact_complete_receipts_can_verify(self):
        for profile, command in (("focus-main", "fullscreen-darkmatter"), ("shared-four", "shared-four")):
            runner = Mock(return_value=captured(profile))
            result = DirectMonitor(self.binding, runner=runner).apply(profile)
            runner.assert_called_once_with([str(self.helper), "display", UUID, command],
                                           timeout=TIMEOUT_SECONDS, max_output=MAX_OUTPUT)
            self.assertTrue(result["verified"])
            self.assertEqual(result["profile_id"], profile)
            self.assertFalse(result["physical_acceptance"])
            self.assertFalse(result["receipt"]["hardware_acceptance"])
            self.assertIsNone(result["receipt"]["reply_actual_bytes"])
        runner = Mock()
        self.assertEqual(DirectMonitor(self.binding, runner=runner).apply("fullscreen-darkmatter")["error"], "unsupported_profile")
        runner.assert_not_called()

    def test_all_contract_fields_are_required_and_strictly_typed(self):
        reference = receipt()
        for key in reference:
            value = copy.deepcopy(reference)
            del value[key]
            with self.subTest(missing=key), self.assertRaises(MonitorError):
                validate_receipt(json.dumps(value).encode(), "focus-main")
        mutations = {
            "schema": True, "stage": 9, "error": 1, "direction": 2, "identity_verified": 1,
            "preflight_verified": False, "capabilities_verified": False, "last_read_mask": 7,
            "set_calls_entered": 1, "set_calls_returned": 1, "step_callbacks_entered": 3,
            "last_verified_state": 3, "status": "STOPPED_AFTER_SET_PARTIAL_OR_UNKNOWN",
            "reply_actual_bytes": 11, "automatic_retry": True, "automatic_rollback": True,
            "usb_writes": 1, "e8_writes": 1, "hardware_acceptance": True,
        }
        for key, replacement in mutations.items():
            value = {**reference, key: replacement}
            with self.subTest(field=key), self.assertRaises(MonitorError):
                validate_receipt(json.dumps(value).encode(), "focus-main")
        for key in reference["last_decoded_registers"]:
            value = copy.deepcopy(reference)
            value["last_decoded_registers"][key] ^= 1
            with self.subTest(register=key), self.assertRaises(MonitorError):
                validate_receipt(json.dumps(value).encode(), "focus-main")

    def test_malformed_extra_duplicate_or_wrong_direction_receipt_never_verifies(self):
        raw = json.dumps(receipt()).encode()
        cases = [b"\xff", b"{}", raw + raw, b" " * (MAX_OUTPUT + 1),
                 b'{"schema":1,"schema":1}', b'{"schema":NaN}',
                 json.dumps({**receipt(), "unexpected": True}).encode(),
                 json.dumps(receipt("shared-four")).encode()]
        for value in cases:
            with self.subTest(prefix=value[:40]), self.assertRaises(MonitorError):
                validate_receipt(value, "focus-main")

    def test_executable_rechecked_each_apply_and_unsafe_files_never_run(self):
        for tamper in ("hash", "mode", "not-executable", "link", "hardlink", "owner"):
            with self.subTest(tamper=tamper):
                self.helper.write_bytes(b"fixture only, never executed")
                self.helper.chmod(0o700)
                alias = self.folder / "other"
                if tamper == "hash":
                    self.helper.write_bytes(b"modified after binding")
                elif tamper == "mode":
                    self.helper.chmod(0o720)
                elif tamper == "not-executable":
                    self.helper.chmod(0o600)
                elif tamper == "link":
                    self.helper.rename(alias)
                    self.helper.symlink_to(alias)
                elif tamper == "hardlink":
                    os.link(self.helper, alias)
                runner = Mock(return_value=captured())
                if tamper == "owner":
                    with patch("deskdeck.direct_monitor.os.getuid", return_value=os.getuid() + 1):
                        result = DirectMonitor(self.binding, runner=runner).apply("focus-main")
                else:
                    result = DirectMonitor(self.binding, runner=runner).apply("focus-main")
                self.assertFalse(result["verified"])
                self.assertIsNone(result["profile_id"])
                runner.assert_not_called()
                if self.helper.is_symlink():
                    self.helper.unlink()
                if alias.exists():
                    alias.unlink()
                if not self.helper.exists():
                    self.helper.write_bytes(b"fixture only, never executed")

    def test_partial_nonzero_timeout_and_oversized_results_are_unknown_without_retry(self):
        failures = [captured(code=1), Captured(0, b"{}", b""),
                    Captured(0, b"x" * (MAX_OUTPUT + 1), b""),
                    Captured(0, captured().stdout, b"x" * (MAX_OUTPUT + 1)),
                    Captured(True, captured().stdout, b""),
                    subprocess.TimeoutExpired("fixture", TIMEOUT_SECONDS), OSError("fixture")]
        for failure in failures:
            runner = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
            adapter = DirectMonitor(self.binding, runner=runner)
            result = adapter.apply("focus-main")
            self.assertFalse(result["verified"])
            self.assertEqual(result["state"], "unknown")
            self.assertIsNone(result["profile_id"])
            self.assertIsNone(result["receipt"])
            runner.assert_called_once()
            self.assertTrue(DirectMonitor(self.binding, runner=Mock(return_value=captured())).apply("focus-main")["verified"])

    def test_nonblocking_lock_serializes_separate_adapter_instances(self):
        second_runner = Mock(return_value=captured("shared-four"))
        second = DirectMonitor(self.binding, runner=second_runner)
        def first_runner(argv, **kwargs):
            busy = second.apply("shared-four")
            self.assertEqual(busy["error"], "apply_busy")
            self.assertFalse(busy["verified"])
            return captured()
        self.assertTrue(DirectMonitor(self.binding, runner=first_runner).apply("focus-main")["verified"])
        second_runner.assert_not_called()
        self.assertTrue(second.apply("shared-four")["verified"])

    def test_capture_bounds_pipes_and_stops_only_retained_fake_child_on_failure(self):
        class FakeSelector:
            def __init__(self):
                self.items = {}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def register(self, file, event, name):
                self.items[file] = SimpleNamespace(fileobj=file, data=name)
            def unregister(self, file):
                del self.items[file]
            def get_map(self):
                return self.items
            def select(self, timeout):
                return [(key, 1) for key in list(self.items.values())]

        for scenario in ("success", "overflow", "timeout"):
            with self.subTest(scenario=scenario):
                process = Mock()
                process.stdout.fileno.return_value = 11
                process.stderr.fileno.return_value = 12
                process.poll.return_value = 0 if scenario == "success" else None
                process.wait.return_value = 0
                streams = {11: [b"x" * (MAX_OUTPUT + 1) if scenario == "overflow" else b"ok", b""],
                           12: [b"", b""]}
                def read(fd, bound):
                    value = streams[fd].pop(0)
                    self.assertLessEqual(len(value), bound)
                    return value
                clock = {"side_effect": [0, TIMEOUT_SECONDS + 1]} if scenario == "timeout" else {"return_value": 0}
                with patch("deskdeck.direct_monitor.subprocess.Popen", return_value=process) as launch, \
                        patch("deskdeck.direct_monitor.selectors.DefaultSelector", FakeSelector), \
                        patch("deskdeck.direct_monitor.os.read", side_effect=read), \
                        patch("deskdeck.direct_monitor.time.monotonic", **clock):
                    if scenario == "success":
                        self.assertEqual(_capture(["/fake-owned-helper"], timeout=TIMEOUT_SECONDS,
                                                  max_output=MAX_OUTPUT), Captured(0, b"ok", b""))
                        process.kill.assert_not_called()
                    else:
                        with self.assertRaisesRegex(MonitorError, "helper_output_limit" if scenario == "overflow" else "helper_timeout"):
                            _capture(["/fake-owned-helper"], timeout=TIMEOUT_SECONDS, max_output=MAX_OUTPUT)
                        process.kill.assert_called_once_with()
                        process.wait.assert_called_once_with(timeout=1)
                launch.assert_called_once()
                self.assertFalse(launch.call_args.kwargs["shell"])
                self.assertEqual(launch.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
                process.stdout.close.assert_called_once_with()
                process.stderr.close.assert_called_once_with()

    def test_unconfirmed_child_blocks_new_apply_until_exact_child_exit_is_confirmed(self):
        for failure in ("wait-timeout", "poll-error", "kill-error"):
            with self.subTest(failure=failure):
                process = Mock()
                process.poll.return_value = None
                if failure == "wait-timeout":
                    process.wait.side_effect = subprocess.TimeoutExpired("owned fixture", 1)
                elif failure == "poll-error":
                    process.poll.side_effect = OSError("inconclusive owned poll")
                else:
                    process.kill.side_effect = OSError("inconclusive owned kill")
                selector = Mock()
                selector.__enter__ = Mock(return_value=selector)
                selector.__exit__ = Mock(return_value=False)
                selector.get_map.return_value = {"fake-open-pipe": True}
                with patch("deskdeck.direct_monitor.subprocess.Popen", return_value=process) as launch, \
                        patch("deskdeck.direct_monitor.selectors.DefaultSelector", return_value=selector), \
                        patch("deskdeck.direct_monitor.time.monotonic", side_effect=[0, TIMEOUT_SECONDS + 1]):
                    result = DirectMonitor(self.binding).apply("focus-main")
                self.assertEqual(result["error"], "helper_exit_unconfirmed")
                self.assertFalse(result["verified"])
                self.assertIsNone(result["profile_id"])
                launch.assert_called_once()
                kill_count = process.kill.call_count
                second_runner = Mock(return_value=captured())
                second = DirectMonitor(self.binding, runner=second_runner)
                self.assertEqual(second.apply("focus-main")["error"], "helper_exit_unconfirmed")
                second_runner.assert_not_called()
                self.assertEqual(process.kill.call_count, kill_count)
                # Only an observation of this exact retained child exiting can
                # permit a later explicit Apply. No extra stop/restart occurs.
                process.poll.side_effect = None
                process.poll.return_value = -9
                self.assertTrue(second.apply("focus-main")["verified"])
                second_runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
