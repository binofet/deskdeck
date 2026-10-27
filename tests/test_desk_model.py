import copy
import json
from pathlib import Path
import unittest

from deskdeck.desk_model import (
    DeskModelError, MAX_MODEL_BYTES, cursor_anchors, parse_desk_model,
    remove_computer, validate_desk_model,
)


def display(identifier="panel", *, anchor="left", kind="built-in"):
    return {"id": identifier, "name": "lowercase panel", "kind": kind, "anchor": anchor}


def computer(identifier="laptop", *, displays=None):
    return {"id": identifier, "name": identifier, "displays": displays if displays is not None else []}


def model(*computers, owner=None):
    return {"schema_version": 1, "computers": list(computers), "input_owner": owner}


class DeskModelTests(unittest.TestCase):
    def test_names_are_preserved_and_headless_input_owner_is_not_a_cursor_anchor(self):
        value = model(computer("laptop", displays=[display()]), computer("compute"), owner="compute")
        result = validate_desk_model(value)
        self.assertEqual(result["computers"][0]["name"], "laptop")
        self.assertEqual(result["computers"][0]["displays"][0]["name"], "lowercase panel")
        self.assertEqual(result["input_owner"], "compute")
        self.assertEqual([a["computer_id"] for a in cursor_anchors(value)], ["laptop"])
        self.assertIsNone(result["computers"][1]["dell_input"])
        self.assertEqual(validate_desk_model(model()), model())

    def test_each_call_returns_independent_nested_data(self):
        value = model(computer(displays=[{**display(), "placement": {"x": 0, "y": 0, "width": 1, "height": 1}}]))
        before = copy.deepcopy(value)
        first = validate_desk_model(value)
        first["computers"][0]["displays"][0]["placement"]["x"] = 0.5
        first["computers"][0]["name"] = "changed"
        anchor = cursor_anchors(value)[0]
        anchor["placement"]["width"] = 0.5
        self.assertEqual(value, before)
        self.assertEqual(validate_desk_model(value)["computers"][0]["name"], "laptop")

    def test_dell_anchor_requires_external_display_and_assigned_video_port(self):
        value = model(computer(displays=[display(anchor="dell", kind="external")]))
        with self.assertRaisesRegex(DeskModelError, "Dell anchor"):
            validate_desk_model(value)
        value["computers"][0]["dell_input"] = "HDMI1"
        self.assertEqual(cursor_anchors(value)[0]["anchor"], "dell")
        value["computers"][0]["displays"][0]["kind"] = "built-in"
        with self.assertRaisesRegex(DeskModelError, "Dell anchor"):
            validate_desk_model(value)

    def test_unplaced_display_has_no_anchor_or_rectangle(self):
        value = model(computer(displays=[display(anchor="unplaced")]))
        self.assertEqual(cursor_anchors(value), [])
        value["computers"][0]["displays"][0]["placement"] = {"x": 0, "y": 0, "width": 1, "height": 1}
        with self.assertRaisesRegex(DeskModelError, "unplaced"):
            validate_desk_model(value)

    def test_normalized_rectangles_reject_nonfinite_bool_overflow_and_out_of_bounds(self):
        for rectangle in (
            {"x": -0.1, "y": 0, "width": 1, "height": 1},
            {"x": 0.5, "y": 0, "width": 0.6, "height": 1},
            {"x": 0, "y": 0, "width": 0, "height": 1},
            {"x": True, "y": 0, "width": 1, "height": 1},
            {"x": float("nan"), "y": 0, "width": 1, "height": 1},
            {"x": 0, "y": float("inf"), "width": 1, "height": 1},
            {"x": 10**1000, "y": 0, "width": 1, "height": 1},
        ):
            with self.subTest(rectangle=rectangle):
                with self.assertRaises(DeskModelError):
                    validate_desk_model(model(computer(displays=[{**display(), "placement": rectangle}])))

    def test_unknown_fields_dangling_owner_and_injected_identifiers_are_rejected(self):
        invalid = [
            {**model(), "hardware_verified": True},
            model(computer(), owner="missing"),
            model({**computer(), "anchor": "left"}),
            model(computer("Laptop")),
            model(computer("a\n[server]")),
            model(computer(displays=[{**display(), "computer_id": "missing"}])),
            model({**computer(), "name": " a "}),
            model({**computer(), "name": "hello\nthere"}),
            {**model(), "schema_version": True},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(DeskModelError):
                    validate_desk_model(value)

    def test_ids_and_assigned_physical_ports_are_unique(self):
        invalid = [
            model(computer(), computer()),
            model(computer("a", displays=[display()]), computer("b", displays=[display()])),
            model({**computer("a"), "dell_input": "DP1"}, {**computer("b"), "dell_input": "DP1"}),
            model({**computer("a"), "dell_input": "USB-C"}, {**computer("b"), "dell_input": "Thunderbolt"}),
            model({**computer("a"), "usb_upstream": "USB2"}, {**computer("b"), "usb_upstream": "USB2"}),
            model({**computer(), "usb_upstream": "USB5"}),
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(DeskModelError):
                    validate_desk_model(value)

    def test_usb_c_video_alias_has_one_canonical_physical_port(self):
        result = validate_desk_model(model({**computer(), "dell_input": "USB-C"}))
        self.assertEqual(result["computers"][0]["dell_input"], "Thunderbolt")

    def test_host_and_total_display_bounds_hold_across_multiple_computers(self):
        validate_desk_model(model(*(computer(f"host{i}") for i in range(16))))
        with self.assertRaises(DeskModelError):
            validate_desk_model(model(*(computer(f"host{i}") for i in range(17))))
        a = computer("a", displays=[display(f"p{i}") for i in range(8)])
        b = computer("b", displays=[display(f"q{i}") for i in range(8)])
        validate_desk_model(model(a, b))
        b["displays"].append(display("extra"))
        with self.assertRaises(DeskModelError):
            validate_desk_model(model(a, b))

    def test_bounded_parser_refuses_duplicate_fields_nonfinite_and_invalid_utf8(self):
        for raw in (
            b'\xff', b'{}', b'{"schema_version":1,"schema_version":1,"computers":[],"input_owner":null}',
            b'{"schema_version":1,"computers":[],"input_owner":NaN}',
            b" " * (MAX_MODEL_BYTES + 1), b"[" * 1500 + b"]" * 1500,
        ):
            with self.subTest(raw_prefix=raw[:80]):
                with self.assertRaises(DeskModelError):
                    parse_desk_model(raw)
        self.assertEqual(parse_desk_model(json.dumps(model())), model())

    def test_removal_refuses_references_and_never_mutates_original(self):
        value = model(computer("a", displays=[display()]), computer("b"), owner="a")
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(DeskModelError, "referenced"):
            remove_computer(value, "a")
        for target in ("missing", "a"):
            with self.assertRaises(DeskModelError):
                remove_computer(value, "a", reassign_to=target)
        removed = remove_computer(value, "a", reassign_to="b")
        self.assertEqual(removed["input_owner"], "b")
        self.assertEqual(removed["computers"][0]["displays"][0]["id"], "panel")
        self.assertEqual(value, before)
        self.assertEqual(remove_computer(model(computer("unused")), "unused"), model())

    def test_explicit_reassignment_does_not_silently_move_monitor_wiring(self):
        a = {**computer("a", displays=[display(anchor="dell", kind="external")]), "dell_input": "DP1"}
        b = computer("b")
        value = model(a, b, owner="a")
        with self.assertRaisesRegex(DeskModelError, "Dell anchor"):
            remove_computer(value, "a", reassign_to="b")
        value["computers"][1]["dell_input"] = "HDMI1"
        result = remove_computer(value, "a", reassign_to="b")
        self.assertEqual(result["computers"][0]["dell_input"], "HDMI1")
        self.assertIsNone(result["computers"][0]["usb_upstream"])

    def test_public_generic_examples_validate_without_personal_bindings(self):
        folder = Path(__file__).parents[1] / "examples"
        paths = sorted(folder.glob("desk-model-*.json"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            result = parse_desk_model(path.read_bytes())
            self.assertEqual(result, validate_desk_model(result))
        headless = parse_desk_model((folder / "desk-model-desktop-headless.json").read_bytes())
        self.assertEqual(headless["computers"][1]["displays"], [])


if __name__ == "__main__":
    unittest.main()
