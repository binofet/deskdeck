"""Exercise the exact pure browser draft-recovery function, without a browser."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for the browser recovery regression")
class EditorRecoveryTests(unittest.TestCase):
    def test_incomplete_drafts_recover_but_malformed_shapes_do_not(self):
        source = (Path(__file__).parents[1] / "deskdeck/static/editor.js").read_text()
        start = source.index("  function recoverDraft(value) {")
        end = source.index("  function restore()", start)
        script = source[start:end] + r'''
const assert = require('node:assert/strict');
const draft = {schema_version:1, input_owner:'laptop', computers:[
  {id:'laptop',name:'',dell_input:'Thunderbolt',usb_upstream:null,displays:[
    {id:'screen',name:'unfinished',kind:'built-in',anchor:'dell'}]},
  {id:'server',name:'server',dell_input:'Thunderbolt',usb_upstream:null,displays:[]}
]};
// Blank names, temporary duplicate ports and unfinished anchor choices are
// normal editing states. Export validation remains a separate operation.
assert.deepEqual(recoverDraft(draft), draft);
draft.computers[0].name = '🙂'.repeat(64);
assert.deepEqual(recoverDraft(draft), draft);
const copy = () => JSON.parse(JSON.stringify(draft));
let broken = copy(); broken.input_owner = 'missing';
assert.throws(() => recoverDraft(broken));
broken = copy(); broken.computers[0].id = '../outside';
assert.throws(() => recoverDraft(broken));
broken = copy(); broken.computers[0].displays[0].anchor = 'unknown';
assert.throws(() => recoverDraft(broken));
broken = copy(); broken.computers[1].displays = Array(17).fill(broken.computers[0].displays[0]);
assert.throws(() => recoverDraft(broken));
assert.throws(() => recoverDraft({schema_version:1, computers:null, input_owner:null}));
'''
        result = subprocess.run([shutil.which("node"), "-e", script],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
