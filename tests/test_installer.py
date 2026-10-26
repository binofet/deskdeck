import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deskdeck.installer import COMMON_PINS, InstallError, MANIFEST, PLATFORM_PINS, install, prefix_for, uninstall


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve() / "home"
        self.source = self.home / "dev" / "deskdeck"
        package = self.source / "deskdeck"
        package.mkdir(parents=True)
        (package / "tray.py").write_text("# reviewed fixture\n")
        (package / "__init__.py").write_text("")
        (package / "static").mkdir()
        (package / "static" / "index.html").write_text("fixture")
        self.python = Path(self.temp.name).resolve() / "external-python"
        self.python.write_bytes(b"fake interpreter, never executed")
        self.calls = []

    def fake_runner(self, argv, *, prefix):
        self.calls.append(argv)
        if "venv" in argv:
            folder = prefix / "venv" / "bin"
            folder.mkdir(parents=True)
            (folder / "python3").write_bytes(b"fake venv interpreter")
            (prefix / "venv" / "lib").mkdir()
            (prefix / "venv" / "lib64").symlink_to("lib")

    def install(self, **kwargs):
        return install(home=self.home, source=self.source, platform="darwin", python=self.python,
                       runner=self.fake_runner, **kwargs)

    def remove(self):
        return uninstall(home=self.home, platform="darwin")

    def test_fixed_private_prefix_pins_and_quiet_launcher_no_process_or_network(self):
        with patch("deskdeck.installer.subprocess.run", side_effect=AssertionError("no live process")):
            record = self.install()
        prefix = prefix_for(self.home, "darwin")
        self.assertEqual(record["state"], "installed_preview_unqualified")
        self.assertEqual(record["pins"], list(COMMON_PINS + PLATFORM_PINS["darwin"]))
        self.assertEqual(self.calls[0][1:5], ["-I", "-m", "venv", "--copies"])
        self.assertIn("--no-deps", self.calls[1])
        self.assertIn("--only-binary=:all:", self.calls[1])
        self.assertIn("https://pypi.org/simple", self.calls[1])
        self.assertFalse((prefix / "venv" / "lib64").exists())
        self.assertIn("['--port', '0', '--no-open']", (prefix / "launch.py").read_text())
        self.assertEqual(prefix.stat().st_mode & 0o777, 0o700)
        self.assertEqual((prefix / MANIFEST).stat().st_mode & 0o777, 0o600)
        self.assertTrue((prefix / "app/deskdeck/static/index.html").is_file())
        result = self.remove()
        self.assertFalse(result["prefix_retained"])
        self.assertTrue(self.source.is_dir())

    def test_existing_destination_is_never_overwritten(self):
        self.install()
        count = len(self.calls)
        with self.assertRaisesRegex(InstallError, "already exists"):
            self.install()
        self.assertEqual(len(self.calls), count)

    def test_external_interpreter_symlink_is_resolved_without_allowing_source_links(self):
        link = self.python.with_name("python-link")
        link.symlink_to(self.python)
        install(home=self.home, source=self.source, platform="darwin", python=link, runner=self.fake_runner)
        self.assertEqual(self.calls[0][0], str(self.python))

    def test_partial_pip_failure_is_manifested_and_removable_without_retry(self):
        def fail(argv, *, prefix):
            self.fake_runner(argv, prefix=prefix)
            if "pip" in argv:
                raise InstallError("fixture package failure")
        with self.assertRaisesRegex(InstallError, "fixture package"):
            install(home=self.home, source=self.source, platform="darwin", python=self.python, runner=fail)
        prefix = prefix_for(self.home, "darwin")
        self.assertEqual(json.loads((prefix / MANIFEST).read_text())["state"], "incomplete")
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(self.remove()["prefix_retained"])

    def test_changed_user_setup_is_preserved_and_other_files_removed(self):
        setup = self.home / "setup.json"
        setup.write_text('{"fixture":true}')
        with patch("deskdeck.setup.load_setup"):
            self.install(setup=setup)
        prefix = prefix_for(self.home, "darwin")
        config = prefix / "config/desk.json"
        config.write_text("user change")
        result = self.remove()
        self.assertEqual(result["preserved_user_config"], ["config/desk.json"])
        self.assertEqual(config.read_text(), "user change")
        self.assertFalse((prefix / "launch.py").exists())

    def test_uninstall_preflight_refuses_modified_or_unknown_members_before_deleting(self):
        for tamper in ("change", "extra", "missing", "symlink", "hardlink", "traversal"):
            with self.subTest(tamper=tamper):
                self.install()
                prefix = prefix_for(self.home, "darwin")
                member = prefix / "app/deskdeck/tray.py"
                if tamper == "change":
                    member.write_text("unreviewed change")
                elif tamper == "extra":
                    (prefix / "unowned.txt").write_text("keep")
                elif tamper == "missing":
                    member.unlink()
                elif tamper == "symlink":
                    member.unlink()
                    member.symlink_to(self.python)
                elif tamper == "hardlink":
                    os.link(member, prefix / "second-link")
                else:
                    manifest = prefix / MANIFEST
                    record = json.loads(manifest.read_text())
                    record["files"]["../outside"] = record["files"].pop("app/deskdeck/tray.py")
                    manifest.write_text(json.dumps(record))
                with self.assertRaises((InstallError, OSError)):
                    self.remove()
                self.assertTrue((prefix / "launch.py").exists())
                # Only fixture-owned temporary data is reset by this test.
                import shutil
                shutil.rmtree(prefix)

    def test_source_and_destination_link_ancestors_are_rejected(self):
        package = self.source / "deskdeck"
        moved = self.source / "real-package"
        package.rename(moved)
        package.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(InstallError, "Symlinked"):
            self.install()

    def test_linux_gtk_is_explicit_and_never_an_os_package_install(self):
        install(home=self.home, source=self.source, platform="linux", python=self.python,
                runner=self.fake_runner, system_gtk=True)
        self.assertIn("--system-site-packages", self.calls[0])
        self.assertEqual(self.calls[1][-1], "python-xlib==0.33")
        self.assertFalse(any("apt" in arg or "sudo" in arg for call in self.calls for arg in call))

    def test_venv_failure_after_compatibility_link_remains_exactly_removable(self):
        def fail(argv, *, prefix):
            self.fake_runner(argv, prefix=prefix)
            raise InstallError("ensurepip fixture unavailable")
        with self.assertRaisesRegex(InstallError, "ensurepip"):
            install(home=self.home, source=self.source, platform="darwin", python=self.python, runner=fail)
        prefix = prefix_for(self.home, "darwin")
        self.assertTrue((prefix / MANIFEST).is_file())
        self.assertFalse((prefix / "venv/lib64").is_symlink())
        self.assertFalse(self.remove()["prefix_retained"])

    def test_interpreter_alias_into_install_refuses_uninstall_before_deleting(self):
        self.install()
        prefix = prefix_for(self.home, "darwin")
        alias = self.python.with_name("running-alias")
        alias.symlink_to(prefix / "venv/bin/python3")
        with patch("deskdeck.installer.sys.executable", str(alias)):
            with self.assertRaisesRegex(InstallError, "external Python"):
                self.remove()
        self.assertTrue((prefix / "launch.py").exists())

    def test_windows_wrapper_uses_native_user_and_no_policy_bypass_or_start(self):
        script = (Path(__file__).parents[1] / "scripts/install.ps1").read_text()
        self.assertIn("WindowsIdentity]::GetCurrent()", script)
        self.assertIn("ReparsePoint", script)
        self.assertIn("Get-Acl", script)
        self.assertIn("[string]$Python", script)
        self.assertIn("& $executable @arguments", script)
        self.assertIn("Get-Command py -CommandType Application", script)
        uninstall_script = (Path(__file__).parents[1] / "scripts/uninstall.ps1").read_text()
        self.assertIn("-Python $Python", uninstall_script)
        for forbidden in ("ExecutionPolicy Bypass", "Start-Process", "New-Service", "Register-ScheduledTask"):
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
