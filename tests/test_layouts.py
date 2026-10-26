import json
import math
import unittest

from deskdeck.layouts import get_layout, layout_catalog


class NativeLayoutTests(unittest.TestCase):
    def test_catalog_is_complete_and_serializable(self):
        catalog = layout_catalog()
        self.assertEqual(len(catalog), 32)
        self.assertEqual(len({layout["id"] for layout in catalog}), len(catalog))
        self.assertEqual(json.loads(json.dumps(catalog)), catalog)
        for kind in ("pbp", "screen-partition"):
            self.assertEqual({layout["native_configuration"] for layout in catalog
                              if layout["kind"] == kind}, set(range(1, 16)))

    def test_every_geometry_tiles_the_panel_without_overlap_or_holes(self):
        for layout in layout_catalog():
            with self.subTest(layout=layout["id"]):
                regions = layout["regions"]
                self.assertEqual(len({r["id"] for r in regions}), len(regions))
                self.assertEqual({r["input_slot"] for r in regions},
                                 set(range(1, layout["input_slots"] + 1)))
                for r in regions:
                    self.assertTrue(all(math.isfinite(r[key]) for key in ("x", "y", "width", "height")))
                    self.assertGreater(r["width"], 0)
                    self.assertGreater(r["height"], 0)
                    self.assertGreaterEqual(r["x"], 0)
                    self.assertGreaterEqual(r["y"], 0)
                    self.assertLessEqual(r["x"] + r["width"], 1 + 1e-12)
                    self.assertLessEqual(r["y"] + r["height"], 1 + 1e-12)
                self.assertAlmostEqual(sum(r["width"] * r["height"] for r in regions), 1)
                for i, left in enumerate(regions):
                    for right in regions[i + 1:]:
                        overlap_x = min(left["x"] + left["width"], right["x"] + right["width"]) - max(left["x"], right["x"])
                        overlap_y = min(left["y"] + left["height"], right["y"] + right["height"]) - max(left["y"], right["y"])
                        self.assertTrue(overlap_x <= 1e-12 or overlap_y <= 1e-12)

    def test_partition_streams_do_not_create_extra_physical_inputs(self):
        for number in range(1, 16):
            pbp = get_layout(f"pbp-{number:02d}")
            partition = get_layout(f"screen-partition-{number:02d}")
            self.assertEqual(partition["input_slots"], 1)
            self.assertTrue(partition["capabilities"]["imst_required"])
            self.assertEqual(pbp["input_slots"], len(pbp["regions"]))
            self.assertFalse(pbp["capabilities"]["imst_required"])
            for p, s in zip(pbp["regions"], partition["regions"]):
                self.assertEqual({k: v for k, v in p.items() if k != "input_slot"},
                                 {k: v for k, v in s.items() if k != "input_slot"})
                self.assertEqual(s["input_slot"], 1)
        mixed = get_layout("pbp-with-screen-partition")
        self.assertEqual(mixed["input_slots"], 2)
        self.assertEqual({r["input_slot"] for r in mixed["regions"] if r["y"] == 0}, {1})
        self.assertEqual({r["input_slot"] for r in mixed["regions"] if r["y"] == .5}, {2})

    def test_manual_numbering_and_asymmetric_pixel_boundary_are_preserved(self):
        quad = get_layout("pbp-15")["regions"]
        self.assertEqual([(r["id"], r["x"], r["y"]) for r in quad],
                         [("win1", 0, 0), ("win2", .5, 0), ("win3", .5, .5), ("win4", 0, .5)])
        self.assertEqual(get_layout("pbp-10")["regions"][2]["id"], "win3")
        self.assertEqual(get_layout("pbp-10")["regions"][2]["x"], 0)
        self.assertEqual(get_layout("pbp-12")["regions"][1]["x"], .5)
        self.assertEqual(get_layout("pbp-12")["regions"][2]["x"], 0)
        uneven = get_layout("pbp-07")["regions"]
        self.assertEqual(uneven[0]["height"], 853 / 2560)
        self.assertEqual(uneven[1]["y"], 853 / 2560)
        self.assertEqual(uneven[1]["height"], 1707 / 2560)

    def test_no_catalog_entry_claims_a_hardware_control_contract(self):
        for layout in layout_catalog():
            self.assertFalse(layout["capabilities"]["hardware_control_qualified"])
            self.assertIsNone(layout["capabilities"]["hardware_mode_code"])
            self.assertTrue(layout["source"]["url"].startswith("https://dl.dell.com/"))
            self.assertTrue(layout["source"]["printed_pages"])
        with self.assertRaises(ValueError):
            get_layout("pbp-65")

    def test_caller_mutations_cannot_rewrite_the_catalog(self):
        expected = get_layout("pbp-11")
        changed = get_layout("pbp-11")
        changed["regions"][0]["input_slot"] = 99
        changed["capabilities"]["input_families"].clear()
        changed["source"]["printed_pages"].clear()
        changed["notes"].clear()
        self.assertEqual(get_layout("pbp-11"), expected)


if __name__ == "__main__":
    unittest.main()
