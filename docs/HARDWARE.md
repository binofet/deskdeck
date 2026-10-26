# Hardware support and qualification

DeskDeck v0.1 is a local profile preview and an adapter foundation. Live
qualification of a Dell U5226KW is in progress; integrated switching is unaccepted.
A saved desired state is useful because a monitor may forget its PBP layout when
PBP is disabled and enabled, but storing that state alone does not prove that it
can be restored. No monitor utility is installed or discovered automatically.

## Direct-control qualification

The active qualification uses an independently reviewed, constrained DDC/CI
helper, with stable monitor identity checks and strict reply validation. It does
not install or run Dell software. The helper is separate from the public preview;
the panel's Apply action remains disabled.

Observed on 26 October 2026:

- Strict reads of active input, E7, E8 pane assignments, and E9 PBP mode passed.
- One same-value write of a captured E8 pane map passed unchanged four-register
  readback. This alone does not qualify restoration.
- A bounded four-way mode and primary-source sequence produced the intended
  four corners, confirmed by the person at the monitor. Early post-transition
  reads failed strict validation; a later bounded checkpoint succeeded.
- The controlled return selected Thunderbolt primary before disabling PBP.
  With five-second settling waits, all four register checks passed after both
  steps. Fullscreen picture and physical input restoration were human-confirmed.
- Physical USB input stayed on the upper-left computer. Automatic input-owner
  control, role handoff, and per-profile Deskflow geometry remain unqualified.

E7's current-value word alone did not distinguish the observed USB owners. A
static protocol candidate is not a qualified write: exact-device capability
evidence, complete reply decoding, and physical input confirmation are still
required. No firmware patch or USB-control write has been performed.

The DDPM documentation and adapter below are historical reference research,
not the active installation or live-control path.

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

The [Windows 2.3.0 administration guide](https://dl.dell.com/content/manual4985061-dell-display-and-peripheral-manager-for-windows-version-2-3-0-administration-guide.pdf?language=en-us)
was retrieved and inspected after an initial download timeout. Printed page 10
locates its CLI at
`C:\Program Files\Dell\Dell Display and Peripheral Manager\Plugins\Subagent\cli.subagent.exe`.
Table 175 on page 77 maps `pbp-3a` to a large left region and stacked right regions.
The [U5226KW user guide](https://dl.dell.com/content/manual32544295-dell-ultrasharp-52-thunderbolt-hub-monitor-u5226kw-u5226kw-wost-user-s-guide.pdf?language=en-us),
page 58, Configuration 11 identifies left as Win1, upper right as Win2, and lower
right as Win3. This establishes the documented geometry for Shared; it does not
establish how three CLI input arguments bind to those windows.

The Windows guide's Table 173 on page 76 prints a two-input example with duplicated
`-value` flags. DeskDeck does not silently correct that example or turn it into a
write command. No Windows command is implemented. Older DDM/`WritePxP` examples
are not a substitute for a current verified contract.

Both guides document ExportSettings/ImportSettings (Mac pages 83–84; Windows
pages 111–112), but neither supplies an exported schema or expressly guarantees
complete PxP mode and region coverage. Their DeviceData examples contain an active
input, not a complete three-region state. Export/import therefore remains a
possible qualification route, not an implemented or verified restore mechanism.

## The current blockers

The documented `left-large-right-stack` geometry corresponds to `pbp-3a`. The
current implementation still has no verified contract for all three ordered input
assignments, or readback of every region and the layout. Neither guide provides
an explicit complete three-source example or PxP readback command. This is a
documentation gap, not evidence that the hardware cannot support restoration.
A two-input example does not establish three-input support.
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
