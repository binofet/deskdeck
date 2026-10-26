# Dell U5226KW native layouts

The U5226KW guide documents 15 PBP configurations, the same 15 geometries for
single-input Screen Partition, and one mixed PBP/Screen Partition arrangement.
DeskDeck's pure catalog includes these 31 mode/geometry combinations plus
fullscreen. A catalog entry describes the panel; it does not activate the monitor,
Deskflow, USB ownership or an OS display arrangement.

Primary sources are Dell's [U5226KW manuals page](https://www.dell.com/support/product-details/en-us/product/u5226kw-monitor/resources/manuals)
and [U5226KW User's Guide](https://dl.dell.com/content/manual32544295-dell-ultrasharp-52-thunderbolt-hub-monitor-u5226kw-u5226kw-wost-user-s-guide.pdf?language=en-us).
Page numbers below are the printed pages. The diagrams on pages 58-60 were
visually checked; the PDF and Dell software are not bundled with DeskDeck.

## Geometry and signal identity

Coordinates start at the panel's upper-left corner: x increases rightward and y
downward. All rectangles are normalized to the full 6144 by 2560 panel. In the
table, each rectangle is `(x, y, width, height)` and `Wn` is Dell's printed Win n.
Window numbering is significant: four-quadrant Win3 is bottom right and Win4 is
bottom left. It is not a row-by-row list.

| Configuration | Geometry | Rectangles in printed window order |
| --- | --- | --- |
| 1 | Equal left/right | W1 `(0,0,1/2,1)`; W2 `(1/2,0,1/2,1)` |
| 2 | Three-quarter left | W1 `(0,0,3/4,1)`; W2 `(3/4,0,1/4,1)` |
| 3 | Three-quarter right | W1 `(0,0,1/4,1)`; W2 `(1/4,0,3/4,1)` |
| 4 | Two-third left | W1 `(0,0,2/3,1)`; W2 `(2/3,0,1/3,1)` |
| 5 | Two-third right | W1 `(0,0,1/3,1)`; W2 `(1/3,0,2/3,1)` |
| 6 | Equal top/bottom | W1 `(0,0,1,1/2)`; W2 `(0,1/2,1,1/2)` |
| 7 | 853-row top, 1707-row bottom | W1 `(0,0,1,853/2560)`; W2 `(0,853/2560,1,1707/2560)` |
| 8 | Three equal columns | W1 `(0,0,1/3,1)`; W2 `(1/3,0,1/3,1)`; W3 `(2/3,0,1/3,1)` |
| 9 | Wide center column | W1 `(0,0,1/4,1)`; W2 `(1/4,0,1/2,1)`; W3 `(3/4,0,1/4,1)` |
| 10 | Stacked left, right half | W1 `(0,0,1/2,1/2)`; W2 `(1/2,0,1/2,1)`; W3 `(0,1/2,1/2,1/2)` |
| 11 | Left half, stacked right | W1 `(0,0,1/2,1)`; W2 `(1/2,0,1/2,1/2)`; W3 `(1/2,1/2,1/2,1/2)` |
| 12 | Top half, split bottom | W1 `(0,0,1,1/2)`; W2 `(1/2,1/2,1/2,1/2)`; W3 `(0,1/2,1/2,1/2)` |
| 13 | Split top, bottom half | W1 `(0,0,1/2,1/2)`; W2 `(1/2,0,1/2,1/2)`; W3 `(0,1/2,1,1/2)` |
| 14 | Four equal columns | W1 `(0,0,1/4,1)`; W2 `(1/4,0,1/4,1)`; W3 `(1/2,0,1/4,1)`; W4 `(3/4,0,1/4,1)` |
| 15 | Four quadrants | W1 `(0,0,1/2,1/2)`; W2 `(1/2,0,1/2,1/2)`; W3 `(1/2,1/2,1/2,1/2)`; W4 `(0,1/2,1/2,1/2)` |

These geometries are transcribed from the guide's diagrams and window timings,
not inferred from DDC values. Configuration 7 preserves the printed 853/1707-row
split instead of rounding it to exactly one-third/two-thirds.

The signal interpretation depends on the selected mode:

| Catalog IDs | Kind | Independent input slots | Interpretation and source |
| --- | --- | --- | --- |
| `fullscreen` | `fullscreen` | 1 | One full-panel region with both PBP and partition off; page 58's Off option. |
| `pbp-01` through `pbp-15` | `pbp` | 2 for 1-7; 3 for 8-13; 4 for 14-15 | Each Win n has its own input selector; pages 57-58. |
| `screen-partition-01` through `screen-partition-15` | `screen-partition` | 1 in every entry | Multiple iMST streams over one physical DP/Thunderbolt input; pages 8 and 59. |
| `pbp-with-screen-partition` | `pbp-with-screen-partition` | 2 | Four quarters: PC1 streams 1/2 across the top, PC2 streams 1/2 across the bottom; page 60. |

In the mixed entry, `win1`/`win2` use input slot 1 and `win4`/`win3` use slot 2.
Those region IDs reuse Configuration 15's geometry; the mixed diagram labels PCs
and streams instead of assigning Win numbers. Its bottom-left pane is PC2 stream
1 and bottom-right is PC2 stream 2. Four visible panes therefore do not imply four
physical inputs or four independently selected computers.

An input slot counts an independent video selector, not necessarily a distinct
computer or connector in use. Page 57 allows an input to be duplicated in
equal-sized windows and restricts duplication when window sizes differ. The
catalog does not turn those rules into a generic binding validator.

Screen Partition is an internal MST feature, not merely four application windows
or a crop of one desktop. Its panes can be separate host display streams over
the same input. The guide limits it to DP/TBT and warns that GPU capability and
link bandwidth can prevent native timing or leave panes dark (pages 59-60).
Page 59 also lists lower refresh rates for several partition configurations than
page 58's PBP examples. The catalog records geometry without promising a timing,
OS compatibility or multi-display stream support on a particular machine.

## Pure catalog API

```python
from deskdeck.layouts import get_layout, layout_catalog

catalog = layout_catalog()  # Fresh, JSON-compatible values; no I/O.
layout = get_layout("pbp-11")
assert layout["input_slots"] == 3
assert layout["regions"][1] == {
    "id": "win2", "x": 0.5, "y": 0.0,
    "width": 0.5, "height": 0.5, "input_slot": 2,
}
```

Each entry contains `id`, `label`, `kind`, `native_configuration`, `regions`,
`input_slots`, `capabilities`, `source` and `notes`. `input_slots` is an integer
count; region `input_slot` values are integers from 1 through that count. Repeated
slot numbers explicitly identify shared physical inputs. `native_configuration`
is the printed 1-15 number, or null for fullscreen and the mixed mode.
`source` contains the official PDF URL and printed page numbers.

Every entry has `hardware_control_qualified=false` and
`hardware_mode_code=null`. These describe the catalog's lack of an activation
contract. They do not negate separately recorded qualification of a specific
private device/helper. Printed Configuration 15 does not imply E9=15; visual
window order does not establish any VCP packing, source argument order or USB
owner command. No public per-layout DDC code table is claimed here.

A routing layer must bind input slots to actual machines and measured OS display
IDs, including individual iMST streams and enclosing Deskflow computer bounds.
Panel coordinates alone cannot establish an OS display's coordinates or prove
that a pane seam lies on a whole computer's outer edge. Unknown measurements
must remain unknown. Catalog access and JSON serialization perform no discovery,
process launch, hardware operation or network access.

## Could custom firmware add layouts?

There is no demonstrated U5226KW custom-firmware path or firmware patch in this
project. The guide mentions firmware updates (page 16), but that establishes an
update feature, not a custom-layout SDK, an unlocked image format or a programmable
compositor. No such interface was established by the primary documents reviewed.

Dell's [U5226KW Statement of Volatility, Table 1](https://dl.dell.com/content/manual28251400-statement-of-volatility-dell-ultrasharp-52-thunderbolt-hub-monitor-u5226kw.pdf?language=en-us)
identifies separate scaler, hub, Thunderbolt and PD firmware storage. It marks
these components as not user accessible for external data and describes hardware
and software write protection. This does not prove that modifications are
impossible; it supplies no supported custom-firmware development route.

The engineering inference is conditional: another arrangement could be feasible
only if the scaler's input/stream, scaling, memory and composition resources can
represent it and suitable firmware can expose it. The documents do not establish
those internal resource limits or a safe implementation/recovery contract. A
firmware change cannot be assumed to add physical ports, link bandwidth or host
GPU/OS stream support. A missing OSD layout is not evidence that a new DDC number
or a firmware edit can enable it.

The next evidence needed for a firmware proposal is a Dell-supported extension
or official release adding the arrangement, or documented scaler/firmware
interfaces and hardware limits sufficient to establish feasibility. An external
video compositor is a separate possible architecture; it is not a native monitor
layout. This catalog includes no updater, flashing procedure, firmware image,
vendor software installation or experimental command.
