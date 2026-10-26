import configparser
from dataclasses import FrozenInstanceError, replace
import hashlib
import unittest
from unittest.mock import patch

from deskdeck.core import DEVICES, profiles
from deskdeck.deskflow import (
    ConfigurationError, DeviceBinding, DeviceReadiness, HandoffTransaction,
    Platform, Role, Stage, prepare_profile,
)


def bindings():
    result = []
    for index, device in enumerate(DEVICES, 11):
        platform = {"main": Platform.MACOS, "mac": Platform.MACOS,
                    "linux": Platform.LINUX, "windows": Platform.WINDOWS}[device]
        if platform == Platform.WINDOWS:
            base = "C:/ProgramData/Deskflow"
            executable = "C:/Program Files/Deskflow/deskflow-core.exe"
        elif platform == Platform.MACOS:
            base = "/Users/example/Library/Deskflow"
            executable = "/Applications/Deskflow.app/Contents/MacOS/deskflow-core"
        else:
            base = "/home/example/.config/Deskflow"
            executable = "/usr/bin/deskflow-core"
        result.append(DeviceBinding(
            device, "test-" + device, f"192.168.50.{index}", platform,
            {"main": "Thunderbolt", "windows": "USB2", "mac": "USB3", "linux": "USB4"}[device],
            executable, base, base + "/server.ini", base + "/client.ini",
            base + "/active-topology.conf", base + "/tls/deskflow.pem",
        ))
    return result


def readiness(items):
    fingerprints = {item.device_id: hashlib.sha256(("fixture:" + item.device_id).encode()).hexdigest() for item in items}
    return [DeviceReadiness(
        item.device_id, item.digest, fingerprints[item.device_id],
        frozenset(value for key, value in fingerprints.items() if key != item.device_id),
        frozenset(value for key, value in fingerprints.items() if key != item.device_id),
        executable_verified=True, private_paths_verified=True,
        client_permission_granted=True, server_permission_granted=True,
    ) for item in items]


def ready_plan(profile="focus-mac"):
    items = bindings()
    return prepare_profile(profile, items, readiness=readiness(items))


def transaction_at(stage):
    plan = ready_plan()
    tx = HandoffTransaction(plan, "test-transition-1")
    if stage == Stage.PREPARED:
        return tx, plan
    tx.old_owned_stopped("test-transition-1", DEVICES)
    if stage == Stage.OLD_OWNED_STOPPED:
        return tx, plan
    tx.usb_applied("test-transition-1", "mac", "USB3", verified=True)
    if stage == Stage.USB_APPLIED:
        return tx, plan
    tx.server_listening("test-transition-1", "mac", 1234, plan.server.settings_digest, process_owned=True)
    if stage == Stage.SERVER_LISTENING:
        return tx, plan
    tx.expected_roster("test-transition-1", 1234, plan.expected_computer_names, tls_verified=True)
    if stage == Stage.EXPECTED_ROSTER:
        return tx, plan
    tx.human_accepted("test-transition-1", accepted=True, evidence_reference="fixture:human-input-check")
    return tx, plan


