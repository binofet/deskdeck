from dataclasses import FrozenInstanceError, replace
from fractions import Fraction as F
import json
import unittest
from unittest.mock import patch

from deskdeck.edge_routes import (
    DisplayGeometry, GeometryError, HostGeometry, Rect, RegionBinding, plan_edge_routes,
)
from deskdeck.layouts import get_layout, layout_catalog


def measured(layout, *, grouped=False):
    """Synthetic receipts only; groups use physical-proportional OS rectangles."""
    by_host, bindings = {}, []
    for region in layout["regions"]:
        host_id = "host" + str(region["input_slot"] if grouped else region["id"])
        display_id = "display-" + region["id"]
        bounds = (Rect(region["x"] * 6144, region["y"] * 2560,
                       region["width"] * 6144, region["height"] * 2560)
                  if grouped else Rect(0, 0, 1200, 900))
        by_host.setdefault(host_id, []).append(DisplayGeometry(display_id, bounds, 0, False))
        bindings.append(RegionBinding(region["id"], host_id, display_id, bounds, True))
    hosts = []
    for host_id, displays in by_host.items():
        bounds = [display.bounds for display in displays]
        desktop = Rect(min(b.x for b in bounds), min(b.y for b in bounds),
                       max(b.right for b in bounds) - min(b.x for b in bounds),
                       max(b.bottom for b in bounds) - min(b.y for b in bounds))
        hosts.append(HostGeometry(host_id, host_id, desktop, tuple(displays), layout["id"], True, True))
    return bindings, hosts


def codes(plan):
    return {blocker.code for blocker in plan.blockers}


