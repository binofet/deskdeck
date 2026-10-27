# Computers and display anchors

A desk setup records the computers you use, their screens, and where each screen
belongs. Adding a computer records a plan; it does not install Deskflow, connect
a client, move USB input or activate a monitor profile.

Keep these four choices separate:

| Choice | What it describes | Example |
| --- | --- | --- |
| Computer | One machine, with its own local ID and readable name. | A laptop called `work-laptop`. |
| Display anchor | Where one of its screens belongs: left, right, above or below the Dell; on the Dell; or unplaced. | Its built-in screen on the left, plus an external display on the Dell. |
| Dell pane and video source | A visible part of the Dell and the input/OS display stream supplying it. | The laptop's Thunderbolt video in the lower-left pane. |
| Keyboard USB owner | The computer receiving the monitor-connected keyboard and mouse through the selected USB upstream. | A workstation attached to the Dell's USB data connection. |

One computer can have several displays. Its built-in screen and a Dell pane still
belong to the same Deskflow computer. The setup name is a label; it does not rename
the actual Deskflow peer. Changing video does not establish USB ownership or the
Deskflow server. Those bindings need their own verified state.

## Choose the screens a computer actually has

**Laptop with a built-in display.** Add the laptop once. Its built-in screen can
anchor left of the Dell while another display uses a Dell pane. Measure both
active OS displays. Closing the lid, disabling a screen or changing scale
invalidates those measurements; a saved anchor does not keep a screen active.

**Computer with a separate external display.** A workstation's small monitor can
anchor right of the Dell. If the workstation also supplies a Dell pane, bind
that pane to its other OS display stream. No built-in screen is required.

**Computer with no separate display.** A mini PC using only the Dell has an
external display anchored to `dell`. A truly headless computer instead has
`displays: []`. It can be recorded as an input owner, but supplies no cursor
anchor. Neither its name nor a hidden pane creates another visible screen or
graphical session.

For example, these are three valid computer records in the new model:

```json
{
  "schema_version": 1,
  "input_owner": "laptop",
  "computers": [
    {"id": "laptop", "name": "Laptop", "usb_upstream": "Thunderbolt",
     "displays": [{"id": "laptop-screen", "name": "Laptop screen",
                   "kind": "built-in", "anchor": "left"}]},
    {"id": "mini-pc", "name": "Mini PC", "dell_input": "DP1",
     "displays": [{"id": "mini-dell", "name": "Mini PC on Dell",
                   "kind": "external", "anchor": "dell"}]},
    {"id": "server", "name": "Server", "displays": []}
  ]
}
```

This example uses the new computer-model format, not the existing `--setup`
wiring-file format. Display IDs are local record IDs, not measured OS display
identities. Optional `placement` rectangles use 0–1 coordinates on the whole desk
canvas, not Dell pane coordinates or OS pixels. A direction or missing rectangle
does not supply edge calibration. Use `unplaced` while location is unknown.

## Add a computer to the plan

1. Choose a distinct local ID and readable name. Preserve the actual Deskflow
   name separately; names differing only by case cannot identify separate peers.
2. Add actual displays, their built-in/external kind and intended anchors.
3. Bind Dell panes to video inputs and OS display streams. Record USB ownership
   separately; choosing fullscreen video does not choose an input server.
4. Measure every active display and the complete desktop before preparing edges.
   Keep the working switch hotkeys while qualifying crossing and return.

A pane need not mean another computer or cable: Screen Partition carries several
display streams over one input, and some PBP layouts duplicate an input. Bind
each pane explicitly; see [native layouts](NATIVE-LAYOUTS.md).

## Edges follow complete computer desktops

Deskflow links the outer edges of complete computer desktops. A seam between
displays on the same computer belongs to that computer's OS arrangement.

If a laptop screen lies left of its Dell display in the OS arrangement, that
Dell display's left edge is internal. A physical pane seam there cannot simply
become a Deskflow left-edge link. Both computers need measured boundaries and
matching usable segments; corner contact is insufficient. Unknown, stale or
incompatible geometry leaves routes not ready. A diagram or hotkey PASS supplies
no geometry; see [measured edge routes](EDGE-ROUTES.md).

## Remove a computer without leaving broken references

Review its profile panes, displays, USB ownership, Deskflow role, routes and
hotkeys. Reassign or remove those references together. Before changing the
physical input owner, verify a usable replacement and return path.

The model refuses removal while displays or input still reference the computer
unless reassignment is explicit. Cable assignments never migrate implicitly.
Moving a record cannot physically transfer a built-in screen to another computer.

Removal does not uninstall software, remove trust records or disconnect USB.
Detaching or disabling a display invalidates affected geometry and routes;
measure the remaining desktop before reusing them.
