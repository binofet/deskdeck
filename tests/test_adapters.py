import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from deskdeck.adapters import (
    BindingError, CommandFailed, CommandTimeout, DDPMAdapter, ExecutionDisabled,
    InvalidPlan, OutputLimitExceeded, UnsupportedFeature,
)


def binding():
    return {
        "monitor": {"verified": True, "model": "U5226KW",
                    "selector": {"service_tag": "TEST001"},
                    "capabilities": ["identity", "firmware", "active_input", "pxp_off"]},
        "inputs": {"main": {"verified": True, "port": "Thunderbolt"},
                   "mac": {"verified": True, "port": "HDMI1"},
                   "linux": {"verified": True, "port": "DP"}},
    }


FULLSCREEN = {"monitor": {"mode": "fullscreen", "regions": {"main": "main"}}}
PBP3 = {"monitor": {"mode": "pbp3", "layout": "left-large-right-stack",
                    "regions": {"left": "main", "top_right": "linux", "bottom_right": "mac"}}}
IDENTITY = {"DeviceType": "Display", "Model": "U5226KW", "ServiceTag": "TEST001",
            "Index": "1", "Result": "PASS"}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="deskdeck-fixture-")
        self.addCleanup(self.directory.cleanup)

    def executable(self, body):
        file = Path(self.directory.name) / "fixture-ddpm"
        file.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
        file.chmod(0o700)
        return str(file)

    def test_preview_never_executes_and_keeps_complete_fullscreen_intent(self):
        adapter = DDPMAdapter()
        steps = adapter.prepare(FULLSCREEN, binding())
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            result = adapter.apply(steps)
        popen.assert_not_called()
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["executed"])
        self.assertTrue(result["blocked"])
        self.assertEqual([step["operation"] for step in steps], [
            "read_identity", "read_firmware", "set_pxp_off", "set_active_input", "read_active_input"])
        self.assertIn("-value=Thunderbolt", steps[3]["argv"])

    def test_unknown_monitor_refuses_before_any_execution(self):
        data = binding()
        data["monitor"]["model"] = "UNKNOWN"
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            with self.assertRaises(UnsupportedFeature):
                DDPMAdapter(allow_execute=True).prepare(FULLSCREEN, data)
        popen.assert_not_called()

    def test_unverified_monitor_refuses(self):
        data = binding()
        data["monitor"]["verified"] = False
        with self.assertRaises(BindingError):
            DDPMAdapter().prepare(FULLSCREEN, data)

    def test_complete_three_input_restore_cannot_skip_missing_mapping(self):
        data = binding()
        del data["inputs"]["linux"]
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            with self.assertRaisesRegex(UnsupportedFeature, "all three ordered region input assignments"):
                DDPMAdapter(allow_execute=True, allow_writes=True).prepare(PBP3, data)
        popen.assert_not_called()

    def test_complete_three_input_binding_cannot_claim_unknown_protocol_support(self):
        data = binding()
        data["monitor"]["capabilities"] += ["pbp3", "complete_region_readback"]
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            with self.assertRaises(UnsupportedFeature):
                DDPMAdapter(allow_execute=True, allow_writes=True).prepare(PBP3, data)
        popen.assert_not_called()

    def test_missing_capability_refuses_before_first_command(self):
        data = binding()
        data["monitor"]["capabilities"].remove("pxp_off")
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            with self.assertRaisesRegex(UnsupportedFeature, "pxp_off"):
                DDPMAdapter().prepare(FULLSCREEN, data)
        popen.assert_not_called()

    def test_unverified_input_and_command_injection_are_rejected(self):
        for bad_port in ({"verified": False, "port": "DP"},
                         {"verified": True, "port": "DP; touch /tmp/unsafe"},
                         {"verified": True, "port": "$(anything)"}, "DP"):
            with self.subTest(port=bad_port):
                data = binding()
                data["inputs"]["main"] = bad_port
                with self.assertRaises(BindingError):
                    DDPMAdapter().prepare(FULLSCREEN, data)

    def test_fullscreen_live_write_refuses_before_even_read_probes(self):
        adapter = DDPMAdapter("/nonexistent/DDPM", allow_execute=True, allow_writes=True)
        steps = adapter.prepare(FULLSCREEN, binding())
        with patch("deskdeck.adapters.subprocess.Popen") as popen:
            with self.assertRaisesRegex(UnsupportedFeature, "PxP is off"):
                adapter.apply(steps)
        popen.assert_not_called()

    def test_explicit_absolute_executable_required_and_no_path_lookup(self):
        with self.assertRaises(BindingError):
            DDPMAdapter("DDPM")
        adapter = DDPMAdapter(allow_execute=True)
        with self.assertRaises(ExecutionDisabled):
            adapter.apply(adapter.prepare_read("identity"))

    def test_reads_use_argv_with_shell_disabled(self):
        script = self.executable("import json\nprint(json.dumps(" + repr(IDENTITY) + "))\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        steps = adapter.prepare_read("identity", binding())
        with patch("deskdeck.adapters.subprocess.Popen", wraps=subprocess.Popen) as popen:
            result = adapter.apply(steps)
        self.assertEqual(popen.call_args.args[0], [script, "/get", "-app=ConnectedDevices"])
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertEqual(result["status"], "read_complete")
        self.assertFalse(result["hardware_verified"])

    def test_firmware_read_targets_one_identity_and_rechecks_discovery(self):
        script = self.executable(
            "import json, sys\n"
            "identity = " + repr(IDENTITY) + "\n"
            "print(json.dumps(identity if '-app=ConnectedDevices' in sys.argv "
            "else dict(identity, Command='GET', TargetFeature='FWVersion', Value='TEST-FW')))\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        steps = adapter.prepare_read("firmware", binding())
        result = adapter.apply(steps)
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(steps[1]["argv"], [script, "/get", "-Display=FWVersion", "-ServiceTag=TEST001"])

    def test_pass_for_another_feature_is_not_the_requested_read(self):
        script = self.executable("import json\nprint(json.dumps(" + repr(IDENTITY) + "))\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        with self.assertRaises(CommandFailed):
            adapter.apply(adapter.prepare_read("firmware", binding()))

    def test_wrong_identity_stops_before_target_read(self):
        wrong = dict(IDENTITY, ServiceTag="OTHER00")
        script = self.executable("import json\nprint(json.dumps(" + repr(wrong) + "))\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        with patch("deskdeck.adapters.subprocess.Popen", wraps=subprocess.Popen) as popen:
            with self.assertRaises(BindingError):
                adapter.apply(adapter.prepare_read("firmware", binding()))
        self.assertEqual(popen.call_count, 1)

    def test_timeout_raises_and_never_returns_success(self):
        script = self.executable("import time\ntime.sleep(0.5)\n")
        adapter = DDPMAdapter(script, allow_execute=True, timeout=0.05)
        with self.assertRaises(CommandTimeout):
            adapter.apply(adapter.prepare_read("identity"))

    def test_output_capture_is_bounded(self):
        script = self.executable("print('x' * 2048)\n")
        adapter = DDPMAdapter(script, allow_execute=True, max_output_bytes=256)
        with self.assertRaises(OutputLimitExceeded):
            adapter.apply(adapter.prepare_read("identity"))

    def test_zero_exit_with_vendor_failure_is_not_success(self):
        script = self.executable("print('{\"Result\":\"FAIL\",\"Message\":\"unsupported\"}')\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        with self.assertRaises(CommandFailed):
            adapter.apply(adapter.prepare_read("identity"))

    def test_nonzero_exit_is_not_success(self):
        script = self.executable("raise SystemExit(3)\n")
        adapter = DDPMAdapter(script, allow_execute=True)
        with self.assertRaises(CommandFailed):
            adapter.apply(adapter.prepare_read("identity"))

    def test_unrecognized_vendor_output_is_not_success(self):
        for output in ("not-json", "{}", "[]"):
            with self.subTest(output=output):
                script = self.executable("print(" + repr(output) + ")\n")
                adapter = DDPMAdapter(script, allow_execute=True)
                with self.assertRaises(CommandFailed):
                    adapter.apply(adapter.prepare_read("identity"))

    def test_tampered_or_foreign_plan_is_never_passthrough(self):
        adapter = DDPMAdapter()
        steps = adapter.prepare_read("identity")
        tampered = copy.deepcopy(steps)
        tampered[0]["argv"].append("/set")
        for plan in (tampered, [{"operation": "firmware_flash"}]):
            with self.assertRaises(InvalidPlan):
                adapter.apply(plan)
        with self.assertRaises(InvalidPlan):
            DDPMAdapter().apply(steps)

    def test_selector_cannot_inject_or_address_all_monitors(self):
        for selector in ({"model": "U5226KW"}, {}, {"index": 0},
                         {"service_tag": "TEST001 /set"},
                         {"index": 1, "service_tag": "TEST001"}):
            with self.subTest(selector=selector):
                data = binding()
                data["monitor"]["selector"] = selector
                with self.assertRaises(BindingError):
                    DDPMAdapter().prepare_read("firmware", data)

    def test_index_is_readonly_and_profile_needs_stable_service_tag(self):
        data = binding()
        data["monitor"]["selector"] = {"index": 1}
        adapter = DDPMAdapter()
        steps = adapter.prepare_read("active_input", data)
        self.assertIn("-Index=1", steps[1]["argv"])
        with self.assertRaises(BindingError):
            adapter.prepare(FULLSCREEN, data)

    def test_invalid_runner_limits_rejected(self):
        for kwargs in ({"timeout": float("nan")}, {"timeout": 0},
                       {"timeout": 100}, {"max_output_bytes": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(BindingError):
                    DDPMAdapter(**kwargs)


if __name__ == "__main__":
    unittest.main()
