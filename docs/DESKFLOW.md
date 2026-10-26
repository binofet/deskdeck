# Deskflow 1.27 preparation and handoff contract

`deskdeck.deskflow` is a pure source preparation module. It does not install or
start Deskflow, read a certificate, connect to a computer, send IPC, touch input,
or change the Dell. Its tests establish configuration and state-machine behavior,
not device acceptance. The existing `core.render_deskflow` export is an older
draft; use this module's topology for the v1.27 operator.

## Inputs and rendered files

Provide exactly four immutable `DeviceBinding` objects, one for each ID in
`core.DEVICES`: `main`, `linux`, `mac`, `windows`. Each binding requires:

- `computer_name`: a distinct ASCII Deskflow name, compared without case for
  duplicate detection. This need not equal the OS hostname.
- `lan_address`: one explicit unicast IP literal; no DNS lookup, automatic
  discovery, interface scope, loopback, multicast or IPv4-mapped IPv6 alias.
  Parsing does not prove LAN membership or reachability.
- `platform`: `Platform.MACOS`, `Platform.WINDOWS` or `Platform.LINUX`.
- `usb_upstream`: a unique planned `Thunderbolt`, `USB2`, `USB3` or `USB4` input.
  This is wiring intent, not an observation that USB is currently there.
- `executable`: the absolute local core path.
- `private_directory`: the intended private data directory.
- `server_settings` and `client_settings`: distinct absolute filenames directly
  in that directory, so changing role does not change the TLS base.
- `topology`: an absolute filename inside the private directory.
- `certificate`: an absolute filename in its `tls` child directory.

Paths are checked lexically, including parent traversal, file overlap and Windows
drive/UNC ambiguity. Windows paths render with forward slashes. The module does
not inspect ownership, ACLs, symlinks, permissions, executability or signatures.
The operator must verify these before declaring a binding qualified. User paths,
addresses, identifiers, certificates and generated configurations remain local;
do not commit them.

For non-portable Windows v1.27, `Settings::settingsPath()` uses the system-drive
`ProgramData/Deskflow` directory even with a custom `-s` INI. This module therefore
requires that directory shape for Windows. The operator must verify the actual
system drive and normal non-portable installation. Portable/service operation is
not part of this first contract. Mac/Linux use the role INI's parent directory.
`tls/trusted-servers` and `tls/trusted-clients` must be qualified in that real base.

Call `prepare_profile(profile_id, bindings, readiness=observations)`.
Fullscreen profiles choose their focus device as server. Shared and four-way
profiles default to `main`; `shared_owner` is an explicit optional override.
The result has exactly one server and three clients. It contains:

- `roles`: each immutable `RoleConfiguration` has the selected `settings_path`,
  UTF-8 `settings_ini`, `settings_digest`, and an argv tuple
  `(absolute_core, "server" | "client", "-s", absolute_ini)`.
