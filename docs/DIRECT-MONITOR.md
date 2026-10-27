# Explicit two-layout monitor backend

The default server and tray remain previews. A macOS operator may explicitly select a private, owned mode-0600 JSON file with `serve --direct-binding /absolute/private/binding.json`. Its only keys are `executable`, `sha256` and `monitor_uuid`. No discovery, helper invocation or settings change occurs at startup, status or plan review. Never publish the binding or monitor identity.

Only `focus-main` and `shared-four` dispatch to the pinned two-state native helper. Each needs the exact opposite starting state. Applying the already-selected target is refused before writes; it is not a no-op success. The helper checks identity, capabilities and the complete starting quartet, then writes two video settings once each with strict intermediate and final readback. USB and E8 are never written. Darkmatter USB ownership is required throughout. Existing Deskflow server, shortcuts and roles remain; automatic screen-edge routing and OS display reconciliation are not applied.

A successful receipt proves target monitor registers at the time of readback. It does not prove visible placement, physical input or continuing health. Status reports only the last verified layout in this server session. No initial layout is inferred.

## Failure and recovery

There is no automatic retry or rollback. Apply is serialized without a queue; the browser waits up to72 seconds for a helper bounded to65 seconds plus cleanup. A helper whose exit cannot be confirmed quarantines further helper launches. Any failed transition makes the reported layout unknown, including partial writes, lost replies and unexpected USB ownership. Browser errors require fresh review, never automatic resubmission.

The helper cannot resume from its intermediate state. Keep Darkmatter's laptop keyboard available. If a transition fails, stop Apply requests and recover with the monitor controls: turn PBP off, then select Thunderbolt. Confirm the visible fullscreen desk and physical keyboard/mouse before any further qualification. A recovery statement is not an executed recovery or acceptance receipt.

This backend is source integration until an actual app-triggered complete switch, visible/input confirmation and bounded return are recorded separately. The other four profiles and arbitrary editor drafts have no hardware activation path.
