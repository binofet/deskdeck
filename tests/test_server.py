import http.client
import json
import threading
import unittest

from deskdeck.server import PreviewServer


class ServerBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = PreviewServer(("127.0.0.1", 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        code, data = r.status, json.loads(r.read())
        c.close()
        return code, data

    def test_remote_binding_not_available(self):
        with self.assertRaises(ValueError):
            PreviewServer(("0.0.0.0", 0))

    def test_packaged_page_and_scripts_are_served(self):
        for path, mime in [("/", "text/html"), ("/static/style.css", "text/css"),
                           ("/static/app.js", "text/javascript")]:
            c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
            c.request("GET", path)
            response = c.getresponse()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader("Content-Type").startswith(mime))
            self.assertGreater(len(response.read()), 100)
            c.close()

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "attacker.example"})[0], 403)

    def test_foreign_origin_rejected(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"Origin": "https://attacker.example"})[0], 403)

    def test_mutation_without_token_rejected(self):
        self.assertEqual(self.request("POST", "/api/plan", '{"profile_id":"shared"}',
                                      {"Content-Type": "application/json"})[0], 403)

    def test_apply_never_claims_hardware_success(self):
        token = self.request("GET", "/api/session")[1]["token"]
        headers = {"Content-Type": "application/json", "X-DeskDeck-Token": token}
        code, data = self.request("POST", "/api/apply", '{"profile_id":"shared"}', headers)
        self.assertEqual(code, 409)
        self.assertFalse(data["ready"])
        self.assertIsNone(self.request("GET", "/api/status")[1]["current_profile"])

    def test_malformed_plan_does_not_escape_handler(self):
        headers = {"Content-Type": "application/json", "X-DeskDeck-Token": self.server.session_token}
        for bad in ["[]", "null", "{}", '{"profile_id":false}', '{"profile_id":"unknown"}', '{"profile_id":"shared","command":"evil"}']:
            self.assertEqual(self.request("POST", "/api/plan", bad, headers)[0], 400)


if __name__ == "__main__":
    unittest.main()
