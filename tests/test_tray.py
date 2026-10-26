from contextlib import redirect_stderr, redirect_stdout
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deskdeck.core import profiles
from deskdeck.tray import (
    TrayActions, TrayUnavailable, _load_backend, build_menu, icon_pixels, main, preview_url,
    run_tray,
)


class FakeMenu(tuple):
    SEPARATOR = object()

    def __new__(cls, *items):
        return super().__new__(cls, items)


def fake_backend():
    return SimpleNamespace(
        Menu=FakeMenu,
        MenuItem=lambda text, callback, **kwargs: SimpleNamespace(text=text, callback=callback, **kwargs),
        Icon=Mock(HAS_MENU=True),
    )


class TrayTests(unittest.TestCase):
    def test_navigation_is_fixed_loopback_and_only_known_profiles(self):
        self.assertEqual(preview_url(8765), "http://127.0.0.1:8765/")
        for item in profiles():
            self.assertEqual(preview_url(8765, item["id"]), "http://127.0.0.1:8765/#profile=" + item["id"])
        for port in (True, 0, -1, 65536, "8765"):
            with self.assertRaises(ValueError):
                preview_url(port)
        for name in ("https://example.invalid", "shared\n/api/apply", "../focus-main"):
            with self.assertRaises(ValueError):
                preview_url(8765, name)

    def test_six_profile_callbacks_navigate_without_applying_or_changing_readiness(self):
        opener = Mock(return_value=True)
        actions = TrayActions(8765, opener)
        menu = build_menu(fake_backend(), actions)
        items = [item for item in menu if item is not FakeMenu.SEPARATOR]
        self.assertEqual(len(items), 8)
        self.assertEqual(items[0].text, "Open DeskDeck")
        self.assertEqual(items[-1].text, "Quit DeskDeck")
        self.assertTrue(all(item.text.startswith("Preview ") for item in items[1:-1]))
        for entry, profile in zip(items[1:-1], profiles(), strict=True):
            entry.callback(None, entry)
            self.assertEqual(opener.call_args.args, (preview_url(8765, profile["id"]),))
        self.assertEqual(opener.call_count, 6)
        self.assertFalse(any("apply" in call.args[0] for call in opener.call_args_list))
        icon = Mock()
        items[-1].callback(icon, items[-1])
        icon.stop.assert_called_once_with()
        self.assertEqual(opener.call_count, 6)

    def test_browser_failure_gives_a_local_manual_fallback(self):
        with redirect_stderr(io.StringIO()) as output:
            self.assertFalse(TrayActions(8765, Mock(return_value=False)).open("shared"))
        self.assertIn("http://127.0.0.1:8765/#profile=shared", output.getvalue())

    def test_linux_menu_less_fallback_is_refused_instead_of_hiding_profile_controls(self):
        backend = SimpleNamespace(Icon=SimpleNamespace(HAS_MENU=False))
        with patch.dict("sys.modules", {"pystray": backend, "PIL": SimpleNamespace(Image=Mock())}):
            with self.assertRaisesRegex(TrayUnavailable, "cannot show profile menus"):
                _load_backend()

    def test_icon_is_small_monochrome_and_has_transparency(self):
        data = icon_pixels()
        self.assertEqual(len(data), 64 * 64 * 4)
        colors = {data[index:index + 4] for index in range(0, len(data), 4)}
        self.assertEqual(colors, {bytes((0, 0, 0, 0)), bytes((0, 0, 0, 255)), bytes((255, 255, 255, 255))})
        for size in (True, 0, 31, 257, "64"):
            with self.assertRaises(ValueError):
                icon_pixels(size)

    def test_runtime_owns_only_its_loopback_server_and_closes_it_when_icon_exits(self):
        backend = fake_backend()
        image = Mock()
        server = Mock(server_address=("127.0.0.1", 43210))
        with patch("deskdeck.tray._load_backend", return_value=(backend, image)), \
                patch("deskdeck.tray.PreviewServer", return_value=server) as server_type, \
                patch("deskdeck.tray.threading.Thread") as worker, \
                patch("deskdeck.tray.webbrowser.open", return_value=True) as opened, \
                redirect_stdout(io.StringIO()):
            run_tray(0)
        server_type.assert_called_once_with(("127.0.0.1", 0), setup=None)
        opened.assert_not_called()
        backend.Icon.return_value.run.assert_called_once_with()
        server.shutdown.assert_called_once_with()
        server.server_close.assert_called_once_with()
        worker.return_value.start.assert_called_once_with()
        worker.return_value.join.assert_called_once_with(timeout=5)

    def test_native_start_failure_still_closes_its_server_without_browser_side_effects(self):
        backend = fake_backend()
        backend.Icon.return_value.run.side_effect = RuntimeError("fixture backend failure")
        server = Mock(server_address=("127.0.0.1", 43210))
        with patch("deskdeck.tray._load_backend", return_value=(backend, Mock())), \
                patch("deskdeck.tray.PreviewServer", return_value=server), \
                patch("deskdeck.tray.threading.Thread"), \
                patch("deskdeck.tray.webbrowser.open") as opened, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "fixture backend failure"):
                run_tray(open_browser=False)
        opened.assert_not_called()
        server.shutdown.assert_called_once_with()
        server.server_close.assert_called_once_with()

    def test_failed_thread_start_never_deadlocks_by_shutting_down_an_unstarted_server(self):
        server = Mock(server_address=("127.0.0.1", 8765))
        with patch("deskdeck.tray._load_backend", return_value=(fake_backend(), Mock())), \
                patch("deskdeck.tray.PreviewServer", return_value=server), \
                patch("deskdeck.tray.threading.Thread") as worker:
            worker.return_value.start.side_effect = RuntimeError("fixture thread failure")
            with self.assertRaises(RuntimeError):
                run_tray()
        server.shutdown.assert_not_called()
        server.server_close.assert_called_once_with()

    def test_missing_backend_or_non_main_thread_never_binds_a_server(self):
        with patch("deskdeck.tray._load_backend", side_effect=TrayUnavailable("unavailable")), \
                patch("deskdeck.tray.PreviewServer") as server:
            with self.assertRaises(TrayUnavailable):
                run_tray()
        server.assert_not_called()
        with patch("deskdeck.tray.threading.current_thread", return_value=object()), \
                patch("deskdeck.tray._load_backend") as load, \
                patch("deskdeck.tray.PreviewServer") as server:
            with self.assertRaises(TrayUnavailable):
                run_tray()
        load.assert_not_called()
        server.assert_not_called()

    def test_cli_keeps_setup_and_explicit_no_open_and_reports_optional_dependency_failure(self):
        with patch("deskdeck.tray.run_tray") as run, patch("deskdeck.setup.load_setup", return_value={"fixture": True}):
            self.assertEqual(main(["--port", "0", "--setup", "/fixture/setup.json", "--no-open"]), 0)
        run.assert_called_once_with(0, setup={"fixture": True}, open_browser=False)
        with patch("deskdeck.tray.run_tray", side_effect=TrayUnavailable("fixture missing extra")), \
                redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main([]), 1)
        self.assertIn("fixture missing extra", output.getvalue())

    def test_startup_never_opens_browser_unless_explicitly_requested(self):
        with patch("deskdeck.tray.run_tray") as run:
            self.assertEqual(main([]), 0)
            run.assert_called_once_with(8765, setup=None, open_browser=False)
        with patch("deskdeck.tray.run_tray") as run:
            self.assertEqual(main(["--open"]), 0)
            run.assert_called_once_with(8765, setup=None, open_browser=True)


if __name__ == "__main__":
    unittest.main()
