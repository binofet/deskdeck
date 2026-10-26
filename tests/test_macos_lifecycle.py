import configparser
from contextlib import nullcontext
from dataclasses import replace
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch

from deskdeck.core import status
from deskdeck.deskflow import ConfigurationError, DeviceBinding, Platform, Role, RoleConfiguration
from deskdeck.macos_lifecycle import (
    AtomicIniStore, CocoaActions, LifecycleFailure, MacGuiBinding, MacGuiLifecycleAdapter,
    Observation, Process, WriteFailure, WriteReceipt, digest,
    role_semantics_sha256,
)


BASELINE = b"[core]\ncomputerName=test-main\ncoreMode=1\n[gui]\ncloseToTray=false\n"


def role_config(base="/Users/example/Library/Deskflow", role=Role.CLIENT):
    binding = DeviceBinding(
        "main", "test-main", "192.168.50.11", Platform.MACOS, "Thunderbolt",
        "/Applications/Deskflow.app/Contents/MacOS/deskflow-core", base,
        base + "/server.ini", base + "/client.ini", base + "/topology.conf", base + "/tls/deskflow.pem")
    ini = ('[core]\ncomputerName="test-main"\ncoreMode=' + ("2" if role == Role.SERVER else "1")
           + '\nprocessMode=1\n[security]\ntlsEnabled=true\ncheckPeerFingerprints=true\n'
           + 'certificate="' + base + '/tls/deskflow.pem"\n')
    ini += ('[server]\nexternalConfig=true\nexternalConfigFile="' + base + '/topology.conf"\n'
            if role == Role.SERVER else '[client]\nremoteHost="192.168.50.12"\n')
    return RoleConfiguration(binding, role, binding.server_settings if role == Role.SERVER else binding.client_settings, ini)


class Rig:
    """Pure clock, process inventory and side-effect recorder; no native calls."""

    def __init__(self, *, opt_in=True):
        self.binding = MacGuiBinding("/Applications/Deskflow.app", "/Users/example/Library/Deskflow/Deskflow.conf", 501)
        self.now, self.events, self.raw = 0.0, [], BASELINE
        self.running, self.launched = True, False
        self.quit_result, self.quit_hangs, self.launch_hangs = True, False, False
        self.core_ready = True
        self.override = None
        self.old_gui = Process(10, "1.000000", self.binding.gui_path, 501, True)
        self.old_core = Process(11, "old-core-start", self.binding.core_path, 501, True, 10)
        self.new_gui = replace(self.old_gui, pid=20, start_identity="2.000000")
        self.new_core = replace(self.old_core, pid=21, start_identity="new-core-start", parent_pid=20)
        self.adapter = MacGuiLifecycleAdapter(
            self.binding, actions=self, store=self, observe=self.observe, lease=lambda: nullcontext(),
            experimental_opt_in=opt_in, clock=lambda: self.now, pause=self.pause,
            shutdown_timeout=1, readiness_timeout=1, poll_interval=0.1)
        self.plan = self.adapter.prepare(role_config(), digest(BASELINE))

    def pause(self, seconds):
        self.now += seconds

    def observe(self, deadline):
        self.events.append("observe")
        sample = Observation(self.now, True, True)
        if self.running:
            sample = replace(sample, gui=(self.old_gui,), core=(self.old_core,))
        elif self.launched:
            # Independently parse the fake active file, including legitimate GUI writes.
            observed_ini = self.raw + b"\n[log]\nlevel=INFO\n"
            core = replace(self.new_core, role_semantics_sha256=role_semantics_sha256(
                observed_ini, self.binding, self.plan.role), role=self.plan.role)
            sample = replace(sample, gui=(self.new_gui,), core=(core,), ready=self.core_ready)
        return self.override(sample) if self.override else sample

    def read(self, path):
        self.events.append("read")
        return self.raw

    def quit(self, binding, process, deadline):
        self.events.append("quit")
        if self.quit_result and not self.quit_hangs:
            self.running = False
        return self.quit_result

    def replace(self, plan, assert_stopped):
        assert_stopped()
        self.events.append("write")
        self.raw = plan.ini
        return WriteReceipt("Deskflow.conf.backup-fixture", plan.current_sha256, plan.settings_sha256)

    def launch(self, binding, deadline):
        self.events.append("launch")
        self.launched = True
        if self.launch_hangs:
            self.now = deadline
        return self.new_gui.pid


