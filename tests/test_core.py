import unittest

from deskdeck.core import get_profile, plan_profile, profiles, render_deskflow


class CompleteProfileTests(unittest.TestCase):
    def test_focus_then_shared_restores_every_region(self):
        original = plan_profile("shared")["steps"]
        for profile in profiles()[1:]:
            plan_profile(profile["id"])
            restored = plan_profile("shared")
            self.assertEqual(restored["steps"], original)
            self.assertEqual(restored["profile"]["monitor"]["regions"],
                             {"left": "main", "top_right": "linux", "bottom_right": "mac"})

    def test_caller_changes_cannot_rewrite_saved_profile(self):
        bad = get_profile("shared")
        bad["monitor"]["regions"]["top_right"] = "windows"
        self.assertEqual(get_profile("shared")["monitor"]["regions"]["top_right"], "linux")

    def test_unqualified_plans_never_claim_ready(self):
        for profile in profiles():
            self.assertFalse(plan_profile(profile["id"])["ready"])
            self.assertTrue(plan_profile(profile["id"])["blockers"])

    def test_draft_rejects_config_injection_and_duplicate_names(self):
        for value in ["host\nend\nsection: options", "two words", "x:main"]:
            with self.assertRaises(ValueError):
                render_deskflow("shared", {"main": value, "mac": "mac", "linux": "linux", "windows": "windows"})
        with self.assertRaises(ValueError):
            render_deskflow("shared", dict.fromkeys(("main", "mac", "linux", "windows"), "same"))

    def test_focus_drafts_do_not_route_through_hidden_computers(self):
        focus = render_deskflow("focus-mac")
        links = focus.split("section: links\n")[1].split("\nend")[0]
        self.assertNotIn("linux", links)
        self.assertIn("right = mac", links)


if __name__ == "__main__":
    unittest.main()
