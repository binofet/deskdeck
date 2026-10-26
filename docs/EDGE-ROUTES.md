# Calibrated panel-edge route preview

`deskdeck.edge_routes` is a pure planner. It imports the pure native-layout catalog,
not a monitor/process/input adapter. It never activates edges, changes Deskflow
configuration, edits the UI, starts an application or reads host state. Existing
host-switch hotkeys remain untouched as the fallback.

```python
from deskdeck.edge_routes import plan_edge_routes

plan = plan_edge_routes("pbp-11", bindings=region_bindings, hosts=host_receipts)
preview = plan.to_dict()  # JSON-compatible links, seam kinds and blockers.
assert preview["activation_available"] is False
```

`ready` means only that the supplied calibrated geometry can represent all panel
seams. It is false when required measurements are unknown/inconsistent or any
seam is unrepresentable. It is not physical edge acceptance or permission to
activate. `physical_edges_verified` remains false. A blocked plan can retain
candidate links for inspection, but `preview_text` is null; no partial topology
is offered for activation. A fullscreen plan can be geometrically ready with no
panel seams or links. That does not connect the other computers' laptop screens.

## Bindings and coordinate rules

The catalog's `regions` are normalized against the entire Dell panel, x right and
y down. A `RegionBinding(region_id, host_id, display_id, viewport,
mapping_verified=True)` maps each visible region to a measured rectangle in that
host's Deskflow coordinate space. Normally `viewport` is the OS display's entire
rectangle; a cropped mapping needs its actual calibrated viewport. No stretch,
crop, origin, DPI conversion or correspondence is inferred from the panel layout.
Only positive, axis-aligned orientation is implemented; rotation/mirroring refuse.

`HostGeometry` supplies the exact Deskflow enclosing desktop rectangle, every
active `DisplayGeometry`, matching `layout_id`, `complete=True` and
`coordinate_space_verified=True`. Each display needs bounds, `rotation=0` and
`mirrored=False`. Omitting even an apparently unused laptop/phantom head makes
an enclosing computer-edge calculation unreliable. The planner verifies the
reported desktop is exactly the bounding rectangle of all active heads and
refuses overlapping display rectangles. Gaps are allowed; they are not screens.

Host IDs are logical local IDs. `computer_name` is the actual Deskflow name and is
preserved verbatim, including lowercase names. Names that collide without case
refuse, matching Deskflow's CaselessCmp semantics. OS display IDs are opaque and
may include native Windows device-name backslashes; they never enter link syntax.

An `input_slot` denotes a physical input selector, not a host or OS display.
Distinct slots can belong to the same host. Repeated slots must belong to one
host, but their iMST panes can map to different OS display heads. Bind every pane
individually. Reusing overlapping OS viewports across panes refuses: duplicating
an input does not establish a unique physical cursor location.

## Seam representation

Only shared line segments with positive length count as adjacency. Corners do
not link. Regions must tile the panel with no overlap. Each seam is classified:

- `same_input_internal`: repeated physical-input slot, normally iMST streams.
- `same_host_internal`: distinct input slots mapped to one computer.
- `inter_host`: a candidate Deskflow transition between different computers.

Internal seams receive no Deskflow self-link. Their measured OS segments must
coincide with matching orientation, or the plan reports `internal_os_seam_misaligned`.
An inter-host seam must map to the correct outer edge of **both complete computer
rectangles**. An edge inside a laptop-plus-Dell desktop cannot be represented by
a Deskflow computer link and is blocked. A laptop above the Dell can instead
change a valid outer-edge segment's percentage range; negative origins are handled.

Every usable inter-host seam emits two inverse links, with independent source
and destination percentage intervals. Candidate intervals are checked for
disjointness on each computer/side after Deskflow's float32 conversion. There are
no zero-length intervals, reversed endpoints, corner-only routes or one-sided
pairs. A segment must contain a reachable integer cursor sample; percentage
rounding must remain below half a cursor-coordinate unit. Whole-panel ratios
from the catalog are retained, including the 853/2560 split. Float catalog ratios
are conservatively recovered as simple fractions; callers can supply exact
`Fraction` values when exact small spans matter.

The planner covers physical **panel** seams. It does not invent where separate
laptops sit outside the Dell, their physical sizes or external physical gaps.
Those require a separately measured whole-desk placement contract. Moving an OS
display to make a desired boundary representable is a separate user-visible
operation, not something this planner performs.

## Minimal body-free receipt for each participating host

The later operator should retain the following common fields in one current
layout/session capture. No screenshots, window titles, application content,
keystrokes, credentials, certificates, private keys or full logs are needed:

| Field | Required meaning |
| --- | --- |
| `schema_version` | `1` for the local receipt schema. |
| `host_id`, `computer_name` | Local logical identity and exact configured Deskflow name. |
| `layout_id`, `observed_at_utc`, `geometry_generation` | Current native layout and capture/session generation for invalidating stale receipts. |
| `session_kind` | macOS, Windows, Wayland or X11; not inferred from the machine's OS label. |
| `coordinate_space` | Explicit top-left, x-right/y-down coordinates matching the actual Deskflow process, including its DPI/scaling awareness. |
| `desktop` | Actual Deskflow computer shape `{x,y,width,height}`, including negative origins. |
| `active_displays` | Complete list of `{display_id,bounds:{x,y,width,height},rotation,mirrored}` in that same coordinate space, including laptops and still-enumerated external heads. |
| `region_bindings` | Each visible catalog `{region_id,display_id,viewport:{x,y,width,height},mapping_verified}` for that host; the catalog already supplies its physical input group. |
| `complete`, `coordinate_space_verified` | Explicit verification results; absent/unknown is false, never a default success. |

If the OS observer supplies different units, retain the source units and measured
per-display affine conversion into Deskflow coordinates; mark verification false
until that conversion is established. A display ID can be a local opaque token
with a retained binding to the OS head; hardware serial numbers are unnecessary.
`geometry_generation`, timestamp and session metadata are retained by the operator;
the pure planner checks layout correspondence and flags, not wall-clock freshness.
Invalidate receipts after display attach/detach, layout, resolution, scale,
rotation, mirroring, session or sleep/wake changes before reusing a route plan.

Map this receipt to the immutable `HostGeometry`, `DisplayGeometry` and
`RegionBinding` values. These values remain caller-supplied observations. Human
mouse/typing acceptance of a client does not by itself supply display geometry or
qualify a physical edge. Test harmless crossing and return, segment endpoints,
scaling and hotkey recovery separately before activation is considered.

## Primary implementation evidence and source tests

- [Deskflow 1.27 link parsing and interval limits](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/server/Config.cpp)
- [Fraction-to-destination transform and CaselessCmp](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/server/Config.cpp#L306)
- [Whole-computer coordinate mapping with cursor-center sampling](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/server/Server.cpp#L528)
- [macOS enclosing active-display rectangle](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/platform/OSXComputer.mm#L1335)
- [Windows virtual-screen rectangle](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/platform/MSWindowsComputer.cpp#L1434)
- [Native catalog geometry and signal distinctions](NATIVE-LAYOUTS.md)

`python3 -m unittest discover -s tests -p test_edge_routes.py -v` tests synthetic
receipts, all 32 catalog entries, reciprocal percentages, laptop conflicts,
negative origins, internal/iMST seams, unknown measurements, float collapse and
zero-area/overlap refusal. No native observation or activation is performed.
