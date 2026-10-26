# Hardware support and qualification

DeskDeck v0.1 is a local profile preview and an adapter foundation. The Dell
U5226KW and its three-region PBP behavior have **not been tested by this project**.
A saved desired state is useful because a monitor may forget its PBP layout when
PBP is disabled and enabled, but storing that state alone does not prove that it
can be restored. No monitor utility is installed or discovered automatically.

## Official documentation checked

Research date: 2026-10-26. Dell's
[DDPM manuals page](https://www.dell.com/support/product-details/product/dell-display-peripheral-manager/resources/manuals)
lists the Mac 2.3.0.2 administration guide and Windows 2.3.0 administration guide.

The [Mac 2.3.0.2 guide](https://dl.dell.com/content/manual11715680-dell-display-and-peripheral-manager-for-mac-version-2-3-0-2-administration-guide.pdf?language=en-us)
documents these command forms (page numbers are printed pages):

| Purpose | Documented arguments after the executable | Pages |
| --- | --- | --- |
| Connected identity | `/get -app=ConnectedDevices` | 32 |
| Firmware version, read only | `/get -Display=FWVersion` | 38 |
| Current active input | `/get -Display=ActiveInputSource` | 45–46 |
| Set active input | `/set -Display=ActiveInputSource -value=USB-C` | 46–47 |
| Set PIP/PBP | `/set -Display=PxP -value=pip -input=usb-c,hdmi` | 47–48 |

The guide lists `off` and five three-window modes, `pbp-3a` through `pbp-3e`.
It describes port families HDMI, DP, USB-C, Thunderbolt, and VGA, with numbered
names for repeated ports. The adapter limits inputs to explicitly named examples;
these names do not establish which ports exist on a particular monitor. Selectors
include index, service tag, and model; Dell warns against combining selectors.
Responses show JSON with a `Result` field. The device-data example includes an
active input but does not establish complete PBP layout/region state.

The [Windows 2.3.0 administration PDF](https://dl.dell.com/content/manual4985061-dell-display-and-peripheral-manager-for-windows-version-2-3-0-administration-guide.pdf?language=en-us)
was linked by Dell, but retrieval failed with a timeout during this research.
Its exact syntax and feature coverage remain unverified. No Windows command is
implemented. Older DDM/`WritePxP` examples are not a substitute for this contract.

## The current blockers

The current implementation deliberately does not translate the logical
`left-large-right-stack` layout into a Dell mode letter. It has no verified
contract for all three ordered input assignments, or readback of every region
and the layout. A two-input example does not establish three-input support.
It also has no documented, verified readback that proves PxP is off after a
fullscreen request. Active-input readback alone is insufficient.

Consequently:

- PBP3 preparation raises `UnsupportedFeature`, even with three supplied ports.
- Fullscreen preparation can produce a blocked command preview after validating
  the entire binding. Live application raises before starting any process.
- Identity, firmware version, and active input are the only executable reads.
  These are separate opt-in operations and do not qualify hardware support.
- No VCP values, firmware updater, factory reset, power command, input swapping,
  toggle, SSH command, Deskflow process signal, or arbitrary command passthrough
  is available.

## Adapter API

```python
from deskdeck.adapters import DDPMAdapter

adapter = DDPMAdapter()  # No process execution, including discovery.
adapter.capabilities()

profile = {
    "monitor": {"mode": "fullscreen", "regions": {"main": "main"}}
}
bindings = {
    "monitor": {
        "verified": True,
        "model": "U5226KW",
        "selector": {"service_tag": "TEST001"},  # Synthetic example only.
        "capabilities": ["identity", "firmware", "active_input", "pxp_off"],
    },
    "inputs": {"main": {"verified": True, "port": "Thunderbolt"}},
}
steps = adapter.prepare(profile, bindings)
preview = adapter.apply(steps)
assert preview["status"] == "preview"
assert preview["executed"] is False
```

The `verified` flags are explicit caller attestations based on local evidence.
Example data is not evidence. Keep real selectors, serials, service tags, network
names and machine addresses in private local configuration, out of commits,
screenshots, logs and bug reports. Preview command arrays may contain a selector.

For independently authorized read-only checks, construct a new adapter with an
explicit absolute executable path and `allow_execute=True`. Then use
`prepare_read("identity")`, or `prepare_read("firmware", bindings)` or
`prepare_read("active_input", bindings)`, followed by `apply(steps)`. There is no
PATH lookup. A bound read checks current discovery identity first. An index
selector (`{"index": 1}`) is accepted only for reads; it can change after reconnects.
Profile previews require a stable service-tag selector. The adapter has not
been qualified against an installed DDPM binary. It does not elevate privileges,
grant permissions, or start DDPM automatically.

Only unchanged plans prepared by the same adapter instance are accepted. Fixed
argument lists use `shell=False`; captured stdout and stderr share a bounded
byte budget; every command has a deadline. Timeout, output overflow, malformed
JSON, nonzero exit, or vendor failure raises a typed `AdapterError` subclass and
never returns a success result. Read results may include private device data;
DeskDeck does not publish them. `allow_writes=True` cannot bypass missing support.

## Evidence needed before enabling writes

An adapter contribution must establish all of the following from current official
documentation and explicitly authorized tests on the actual monitor/firmware:

1. A uniquely selected monitor and verified identity before any write.
2. Exact layout-to-geometry mapping, every physical input name, and the defined
   ordering of all three assignments. Missing any assignment must refuse before
   the first write.
3. Semantic readback of the complete layout and every region, including fullscreen
   state, and a mismatch response that never reports successful restoration.
4. A three-region → fullscreen → three-region round trip using explicit full
   state, plus failure/timeout behavior. A successful process exit is insufficient.
5. Calibrated OS display geometry and a separately verified Deskflow activation
   contract before presenting an integrated workspace switch as accepted.

The local test suite uses synthetic executable fixtures only. It verifies refusal,
argument construction, identity mismatch, deadlines, output limits, and vendor
failure handling. It is not monitor, input switching, or Deskflow acceptance.
The repository bundles no Dell binary or administration-guide document.
