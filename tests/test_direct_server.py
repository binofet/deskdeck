import http.client
import json
import threading
import unittest
from deskdeck.server import PreviewServer


class FakeMonitor:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.entered = threading.Event()
        self.release = None

    def apply(self, profile):
        self.calls.append(profile)
        self.entered.set()
        if self.release:
            self.release.wait(2)
        return {"verified": not self.fail, "profile_id": None if self.fail else profile,
                "error": "helper_timeout" if self.fail else None}


class DirectServerTests(unittest.TestCase):
    def setUp(self):
        self.monitor = FakeMonitor()
        self.server = PreviewServer(("127.0.0.1", 0), direct_monitor=self.monitor)

    def tearDown(self):
        self.server.server_close()

    def test_construction_and_status_do_not_apply(self):
        self.assertFalse(self.server.daemon_threads)
        self.assertEqual(self.monitor.calls, [])
        state = self.server.current_status()
        self.assertEqual(state["mode"], "direct-monitor")
        self.assertFalse(state["deskflow_ready"])
        self.assertIsNone(state["current_profile"])

    def test_supported_only_and_failure_invalidates_last_result(self):
        self.assertFalse(self.server.direct_plan("focus-windows")["ready"])
        self.assertEqual(self.server.apply_direct("focus-windows")[0], 409)
        self.assertEqual(self.monitor.calls, [])
        self.assertEqual(self.server.apply_direct("focus-main")[0], 200)
        self.assertEqual(self.server.current_profile, "focus-main")
        self.monitor.fail = True
        self.assertEqual(self.server.apply_direct("shared-four")[0], 409)
        self.assertIsNone(self.server.current_profile)
        self.assertEqual(self.server.last_error, "helper_timeout")
        self.assertEqual(self.monitor.calls, ["focus-main", "shared-four"])

    def test_concurrent_apply_refused_without_queue_or_state_corruption(self):
        self.monitor.release = threading.Event()
        worker = threading.Thread(target=self.server.apply_direct, args=("focus-main",))
        worker.start()
        self.assertTrue(self.monitor.entered.wait(1))
        self.assertTrue(self.server.current_status()["transitioning"])
        self.assertEqual(self.server.apply_direct("shared-four")[1]["error"], "apply_busy")
        self.assertEqual(self.monitor.calls, ["focus-main"])
        self.monitor.release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.server.current_profile, "focus-main")

    def test_http_apply_dispatch_requires_token_and_returns_actual_failure(self):
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        def request(token):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
            connection.request("POST", "/api/apply", json.dumps({"profile_id": "shared-four"}),
                               {"Content-Type": "application/json", "X-DeskDeck-Token": token})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        try:
            self.assertEqual(request("wrong")[0], 403)
            self.assertEqual(self.monitor.calls, [])
            self.assertEqual(request(self.server.session_token)[0], 200)
            self.assertEqual(self.monitor.calls, ["shared-four"])
            self.monitor.fail = True
            code, result = request(self.server.session_token)
            self.assertEqual(code, 409)
            self.assertEqual(result["error"], "helper_timeout")
            self.assertIsNone(self.server.current_profile)
        finally:
            self.server.shutdown()
            worker.join(2)

    def test_graceful_close_waits_for_direct_apply_handler(self):
        self.monitor.release = threading.Event()
        serving = threading.Thread(target=self.server.serve_forever, daemon=True)
        serving.start()
        errors = []
        def apply_request():
            try:
                connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
                connection.request("POST", "/api/apply", json.dumps({"profile_id": "focus-main"}),
                                   {"Content-Type": "application/json", "X-DeskDeck-Token": self.server.session_token})
                response = connection.getresponse()
                response.read()
                connection.close()
            except Exception as error:
                errors.append(error)
        client = threading.Thread(target=apply_request)
        client.start()
        self.assertTrue(self.monitor.entered.wait(1))
        self.server.shutdown()
        closed = threading.Event()
        closer = threading.Thread(target=lambda: (self.server.server_close(), closed.set()))
        closer.start()
        self.assertFalse(closed.wait(0.05))
        self.monitor.release.set()
        closer.join(2)
        client.join(2)
        serving.join(2)
        self.assertTrue(closed.is_set())
        self.assertEqual(errors, [])