class LifecycleTests(unittest.TestCase):
    def assert_no_write_or_launch(self, rig):
        self.assertNotIn("write", rig.events)
        self.assertNotIn("launch", rig.events)
        self.assertEqual(rig.raw, BASELINE)

    def test_default_gate_and_imported_preview_remain_disabled_without_io(self):
        rig = Rig(opt_in=False)
        self.assertEqual(rig.adapter.run(rig.plan).status, "blocked")
        self.assertEqual(rig.events, [])
        self.assertFalse(rig.adapter.capabilities()["qualified"])
        self.assertFalse(rig.adapter.capabilities()["live_profile_apply"])
        self.assertFalse(status()["deskflow_ready"])

    def test_refused_normal_quit_never_writes_or_launches(self):
        rig = Rig()
        rig.quit_result = False
        result = rig.adapter.run(rig.plan)
        self.assertEqual(result.failure, "normal_quit_refused")
        self.assert_no_write_or_launch(rig)

    def test_quit_accepted_but_gui_or_core_remains_times_out_without_writes(self):
        rig = Rig()
        rig.quit_hangs = True
        result = rig.adapter.run(rig.plan)
        self.assertEqual((result.phase, result.failure), ("shutdown", "timeout"))
        self.assertLessEqual(rig.now, 1.1)
        self.assert_no_write_or_launch(rig)

    def test_incomplete_stale_conflicting_and_unowned_inventory_refuses(self):
        changes = [lambda s: replace(s, complete=False),
                   lambda s: replace(s, prerequisites_verified=False),
                   lambda s: replace(s, observed_at=-1),
                   lambda s: replace(s, gui=(replace(s.gui[0], uid=502),)),
                   lambda s: replace(s, gui=(replace(s.gui[0], ownership_verified=False),)),
                   lambda s: replace(s, gui=s.gui * 2),
                   lambda s: replace(s, gui=()),
                   lambda s: replace(s, core=(replace(s.core[0], parent_pid=99),))]
        for change in changes:
            with self.subTest(change=change):
                rig = Rig()
                rig.override = change
                self.assertEqual(rig.adapter.run(rig.plan).status, "failed")
                self.assertNotIn("quit", rig.events)
                self.assert_no_write_or_launch(rig)

    def test_changed_baseline_fails_before_quit(self):
        rig = Rig()
        rig.raw += b"# another editor changed the file\n"
        result = rig.adapter.run(rig.plan)
        self.assertEqual(result.failure, "baseline_config_changed")
        self.assertNotIn("quit", rig.events)
        self.assertNotIn("write", rig.events)

    def test_existing_tls_path_or_key_size_change_refuses_before_quit(self):
        for setting in (b"certificate=/other/tls/deskflow.pem", b"keySize=4096"):
            rig = Rig()
            rig.raw += b"[security]\n" + setting + b"\n"
            rig.plan = rig.adapter.prepare(role_config(), digest(rig.raw))
            result = rig.adapter.run(rig.plan)
            self.assertEqual(result.failure, "existing_tls_settings_require_separate_preparation")
            self.assertNotIn("quit", rig.events)
            self.assertNotIn("write", rig.events)

    def test_cancellation_before_quit_during_shutdown_and_after_write_never_relaunches(self):
        for condition in (lambda r: True, lambda r: "quit" in r.events, lambda r: "write" in r.events):
            rig = Rig()
            rig.adapter.cancelled = lambda: condition(rig)
            result = rig.adapter.run(rig.plan)
            self.assertEqual(result.failure, "cancelled_outcome_requires_observation")
            self.assertNotIn("launch", rig.events)
            if "write" not in rig.events:
                self.assertEqual(rig.raw, BASELINE)
            else:
                self.assertIsNotNone(result.write)

    def test_gui_exit_with_lingering_owned_core_refuses_without_core_stop(self):
        rig = Rig()
        def lingering(sample):
            if "quit" in rig.events:
                return replace(sample, core=(rig.old_core,))
            return sample
        rig.override = lingering
        self.assertEqual(rig.adapter.run(rig.plan).failure, "core_owner_missing")
        self.assert_no_write_or_launch(rig)

    def test_pid_reuse_or_external_relaunch_during_shutdown_aborts(self):
        rig = Rig()
        rig.quit_hangs = True
        def changed(sample):
            if "quit" in rig.events:
                return replace(sample, gui=(replace(rig.old_gui, start_identity="reused-pid"),))
            return sample
        rig.override = changed
        self.assertEqual(rig.adapter.run(rig.plan).failure, "process_replaced_during_shutdown")
        self.assert_no_write_or_launch(rig)

    def test_config_failure_after_successful_stop_never_launches(self):
        rig = Rig()
        rig.replace = Mock(side_effect=OSError("private native detail must not escape"))
        result = rig.adapter.run(rig.plan)
        self.assertEqual(result.phase, "write_config")
        self.assertNotIn("private", result.failure)
        self.assertNotIn("launch", rig.events)
        self.assertFalse(rig.running)

    def test_launch_failure_keeps_backup_receipt_and_does_not_claim_ready(self):
        rig = Rig()
        rig.launch = Mock(side_effect=LifecycleFailure("launch_failed"))
        result = rig.adapter.run(rig.plan)
        self.assertEqual(result.failure, "launch_failed")
        self.assertIsNotNone(result.write)
        self.assertEqual(result.write.before_sha256, digest(BASELINE))
        self.assertFalse(result.qualified)
        self.assertFalse(result.human_accepted)

    def test_late_launch_or_missing_readiness_is_bounded_and_not_retried(self):
        for late in (True, False):
            rig = Rig()
            rig.launch_hangs, rig.core_ready = late, False
            result = rig.adapter.run(rig.plan)
            self.assertEqual(result.failure, "timeout")
            self.assertEqual(rig.events.count("launch"), 1)
            self.assertIsNotNone(result.write)
            self.assertEqual(rig.adapter.run(rig.plan).status, "blocked")

    def test_wrong_core_config_role_or_launch_identity_cannot_be_ready(self):
        for change in (lambda s: replace(s, core=(replace(s.core[0], role_semantics_sha256="a" * 64),)),
                       lambda s: replace(s, core=(replace(s.core[0], role=Role.SERVER),)),
                       lambda s: replace(s, gui=(replace(s.gui[0], pid=30),), core=())):
            rig = Rig()
            rig.override = lambda s: change(s) if rig.launched else s
            result = rig.adapter.run(rig.plan)
            self.assertEqual(result.status, "failed")

    def test_success_has_stop_absence_write_launch_readiness_order_without_acceptance(self):
        rig = Rig()
        result = rig.adapter.run(rig.plan)
        self.assertEqual(result.status, "observed_ready")
        self.assertLess(rig.events.index("quit"), rig.events.index("write"))
        self.assertLess(rig.events.index("write"), rig.events.index("launch"))
        between = rig.events[rig.events.index("quit") + 1:rig.events.index("write")]
        self.assertGreaterEqual(between.count("observe"), 4)
        self.assertEqual((result.gui_pid, result.core_pid), (20, 21))
        self.assertFalse(result.qualified)
        self.assertFalse(result.human_accepted)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(rig.raw.decode())
        self.assertEqual(parser["gui"]["startCoreWithGui"], "true")
        self.assertEqual(parser["gui"]["closeToTray"], "false")
        self.assertEqual(parser["core"]["processMode"], "1")

    def test_forged_plan_and_unsafe_settings_cannot_run(self):
        rig = Rig()
        self.assertEqual(rig.adapter.run(replace(rig.plan, ini=b"forged")).status, "blocked")
        self.assertEqual(rig.events, [])
        for text in (role_config().settings_ini.replace("tlsEnabled=true", "tlsEnabled=false"),
                     role_config().settings_ini + "[gui]\nstartCoreWithGui=true\n"):
            with self.assertRaises(ConfigurationError):
                rig.adapter.prepare(replace(role_config(), settings_ini=text), digest(BASELINE))

    def test_gui_canonical_rewrite_is_ready_but_critical_changes_are_not(self):
        import io
        rig = Rig()
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read_string(rig.plan.ini.decode())
        parser["gui"]["shownServerFirstStartMessage"] = "true"
        parser["log"] = {"level": "INFO"}
        for section in parser.sections():
            for key, value in list(parser[section].items()):
                if value.startswith('"') and value.endswith('"'):
                    parser[section][key] = value[1:-1]
        stream = io.StringIO()
        parser.write(stream)
        rewritten = stream.getvalue().encode()
        self.assertNotEqual(digest(rewritten), rig.plan.settings_sha256)
        self.assertEqual(role_semantics_sha256(rewritten, rig.binding, Role.CLIENT), rig.plan.role_semantics_sha256)
        for before, after in ((b"remoteHost = 192.168.50.12", b"remoteHost = 192.168.50.13"),
                              (b"processMode = 1", b"processMode = 0"),
                              (b"tlsEnabled = true", b"tlsEnabled = false"),
                              (b"closeToTray = false", b"closeToTray = true")):
            changed = rewritten.replace(before, after)
            self.assertNotEqual(changed, rewritten)
            try:
                changed_hash = role_semantics_sha256(changed, rig.binding, Role.CLIENT)
            except ConfigurationError:
                continue
            self.assertNotEqual(changed_hash, rig.plan.role_semantics_sha256)

    def test_server_role_preparation_and_semantic_observation_are_supported(self):
        rig = Rig()
        rig.plan = rig.adapter.prepare(role_config(role=Role.SERVER), digest(BASELINE))
        self.assertEqual(rig.adapter.run(rig.plan).status, "observed_ready")


