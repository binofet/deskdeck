# Official Deskflow macOS GUI lifecycle experiment

**Unqualified, opt-in source adapter. Apply remains disabled.**
`deskdeck.macos_lifecycle` is not imported by the server, CLI or preview UI. Its
tests use fake process/native observations and temporary INIs. They do not prove
that macOS quit, LaunchServices, permissions, ownership observations or real input
work. No native bridge is installed or discovered by this module.

This addresses the GUI ownership boundary: a GUI-owned core can restart after a
core-only stop. There is no assumed GUI Start/Stop/reload IPC. The adapter requests
ordinary termination of the whole official GUI, waits for both exact owned GUI
and core absence, replaces the active GUI INI, and relaunches the official app.
There are no signals, force quit, AppleScript, UI automation, services, daemon
mode, new-instance flags, shell commands or direct core launches.

## Caller-owned prerequisites

Supply a `MacGuiBinding` for an admitted official 1.27 bundle, its actual active
`Deskflow.conf` and UID. The GUI executable is `Contents/MacOS/Deskflow`; the core
is `Contents/MacOS/deskflow-core`. Supply Cocoa objects explicitly to
`CocoaActions`, plus a complete process/readiness observer, bounded monotonic
clock/pause, and an exclusive transition lease. Native bridging/thread/event-loop
behavior is still unqualified; do not block a thread needed by the LaunchServices
completion callback. All injected operations must honor their deadline.

The observer must inventory both exact paths, including conflicts, and establish
official executable/bundle identity, owner UID, process start identity and direct
GUI/core lineage. A basename lookup, PID alone, or responding IPC socket is not
ownership evidence. GUI `start_identity` is its NSRunningApplication launch date
as epoch seconds formatted to six decimals; the quit action rechecks it. The
complete inventory must be fresh within twice the selected polling interval.

`prerequisites_verified` requires admission of the actual GUI default-settings
path, executable/release identity, current graphical session, role permissions,
host-local certificate public fingerprint and existing directional trust. For a
server, the exact prepared topology must already be staged and admitted; this
adapter does not write it. No observer implementation or evidence collector is
included. This boolean is an injected observation, not independent proof.

Prepare a `RoleConfiguration` from `deskdeck.deskflow`'s validated preparation and
the current active INI SHA256. Both role INIs must share the active INI's private
parent/TLS base. The adapter adds `startCoreWithGui=true`, `closeToTray=false` and
`enableUpdateCheck=false`. TLS and peer checks stay enabled, Desktop mode is
required, and it refuses a changed effective certificate path or non-default
2048-bit key-size setting before quitting. It never reads PEM/private-key bodies
or edits trust databases. Clipboard policy is not established by this lifecycle
adapter: qualify it on the controlling server separately.

## Transition and failure behavior

Execution requires `experimental_opt_in=True`; no call from the live Apply route
exists. One adapter permits one attempt with an unchanged prepared plan. Under
the supplied lease it:

1. Checks complete owned-process/prerequisite observations and baseline INI.
2. Uses only `NSRunningApplication.terminate()` on the exact admitted GUI. A
   refusal, close-to-tray behavior, timeout, lingering/orphan core, replaced PID,
   conflicting process or incomplete observation stops before writes/relaunch.
3. Requires two absence samples, then checks absence again around the write.
4. `AtomicIniStore` backs up the original INI to a new random adjacent filename,
   fsyncs it, writes/fsyncs a private temporary file, compares the baseline again,
   checks absence and atomically replaces/readbacks the active INI. Existing
   directories/files must be owner-only, with no symlink components, hardlinks
   or changed baseline. No directories are created. Backup/active files use 0600.
5. Uses `NSWorkspace.openApplication(at:configuration:completionHandler:)` with
   the exact bundle URL, no arguments, no new instance, no running-application
   substitution, and no activation request. There is no open-by-name fallback.
6. Observes a bounded readiness window: exact returned GUI PID/start identity,
   owned child core with independently observed critical configuration semantics
   and intended role, plus qualified role/TLS status from the injected observer.

Readiness uses `role_semantics_sha256()` on the independently read effective
active configuration, not the prepared full-file hash. QSettings can normalize
INI serialization and the official GUI adds logging and first-start/state keys.
The digest covers role, name, Desktop process mode, port, TLS/peer-check settings,
certificate path/key size, disabled enter/exit commands and GUI lifecycle flags;
clients add server address/language sync, servers add interface, external topology
path, clipboard setting and computer names. The observer must establish that
these values apply to the owned core, not merely read a prospective file. Exact
topology content and public TLS/permission observations remain separately admitted
prerequisites. Unrecognized QSettings string encodings refuse. Byte hashes remain
in the atomic-write receipt, where exact bytes are relevant.

Cancellation is checked at observation and mutation boundaries. There is no
automatic retry or rollback. Cancellation/timeout after a launch request cannot
cancel a pending LaunchServices launch; report its outcome as unknown and let
the existing operator observe/recover. After write/launch failure, retain the
backup receipt and independently re-observe before any recovery. A write error
distinguishes replacement completed versus not completed, and a cleanup failure
does not discard that receipt or skip closing the directory descriptor.

The lease only serializes participating callers. Filesystem replacement and
process launch are not one atomic macOS transaction; an external/manual launch
can race observations. This requires runtime qualification and operator control,
not an invented guarantee that the machine cannot change between samples.

`observed_ready` always retains `qualified=false` and `human_accepted=false`. It
does not establish a complete client roster, physical input delivery, USB routing,
monitor geometry or whole-desk restoration. The existing handoff contract still
requires those independent observations and human acceptance.

## Source and test evidence

Run only the source tests with
`python3 -m unittest discover -s tests -p test_macos_lifecycle.py -v`.

- [Normal application termination](https://developer.apple.com/documentation/appkit/nsrunningapplication/terminate())
- [LaunchServices app opening](https://developer.apple.com/documentation/appkit/nsworkspace/openapplication(at:configuration:completionhandler:))
- [New-instance option](https://developer.apple.com/documentation/appkit/nsworkspace/openconfiguration/createsnewapplicationinstance)
- [Running-app substitution option](https://developer.apple.com/documentation/appkit/nsworkspace/openconfiguration/allowsrunningapplicationsubstitution)
- [Deskflow 1.27 GUI startup and native quit/close handling](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/gui/MainWindow.cpp)
- [Deskflow 1.27 GUI/core names](https://github.com/deskflow/deskflow/blob/v1.27.0/src/apps/deskflow-gui/CMakeLists.txt)
- [Deskflow 1.27 settings and TLS path behavior](https://github.com/deskflow/deskflow/blob/v1.27.0/src/lib/common/Settings.cpp)