class DeskflowPreparationTests(unittest.TestCase):
    def test_every_profile_plans_four_roles_and_one_server_following_usb_owner(self):
        items = bindings()
        for profile in profiles():
            with self.subTest(profile=profile["id"]):
                plan = prepare_profile(profile["id"], items)
                owner = profile["focus"] if profile["mode"] == "focus" else "main"
                self.assertEqual(plan.server_device, owner)
                self.assertEqual(len(plan.roles), 4)
                self.assertEqual(sum(item.role == Role.SERVER for item in plan.roles), 1)
                self.assertEqual(plan.usb_upstream, next(item.usb_upstream for item in items if item.device_id == owner))

    def test_explicit_shared_override_does_not_override_fullscreen_focus(self):
        self.assertEqual(prepare_profile("shared", bindings(), shared_owner="linux").server_device, "linux")
        self.assertEqual(prepare_profile("focus-mac", bindings(), shared_owner="linux").server_device, "mac")

    def test_missing_device_and_duplicate_device_name_address_or_usb_refuse(self):
        items = bindings()
        candidates = [items[:-1], items[:-1] + [items[0]]]
        for key in ("computer_name", "lan_address", "usb_upstream"):
            candidates.append([items[0], replace(items[1], **{key: getattr(items[0], key)}), *items[2:]])
        candidates.append([items[0], replace(items[1], computer_name=items[0].computer_name.upper()), *items[2:]])
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ConfigurationError):
                    prepare_profile("shared", candidate)

    def test_ipv6_canonicalization_cannot_hide_duplicate_addresses(self):
        items = bindings()
        items[0] = replace(items[0], lan_address="fd00:0:0:0:0:0:0:1")
        items[1] = replace(items[1], lan_address="fd00::1")
        with self.assertRaises(ConfigurationError):
            prepare_profile("shared", items)

    def test_ipv6_link_local_requires_a_scope_this_binding_contract_does_not_support(self):
        item = bindings()[0]
        for address in ("fe80::1", "fe90::2", "febf::3", "fe80::1%en0"):
            with self.subTest(address=address):
                with self.assertRaises(ConfigurationError):
                    replace(item, lan_address=address)
        self.assertEqual(replace(item, lan_address="fd00::1").lan_address, "fd00::1")
        self.assertEqual(replace(item, lan_address="169.254.1.2").lan_address, "169.254.1.2")

    def test_names_addresses_and_paths_reject_injection_or_ambiguous_binding(self):
        item = bindings()[0]
        cases = [
            ("computer_name", "name\n[security]"), ("computer_name", "x]"),
            ("computer_name", "-option"), ("computer_name", "two words"),
            ("lan_address", "example.local"), ("lan_address", "192.168.50.1\nother=value"),
            ("lan_address", "127.0.0.1"), ("lan_address", "::"),
            ("lan_address", "224.0.0.1"), ("lan_address", "fe80::1%en0"),
            ("lan_address", "255.255.255.255"),
            ("executable", "deskflow-core"), ("executable", "/Applications/../unexpected/core"),
            ("certificate", item.certificate + "\r[server]"),
            ("topology", item.topology + "\u2028other=value"),
            ("topology", "/outside/private/active.conf"),
            ("topology", item.server_settings),
            ("topology", item.server_settings + "/active.conf"),
            ("client_settings", item.private_directory + "/other/client.ini"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ConfigurationError):
                    replace(item, **{key: value})
        windows = bindings()[-1]
        for value in (r"C:relative.exe", r"\\server\share\core.exe", r"\root-only.exe"):
            with self.assertRaises(ConfigurationError):
                replace(windows, executable=value)
        for filename in ("NUL", "CON.ini", "active.conf:stream", "trailing.ini.", "space.ini "):
            with self.assertRaises(ConfigurationError):
                replace(windows, topology=windows.private_directory + "/" + filename)
        with self.assertRaises(ConfigurationError):
            replace(windows, private_directory="C:/Users/example/Deskflow")

    def test_quoted_path_metacharacters_cannot_create_ini_sections(self):
        items = bindings()
        old = items[0]
        base = '/Users/example/Deskflow [private]; "quoted" \\local'
        items[0] = replace(old, private_directory=base, server_settings=base + "/server.ini",
                           client_settings=base + "/client.ini", topology=base + "/active.conf",
                           certificate=base + "/tls/deskflow.pem")
        plan = prepare_profile("focus-main", items)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(plan.server.settings_ini)
        self.assertEqual(set(parser.sections()), {"core", "security", "server", *["computer_test-" + d for d in DEVICES]})
        self.assertIn(r'\"quoted\"', parser["security"]["certificate"])
        self.assertIn(r'\\local', parser["security"]["certificate"])
        self.assertEqual(parser["security"]["tlsEnabled"], "true")

    def test_native_127_settings_keep_tls_and_desktop_roles_and_exact_target(self):
        plan = ready_plan()
        for configuration in plan.roles:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(configuration.settings_ini)
            self.assertEqual(parser["core"]["processMode"], "1")
            self.assertEqual(parser["security"]["tlsEnabled"], "true")
            self.assertEqual(parser["security"]["checkPeerFingerprints"], "true")
            self.assertEqual(configuration.argv[1:], (configuration.role.value, "-s", configuration.settings_path))
            if configuration.role == Role.CLIENT:
                self.assertEqual(parser["client"]["remoteHost"], '"' + plan.server.binding.lan_address + '"')
                self.assertEqual(parser["core"]["coreMode"], "1")
            else:
                self.assertEqual(parser["core"]["coreMode"], "2")
                self.assertTrue(all("computer_test-" + d in parser for d in DEVICES))
        self.assertNotIn("section: screens", plan.topology_text)
        self.assertIn("geometry is unqualified", plan.topology_text)
        self.assertIn("switchToScreen(test-windows)", plan.topology_text)

    def test_missing_readiness_never_unlocks_handoff(self):
        plan = prepare_profile("shared", bindings())
        self.assertFalse(plan.prerequisites_satisfied)
        tx = HandoffTransaction(plan, "blocked")
        with self.assertRaises(ConfigurationError):
            tx.old_owned_stopped("blocked", DEVICES)
        self.assertEqual(tx.stage, Stage.PREPARED)
        self.assertFalse(tx.complete)

    def test_readiness_and_blockers_cannot_be_separated_by_replacing_a_plan(self):
        plan = ready_plan()
        missing = replace(plan, readiness=())
        self.assertFalse(missing.prerequisites_satisfied)
        self.assertEqual({blocker.code for blocker in missing.blockers}, {"missing_readiness"})
        with self.assertRaises((TypeError, ValueError)):
            replace(missing, blockers=())
        untrusted = replace(plan.readiness[0], trusted_servers=frozenset())
        changed = replace(plan, readiness=(untrusted, *plan.readiness[1:]))
        self.assertFalse(changed.prerequisites_satisfied)
        self.assertIn("server_fingerprint_untrusted", [blocker.code for blocker in changed.blockers])
        with self.assertRaises(ConfigurationError):
            HandoffTransaction(changed, "untrusted").old_owned_stopped("untrusted", DEVICES)
        with self.assertRaises(ConfigurationError):
            replace(plan, readiness=[*plan.readiness])

    def test_correct_peer_fingerprint_is_required_in_both_trust_directions(self):
        items = bindings()
        observations = readiness(items)
        for index, key, code in ((0, "trusted_servers", "server_fingerprint_untrusted"),
                                 (2, "trusted_clients", "client_fingerprint_untrusted:main")):
            changed = list(observations)
            changed[index] = replace(changed[index], **{key: frozenset({"a" * 64})})
            plan = prepare_profile("focus-mac", items, readiness=changed)
            self.assertFalse(plan.prerequisites_satisfied)
            self.assertIn(code, [blocker.code for blocker in plan.blockers])

    def test_stale_binding_missing_permissions_and_reused_certificates_block(self):
        items = bindings()
        observations = readiness(items)
        changes = [
            (0, {"binding_digest": "a" * 64}, "binding_not_qualified"),
            (0, {"private_paths_verified": False}, "private_paths_not_verified"),
            (0, {"executable_verified": False}, "executable_not_verified"),
            (0, {"certificate_sha256": None}, "missing_local_certificate"),
            (0, {"client_permission_granted": False}, "injection_permission_missing"),
            (2, {"server_permission_granted": False}, "capture_permission_missing"),
            (1, {"certificate_sha256": observations[0].certificate_sha256}, "duplicate_local_certificate"),
        ]
        for index, change, code in changes:
            changed = list(observations)
            changed[index] = replace(changed[index], **change)
            plan = prepare_profile("focus-mac", items, readiness=changed)
            self.assertFalse(plan.prerequisites_satisfied)
            self.assertIn(code, [blocker.code for blocker in plan.blockers])
        with self.assertRaises(ConfigurationError):
            prepare_profile("shared", items, readiness=[observations[0], observations[0]])

    def test_preparation_is_deterministic_immutable_and_performs_no_io(self):
        items = bindings()
        with patch("builtins.open") as opened, patch("subprocess.Popen") as spawned, patch("socket.socket") as connected:
            first = prepare_profile("shared", items, readiness=readiness(items))
            second = prepare_profile("shared", reversed(items), readiness=reversed(readiness(items)))
        opened.assert_not_called()
        spawned.assert_not_called()
        connected.assert_not_called()
        self.assertEqual(first, second)
        items.clear()
        self.assertEqual(len(first.roles), 4)
        with self.assertRaises(FrozenInstanceError):
            first.server_device = "linux"
        with self.assertRaises(ConfigurationError):
            replace(first, roles=tuple(replace(item, role=Role.SERVER) for item in first.roles))
        with self.assertRaises(ConfigurationError):
            replace(first, roles=tuple(replace(item, settings_ini=item.settings_ini.replace("tlsEnabled=true", "tlsEnabled=false")) for item in first.roles))


class HandoffTests(unittest.TestCase):
    def test_only_human_acceptance_after_the_full_roster_completes(self):
        for stage in Stage:
            if stage == Stage.FAILED:
                continue
            tx, _ = transaction_at(stage)
            self.assertEqual(tx.complete, stage == Stage.HUMAN_ACCEPTED)
            self.assertFalse(tx.status()["execution_available"])
            self.assertFalse(tx.status()["geometry_qualified"])
        tx, _ = transaction_at(Stage.HUMAN_ACCEPTED)
        self.assertEqual(tx.status()["human_evidence_reference"], "fixture:human-input-check")

    def test_partial_stop_out_of_order_and_stale_events_do_not_advance(self):
        tx, _ = transaction_at(Stage.PREPARED)
        for stopped in (DEVICES[:-1], (*DEVICES, "main"), (*DEVICES, "foreign")):
            with self.assertRaises(ConfigurationError):
                tx.old_owned_stopped("test-transition-1", stopped)
        with self.assertRaises(ConfigurationError):
            tx.old_owned_stopped("earlier-transition", DEVICES)
        with self.assertRaises(ConfigurationError):
            tx.human_accepted("test-transition-1", accepted=True, evidence_reference="premature")
        self.assertEqual(tx.stage, Stage.PREPARED)

    def test_usb_readback_and_owned_server_identity_must_match(self):
        tx, plan = transaction_at(Stage.OLD_OWNED_STOPPED)
        for device, upstream, verified in (("main", "USB3", True), ("mac", "USB4", True), ("mac", "USB3", False)):
            with self.assertRaises(ConfigurationError):
                tx.usb_applied("test-transition-1", device, upstream, verified=verified)
        tx.usb_applied("test-transition-1", "mac", "USB3", verified=True)
        for device, pid, digest, owned in (("main", 1234, plan.server.settings_digest, True),
                                           ("mac", True, plan.server.settings_digest, True),
                                           ("mac", 1234, "a" * 64, True),
                                           ("mac", 1234, plan.server.settings_digest, False)):
            with self.assertRaises(ConfigurationError):
                tx.server_listening("test-transition-1", device, pid, digest, process_owned=owned)
        self.assertEqual(tx.stage, Stage.USB_APPLIED)

    def test_unexpected_missing_duplicate_or_unauthenticated_rosters_refuse(self):
        tx, plan = transaction_at(Stage.SERVER_LISTENING)
        names = sorted(plan.expected_computer_names)
        for values, pid, tls in ((names[:-1], 1234, True), (names + ["foreign"], 1234, True),
                                 (names + [names[0]], 1234, True), (names, 9999, True), (names, 1234, False)):
            with self.assertRaises(ConfigurationError):
                tx.expected_roster("test-transition-1", pid, values, tls_verified=tls)
        self.assertEqual(tx.stage, Stage.SERVER_LISTENING)

    def test_human_confirmation_needs_an_explicit_result_and_reference(self):
        tx, _ = transaction_at(Stage.EXPECTED_ROSTER)
        for accepted, reference in ((False, "fixture:declined"), (1, "fixture:truthy"), (True, "")):
            with self.assertRaises(ConfigurationError):
                tx.human_accepted("test-transition-1", accepted=accepted, evidence_reference=reference)
        self.assertFalse(tx.complete)

    def test_failure_is_terminal_and_can_never_be_relabelled_complete(self):
        for stage in Stage:
            if stage == Stage.FAILED:
                continue
            tx, _ = transaction_at(stage)
            tx.fail("test-transition-1", "fixture-failure")
            self.assertEqual(tx.stage, Stage.FAILED)
            self.assertFalse(tx.complete)
            with self.assertRaises(ConfigurationError):
                tx.human_accepted("test-transition-1", accepted=True, evidence_reference="late")
            self.assertEqual(tx.status()["failure_code"], "fixture-failure")
            with self.assertRaises(ConfigurationError):
                tx.fail("test-transition-1", "overwritten-failure")
            self.assertEqual(tx.status()["failure_code"], "fixture-failure")


if __name__ == "__main__":
    unittest.main()