@unittest.skipUnless(os.name == "posix", "POSIX private-file implementation")
class AtomicStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.active = self.base / "Deskflow.conf"
        self.active.write_bytes(BASELINE)
        self.active.chmod(0o600)
        self.store = AtomicIniStore()
        rig = Rig()
        rig.binding = MacGuiBinding("/Applications/Deskflow.app", str(self.active), os.getuid())
        self.plan = replace(rig.plan, binding=rig.binding)

    def test_atomic_private_backup_and_exact_ini_readback_without_tls_access(self):
        guard = Mock()
        receipt = self.store.replace(self.plan, guard)
        self.assertEqual(self.active.read_bytes(), self.plan.ini)
        self.assertEqual((self.base / receipt.backup_name).read_bytes(), BASELINE)
        self.assertEqual(stat.S_IMODE(self.active.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.base / receipt.backup_name).stat().st_mode), 0o600)
        self.assertEqual(guard.call_count, 2)
        self.assertEqual(len(list(self.base.iterdir())), 2)

    def test_guard_refuses_before_backup_or_replace(self):
        with self.assertRaises(WriteFailure) as caught:
            self.store.replace(self.plan, Mock(side_effect=LifecycleFailure("process_reappeared")))
        self.assertIsNone(caught.exception.receipt)
        self.assertEqual(self.active.read_bytes(), BASELINE)
        self.assertEqual(len(list(self.base.iterdir())), 1)

    def test_changed_baseline_or_symlink_hardlink_and_permissions_refuse(self):
        self.active.write_bytes(b"changed")
        with self.assertRaises(WriteFailure):
            self.store.replace(self.plan, Mock())
        self.active.unlink()
        other = self.base / "other.ini"
        other.write_bytes(BASELINE)
        other.chmod(0o600)
        self.active.symlink_to(other)
        with self.assertRaises(OSError):
            self.store.read(str(self.active))
        self.active.unlink()
        os.link(other, self.active)
        with self.assertRaises(LifecycleFailure):
            self.store.read(str(self.active))
        self.active.unlink()
        other.unlink()
        self.active.write_bytes(BASELINE)
        self.active.chmod(0o644)
        with self.assertRaises(LifecycleFailure):
            self.store.read(str(self.active))

    def test_failure_after_replace_retains_recovery_receipt_and_reports_partial_write(self):
        original_fsync, calls = os.fsync, 0
        def fail_after_replace(fd):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("simulated directory sync failure")
            return original_fsync(fd)
        with patch("deskdeck.macos_lifecycle.os.fsync", side_effect=fail_after_replace):
            with self.assertRaises(WriteFailure) as caught:
                self.store.replace(self.plan, Mock())
        error = caught.exception
        self.assertEqual(str(error), "config_write_failed_replaced")
        self.assertEqual(self.active.read_bytes(), self.plan.ini)
        self.assertEqual((self.base / error.receipt.backup_name).read_bytes(), BASELINE)

    def test_second_guard_failure_keeps_original_and_backup_but_cleans_temp(self):
        guard = Mock(side_effect=[None, LifecycleFailure("process_reappeared")])
        with self.assertRaises(WriteFailure) as caught:
            self.store.replace(self.plan, guard)
        self.assertEqual(str(caught.exception), "config_write_failed_not_replaced")
        self.assertEqual(self.active.read_bytes(), BASELINE)
        self.assertEqual(len(list(self.base.iterdir())), 2)

    def test_cleanup_error_cannot_hide_primary_failure_or_leak_directory_fd(self):
        guard = Mock(side_effect=[None, LifecycleFailure("process_reappeared")])
        original_close = os.close
        with patch("deskdeck.macos_lifecycle.os.unlink", side_effect=PermissionError("fixture cleanup")), \
                patch("deskdeck.macos_lifecycle.os.close", wraps=original_close) as closed:
            with self.assertRaises(WriteFailure) as caught:
                self.store.replace(self.plan, guard)
        self.assertEqual(str(caught.exception), "config_write_failed_not_replaced")
        self.assertIsNotNone(caught.exception.receipt)
        self.assertTrue(caught.exception.cleanup_failed)
        self.assertTrue(closed.called)
        self.assertEqual(self.active.read_bytes(), BASELINE)