class EdgeRouteTests(unittest.TestCase):
    def test_unknown_geometry_and_missing_receipts_are_explicitly_not_ready(self):
        for layout in ({}, {"id": "unknown", "regions": []}, "not-a-native-layout", get_layout("pbp-01")):
            with self.subTest(layout=layout):
                plan = plan_edge_routes(layout)
                self.assertFalse(plan.ready)
                self.assertIsNone(plan.preview_links())
                self.assertFalse(plan.to_dict()["activation_available"])
        layout = get_layout("pbp-01")
        layout["regions"][0]["width"] = None
        self.assertIn("unknown_or_invalid_region_geometry", codes(plan_edge_routes(layout)))

    def test_equal_halves_are_reciprocal_and_preserve_lowercase_names(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        hosts = [replace(hosts[0], computer_name="quasar"), replace(hosts[1], computer_name="pulsar")]
        plan = plan_edge_routes("pbp-01", bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        self.assertEqual(len(plan.links), 2)
        first, second = plan.links
        self.assertEqual((first.source_name, first.target_name), ("quasar", "pulsar"))
        self.assertEqual((first.side, second.side), ("right", "left"))
        self.assertEqual(first.source_percent, ("0", "100"))
        self.assertEqual(first.source_percent, second.target_percent)
        self.assertEqual(first.target_percent, second.source_percent)
        self.assertIn("right(0,100) = pulsar(0,100)", plan.preview_links())
        self.assertFalse(plan.to_dict()["physical_edges_verified"])
        self.assertTrue(plan.to_dict()["hotkeys_unchanged"])
        json.dumps(plan.to_dict())

    def test_left_full_and_stacked_right_match_partial_to_full_edges(self):
        layout = get_layout("pbp-11")
        bindings, hosts = measured(layout)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        outgoing = [link for link in plan.links if link.source_region == "win1"]
        self.assertEqual([link.source_percent for link in outgoing], [("0", "50"), ("50", "100")])
        self.assertTrue(all(link.target_percent == ("0", "100") for link in outgoing))
        for link in plan.links:
            inverse = [candidate for candidate in plan.links
                       if candidate.source_region == link.target_region and candidate.target_region == link.source_region]
            self.assertEqual(len(inverse), 1)
            self.assertEqual(inverse[0].source_percent, link.target_percent)
            self.assertEqual(inverse[0].target_percent, link.source_percent)

    def test_laptop_on_seam_side_prevents_false_whole_computer_link(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        # The second host's Dell is to the right of its laptop, so its left edge
        # is internal to that computer and cannot accept the incoming link.
        laptop = DisplayGeometry("laptop", Rect(-800, 0, 800, 900), 0, False)
        hosts[1] = replace(hosts[1], desktop=Rect(-800, 0, 2000, 900),
                           displays=(laptop, *hosts[1].displays))
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertFalse(plan.ready)
        self.assertIn("seam_not_representable_on_computer_edges", codes(plan))
        self.assertEqual(plan.links, ())  # Never emit only half of the pair.

    def test_extra_laptop_above_uses_actual_enclosing_height_and_negative_origin(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        laptop = DisplayGeometry("laptop", Rect(0, -900, 1200, 900), 0, False)
        hosts[0] = replace(hosts[0], desktop=Rect(0, -900, 1200, 1800),
                           displays=(laptop, *hosts[0].displays))
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        first = next(link for link in plan.links if link.source_region == "win1")
        self.assertEqual(first.source_percent, ("50", "100"))
        self.assertEqual(first.target_percent, ("0", "100"))

    def test_same_host_seams_are_internal_only_when_os_displays_really_align(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout, grouped=True)
        # Distinct physical input selectors may belong to the same computer.
        displays = tuple(display for host in hosts for display in host.displays)
        host = HostGeometry("one", "quasar", Rect(0, 0, 6144, 2560), displays, layout["id"], True, True)
        bindings = [replace(binding, host_id="one") for binding in bindings]
        plan = plan_edge_routes(layout, bindings, [host])
        self.assertTrue(plan.ready, plan.blockers)
        self.assertEqual(plan.links, ())
        self.assertEqual(plan.seams[0].kind, "same_host_internal")
        moved = replace(displays[1], bounds=Rect(7000, 0, 3072, 2560))
        host = replace(host, desktop=Rect(0, 0, 10072, 2560), displays=(displays[0], moved))
        bindings[1] = replace(bindings[1], viewport=moved.bounds)
        self.assertIn("internal_os_seam_misaligned", codes(plan_edge_routes(layout, bindings, [host])))

    def test_same_input_imst_is_per_stream_display_not_multiple_hosts(self):
        layout = get_layout("screen-partition-15")
        bindings, hosts = measured(layout, grouped=True)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        self.assertEqual(len(hosts[0].displays), 4)
        self.assertEqual(plan.links, ())
        self.assertTrue(all(seam.kind == "same_input_internal" for seam in plan.seams))
        bindings, hosts = measured(layout, grouped=False)
        self.assertIn("one_input_group_has_multiple_hosts", codes(plan_edge_routes(layout, bindings, hosts)))

    def test_mixed_partition_keeps_internal_row_seams_and_inter_host_vertical_routes(self):
        layout = get_layout("pbp-with-screen-partition")
        bindings, hosts = measured(layout, grouped=True)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        self.assertEqual(len(plan.links), 4)
        self.assertTrue(all(link.side in {"up", "down"} for link in plan.links))
        self.assertEqual(sum(seam.kind == "same_input_internal" for seam in plan.seams), 2)

    def test_corner_contact_never_becomes_a_route(self):
        layout = get_layout("pbp-15")
        bindings, hosts = measured(layout)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertTrue(plan.ready, plan.blockers)
        pairs = {(link.source_region, link.target_region) for link in plan.links}
        self.assertNotIn(("win1", "win3"), pairs)
        self.assertNotIn(("win2", "win4"), pairs)
        self.assertEqual(len(plan.links), 8)

    def test_catalog_ratios_and_every_native_mode_are_consumed_without_geometry_guessing(self):
        for layout in layout_catalog():
            with self.subTest(layout=layout["id"]):
                grouped = layout["kind"] != "pbp"
                bindings, hosts = measured(layout, grouped=grouped)
                plan = plan_edge_routes(layout, bindings, hosts)
                self.assertTrue(plan.ready, plan.blockers)
                self.assertFalse(plan.to_dict()["activation_available"])
        layout = get_layout("pbp-07")
        self.assertNotEqual(F(str(layout["regions"][0]["height"])), F(1, 3))

    def test_incomplete_stale_rotated_mirrored_or_unknown_display_data_refuses(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        cases = [replace(hosts[0], complete=False), replace(hosts[0], coordinate_space_verified=False),
                 replace(hosts[0], layout_id="pbp-02"), replace(hosts[0], desktop=None),
                 replace(hosts[0], desktop=Rect(0, 0, 1300, 900)),
                 replace(hosts[0], displays=(replace(hosts[0].displays[0], rotation=90),)),
                 replace(hosts[0], displays=(replace(hosts[0].displays[0], mirrored=True),)),
                 replace(hosts[0], displays=(replace(hosts[0].displays[0], bounds=None),))]
        for changed in cases:
            self.assertFalse(plan_edge_routes(layout, bindings, [changed, hosts[1]]).ready)
        self.assertFalse(plan_edge_routes(layout, [replace(bindings[0], mapping_verified=False), bindings[1]], hosts).ready)
        self.assertFalse(plan_edge_routes(layout, [replace(bindings[0], display_id="unknown"), bindings[1]], hosts).ready)

    def test_overlapping_regions_and_duplicate_views_refuse(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        layout["regions"][1]["x"] = 0.4
        self.assertIn("overlapping_regions", codes(plan_edge_routes(layout, bindings, hosts)))
        layout = get_layout("screen-partition-01")
        bindings, hosts = measured(layout, grouped=True)
        bindings[1] = replace(bindings[1], display_id=bindings[0].display_id, viewport=bindings[0].viewport)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertIn("duplicate_or_overlapping_os_viewports", codes(plan))
        self.assertFalse(plan.ready)

    def test_float32_collapsed_segment_is_not_emitted_as_a_zero_length_route(self):
        tiny = F(1, 10**12)
        layout = {"id": "thin-fixture", "input_slots": 4, "regions": [
            {"id": "left", "x": 0, "y": 0, "width": F(1, 2), "height": 1, "input_slot": 1},
            {"id": "top", "x": F(1, 2), "y": 0, "width": F(1, 2), "height": F(1, 2), "input_slot": 2},
            {"id": "thin", "x": F(1, 2), "y": F(1, 2), "width": F(1, 2), "height": tiny, "input_slot": 3},
            {"id": "bottom", "x": F(1, 2), "y": F(1, 2) + tiny, "width": F(1, 2), "height": F(1, 2) - tiny, "input_slot": 4},
        ]}
        bindings, hosts = measured(layout)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertIn("seam_not_representable_on_computer_edges", codes(plan))
        self.assertFalse(plan.ready)
        self.assertIsNone(plan.preview_links())

    def test_positive_subpixel_seam_with_no_cursor_sample_is_not_ready(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        for index in range(2):
            tiny = Rect(0, 0, 1200, F(1, 10))
            hosts[index] = replace(hosts[index], desktop=tiny,
                                   displays=(replace(hosts[index].displays[0], bounds=tiny),))
            bindings[index] = replace(bindings[index], viewport=tiny)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertFalse(plan.ready)
        self.assertIn("seam_not_representable_on_computer_edges", codes(plan))

    def test_float32_rounding_must_preserve_reachable_cursor_sample_membership(self):
        # The first cut includes y=0.5 before conversion, but its emitted
        # 0.048828125098 percent becomes exactly y=0.5 as float32: [0, 0.5)
        # loses the only sample and the reciprocal lower seam would gain it.
        cut = F(500000001, 1024000000000)
        layout = {"id": "sample-rounding", "input_slots": 3, "regions": [
            {"id": "left", "x": 0, "y": 0, "width": F(1, 2), "height": 1, "input_slot": 1},
            {"id": "top", "x": F(1, 2), "y": 0, "width": F(1, 2), "height": cut, "input_slot": 2},
            {"id": "bottom", "x": F(1, 2), "y": cut, "width": F(1, 2), "height": 1 - cut, "input_slot": 3},
        ]}
        bindings, hosts = measured(layout)
        left = Rect(0, 0, 1200, 1024)
        hosts[0] = replace(hosts[0], desktop=left,
                           displays=(replace(hosts[0].displays[0], bounds=left),))
        bindings[0] = replace(bindings[0], viewport=left)
        plan = plan_edge_routes(layout, bindings, hosts)
        self.assertFalse(plan.ready)
        self.assertIn("seam_not_representable_on_computer_edges", codes(plan))
        self.assertIsNone(plan.preview_links())
        self.assertFalse(any(link.source_region == "left" for link in plan.links))

    def test_caseless_name_collision_refuses_without_renaming(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        hosts = [replace(hosts[0], computer_name="quasar"), replace(hosts[1], computer_name="Quasar")]
        self.assertIn("caseless_computer_name_collision", codes(plan_edge_routes(layout, bindings, hosts)))

    def test_os_display_ids_are_opaque_and_not_confused_with_computer_names(self):
        layout = get_layout("pbp-01")
        bindings, hosts = measured(layout)
        opaque = r"\\.\DISPLAY1"
        hosts[0] = replace(hosts[0], displays=(replace(hosts[0].displays[0], display_id=opaque),))
        bindings[0] = replace(bindings[0], display_id=opaque)
        self.assertTrue(plan_edge_routes(layout, bindings, hosts).ready)
        bindings[0] = replace(bindings[0], display_id="bad\nidentifier")
        self.assertFalse(plan_edge_routes(layout, bindings, hosts).ready)

    def test_planning_is_immutable_deterministic_and_has_no_io(self):
        layout = get_layout("pbp-11")
        bindings, hosts = measured(layout)
        with patch("builtins.open") as opened, patch("subprocess.Popen") as spawned, patch("socket.socket") as socket:
            first = plan_edge_routes(layout, bindings, hosts)
            second = plan_edge_routes(layout, reversed(bindings), reversed(hosts))
        opened.assert_not_called()
        spawned.assert_not_called()
        socket.assert_not_called()
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.layout_id = "changed"
        with self.assertRaises(GeometryError):
            Rect(0, 0, 0, 1)
        with self.assertRaises(GeometryError):
            Rect(0, 0, float("nan"), 1)


if __name__ == "__main__":
    unittest.main()
