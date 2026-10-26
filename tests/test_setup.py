import copy
import json
from pathlib import Path
import tempfile
import unittest

from deskdeck.core import plan_profile, profiles
from deskdeck.setup import load_setup, setup_summary, validate_setup


PLANNED = {
    "schema_version": 1,
    "source_inputs": {"main": "Thunderbolt", "linux": "DP1", "mac": "HDMI2", "windows": "HDMI1"},
    "reserved_inputs": {"future": "DP2"},
    "keyboard_owner": None,
}


class PlannedWiringTests(unittest.TestCase):
    def test_shared_and_focus_use_the_complete_selected_port_map(self):
        shared = plan_profile("shared", setup=PLANNED)
        self.assertEqual(shared["steps"][1]["details"], {
            "left": {"device": "main", "port": "Thunderbolt"},
            "top_right": {"device": "linux", "port": "DP1"},
            "bottom_right": {"device": "mac", "port": "HDMI2"},
        })
        self.assertEqual(plan_profile("focus-windows", setup=PLANNED)["steps"][1]["details"],
                         {"main": {"device": "windows", "port": "HDMI1"}})
        self.assertEqual(plan_profile("shared", setup=PLANNED), shared)

    def test_reservation_does_not_create_an_active_source_or_profile(self):
        self.assertEqual(len(profiles()), 5)
        for p in profiles():
            plan = plan_profile(p["id"], setup=PLANNED)
            self.assertFalse(plan["ready"])
            self.assertFalse(plan["setup"]["hardware_verified"])
            self.assertEqual(plan["setup"]["reserved_inputs"], {"future": "DP2"})
            self.assertNotIn("DP2", json.dumps(plan["steps"]))

    def test_duplicate_missing_and_injected_ports_refuse(self):
        cases = []
        duplicate = copy.deepcopy(PLANNED)
        duplicate["source_inputs"]["mac"] = "DP1"
        cases.append(duplicate)
        reserved = copy.deepcopy(PLANNED)
        reserved["reserved_inputs"]["future"] = "HDMI1"
        cases.append(reserved)
        missing = copy.deepcopy(PLANNED)
        del missing["source_inputs"]["windows"]
        cases.append(missing)
        injected = copy.deepcopy(PLANNED)
        injected["source_inputs"]["windows"] = "HDMI1; arbitrary command"
        cases.append(injected)
        claimed = copy.deepcopy(PLANNED)
        claimed["hardware_verified"] = True
        cases.append(claimed)
        for case in cases:
            with self.assertRaises(ValueError):
                validate_setup(case)

    def test_normalized_setup_is_independent_and_does_not_claim_hardware(self):
        normalized = validate_setup(PLANNED)
        summary = setup_summary(normalized)
        summary["source_inputs"]["main"] = "HDMI1"
        self.assertEqual(normalized["source_inputs"]["main"], "Thunderbolt")
        self.assertEqual(setup_summary(normalized)["evidence"], "user_plan")
        self.assertFalse(setup_summary(normalized)["hardware_verified"])

    def test_explicit_file_load_and_invalid_content_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "desk.local.json"
            path.write_text(json.dumps(PLANNED))
            self.assertEqual(load_setup(path), PLANNED)
            for raw in [b'{"schema_version":1,"schema_version":1}', b'\xff', b'x' * 16385, b'null']:
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "Could not load a valid local setup file"):
                    load_setup(path)
            with self.assertRaises(ValueError):
                load_setup(Path(directory))


if __name__ == "__main__":
    unittest.main()