class CocoaActionTests(unittest.TestCase):
    def test_only_normal_exact_application_termination_is_requested(self):
        rig = Rig()
        app = Mock()
        app.processIdentifier.return_value = rig.old_gui.pid
        app.bundleIdentifier.return_value = rig.binding.bundle_id
        app.bundleURL.return_value.path.return_value = rig.binding.bundle_path
        app.executableURL.return_value.path.return_value = rig.binding.gui_path
        app.launchDate.return_value.timeIntervalSince1970.return_value = 1.0
        app.terminate.return_value = False
        klass = Mock()
        klass.runningApplicationWithProcessIdentifier_.return_value = app
        actions = CocoaActions(klass, Mock(), Mock(), Mock(), clock=lambda: 0)
        self.assertFalse(actions.quit(rig.binding, rig.old_gui, 1))
        app.terminate.assert_called_once_with()
        app.forceTerminate.assert_not_called()
        app.terminate.reset_mock()
        app.launchDate.return_value.timeIntervalSince1970.return_value = 2.0
        with self.assertRaises(LifecycleFailure):
            actions.quit(rig.binding, rig.old_gui, 1)
        app.terminate.assert_not_called()

    def test_launchservices_pins_bundle_without_new_instance_or_arguments(self):
        workspace, url_class, config_class = Mock(), Mock(), Mock()
        app = Mock()
        app.processIdentifier.return_value = 20
        workspace.openApplicationAtURL_configuration_completionHandler_.side_effect = lambda u, c, done: done(app, None)
        actions = CocoaActions(Mock(), workspace, url_class, config_class, clock=lambda: 0)
        binding = Rig().binding
        self.assertEqual(actions.launch(binding, 1), 20)
        url_class.fileURLWithPath_.assert_called_once_with(binding.bundle_path)
        config_class.configuration.return_value.setCreatesNewApplicationInstance_.assert_called_once_with(False)
        config_class.configuration.return_value.setAllowsRunningApplicationSubstitution_.assert_called_once_with(False)
        config_class.configuration.return_value.setActivates_.assert_called_once_with(False)
        config_class.configuration.return_value.setArguments_.assert_not_called()

    def test_late_completion_is_unknown_and_never_retried(self):
        workspace, now = Mock(), iter((0, 1))
        actions = CocoaActions(Mock(), workspace, Mock(), Mock(), clock=lambda: next(now))
        with self.assertRaisesRegex(LifecycleFailure, "launch_completion_timeout_outcome_unknown"):
            actions.launch(Rig().binding, 1)
        self.assertEqual(workspace.openApplicationAtURL_configuration_completionHandler_.call_count, 1)


if __name__ == "__main__":
    unittest.main()
