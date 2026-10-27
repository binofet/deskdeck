import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from deskdeck.monitor_binding import load_binding


class BindingTests(unittest.TestCase):
    def test_private_binding_only_and_no_helper_launch(self):
        with tempfile.TemporaryDirectory() as folder, patch('deskdeck.monitor_binding.sys.platform', 'darwin'):
            path = Path(folder).resolve() / 'binding.json'
            path.write_text(json.dumps({'executable': '/synthetic/helper', 'sha256': 'a' * 64,
                                        'monitor_uuid': '11111111-2222-3333-4444-555555555555'}))
            path.chmod(0o600)
            self.assertEqual(load_binding(str(path)).executable, '/synthetic/helper')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_binding(str(path))
            path.chmod(0o600)
            path.write_text('{"executable":"/a","executable":"/b"}')
            with self.assertRaises(ValueError):
                load_binding(str(path))