- `server_device`, `usb_upstream`, `expected_clients` (device IDs), and
  `expected_computer_names` (the names used in Deskflow's actual IPC roster).
- `topology_text`: links and host-switch hotkeys derived from `core.logical_routes`.
  Computer declarations are in the server INI, not a `section: screens` block.
- `readiness`: the immutable operator observations retained with the plan.
- `blockers` and `prerequisites_satisfied`: derived prerequisite metadata only;
  blockers cannot be replaced independently of the retained readiness.

Strings are deterministically quoted using QSettings INI escaping. Control
characters and line separators are rejected rather than interpolated. Every role
uses TLS and peer fingerprint checks, Desktop `processMode=1`, port 24800, and the
host's explicit certificate. Server INIs declare all four `[computer_NAME]`
groups and the external topology path. Client INIs use only the selected server's
literal address. There is no `-c`, `--new-instance`, shell command or certificate
generation in the prepared argv.

## Readiness is supplied by the operator

Each `DeviceReadiness` is tied to the exact `DeviceBinding.digest`. It records a
public `certificate_sha256`, `trusted_servers` and `trusted_clients` fingerprint
sets, plus explicit booleans `executable_verified`, `private_paths_verified`,
`client_permission_granted` and `server_permission_granted`. These are observations
from the operator, not checks performed by this module. Requalify changed bindings.

For this selected role plan, the server needs capture permission and must trust
each client certificate; every client needs injection permission and must trust
the selected server certificate. Missing certificates, trust, permissions or
verification leave blockers. Reusing one certificate across different hosts also
blocks. An optional future server needs its server permission and reverse trust
qualified when that profile is prepared. Preserve each host's own certificate
when changing role; never distribute its private key to its peers.

On Mac, the human grants Accessibility to the installed app/core and Input
Monitoring for capture in System Settings → Privacy & Security. On Linux Wayland,
the corresponding capture/remote-desktop portal and user consent must work in the
graphical session. Desktop Windows qualification covers normal applications;
secure/UAC desktop operation is not established by this plan.

## Transaction acknowledgements

Create `HandoffTransaction(preparation, transition_id)` with a fresh operator
transition ID. Every acknowledgement repeats that ID; stale or out-of-order
events refuse without advancing. The live actor must serialize transitions and
retain its rollback profile independently. The first operator contract stops all
four prior owned cores, or verifies their absence, before starting any new role.

| Method | Required external observation |
|---|---|
| Constructor | `prepared`; generation alone is incomplete. |
| `old_owned_stopped(id, devices)` | All four previous owned cores exited or are verified absent; prerequisites satisfied. |
| `usb_applied(id, device, upstream, verified=True)` | Actual Dell USB readback matches this plan's server and upstream. |
| `server_listening(id, device, pid, settings_digest, process_owned=True)` | This exact owned core is listening, with the rendered INI digest and a positive observed PID. |
| `expected_roster(id, server_pid, connected_computers, tls_verified=True)` | Same server process, exactly the three expected computer names, authenticated TLS; no missing, extra or duplicate client. |
| `human_accepted(id, accepted=True, evidence_reference=...)` | Human tested input and supplied a retained evidence reference. Only now is `complete` true. |
| `fail(id, code)` | Terminal failure. No later acknowledgement can convert this transaction to completion; recovery needs an independently controlled rollback/new transaction. |

`status()` identifies its evidence as `operator_acknowledgements_only` and always
reports `execution_available=false`, `geometry_qualified=false`. Completing this
sequence neither operates a process nor proves arbitrary physical monitor seams.
It also does not independently recheck an operator's factual assertions.

The separate live actor should own child process handles, executable identity,
start generation, config digest, bounded logs and actual exits. Do not start a
parallel core while a GUI/service still owns one, use `--new-instance`, or stop
processes by name. v1.27 internal local IPC supports build-version handshake,
`stop`, and status notifications; it is not a public role-changing API. A role
change needs an owned stop/start. Unix SIGHUP can reload topology for an unchanged
server; it does not replace this role transition.

## Geometry and acceptance

Deskflow represents each computer's enclosing desktop rectangle. It cannot name
the laptop display and Dell region as separate computers. An unselected Dell
input may remain enumerated and introduce invisible desktop edges. The generated
computer-edge routes are unqualified until tested against each profile's actual
OS display bounds. Host-switch hotkeys provide a simpler initial test path;
never describe all physical seams as seamless merely because clients connected.

The live qualification should exercise Main → each fullscreen owner → Main and
shared restoration, with keys/buttons released and harmless text-editor input.
Observe Dell USB, exactly one server, authenticated client roster and human input
separately; test failure/rollback before relying on this for the primary keyboard.
The source tests perform none of those hardware actions.

## Primary source references

- [v1.27 CLI options](https://github.com/deskflow/deskflow/blob/v1.27.0/src/apps/deskflow-core/CoreArgs.h)
- [Settings keys and Desktop/CoreMode enum values](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/common/Settings.h)
- [Settings/TLS directory behavior](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/common/Settings.cpp)
- [INI-declared computer roster](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/server/Config.cpp)
- [Client address selection](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/deskflow/ClientApp.cpp)
- [Server topology reload](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/deskflow/ServerApp.cpp)
- [Local core stop/status IPC](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/deskflow/ipc/CoreIpcServer.cpp)
- [TLS fingerprint verification](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/net/SecureSocket.cpp)
- [Mac enclosing display rectangle](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/platform/OSXComputer.mm)
- [Windows virtual-desktop rectangle](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/platform/MSWindowsComputer.cpp)
