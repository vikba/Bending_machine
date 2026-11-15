# Architecture review

Review date: 2026-11-14. Scope: scaffold, implementation instructions, architecture,
and service/interface specifications. The user accepted this review's simplifications
and ACR-1 through ACR-9 on 2026-11-14. The module specs, architecture, implementation
instructions and scaffold contracts now reflect them. Runtime bodies remain stubs;
the following records the rationale and implementation validation still required.

## Assessment

The package boundaries are reasonable for this application. The unnecessary complexity
is mainly in coordination between objects, rather than the number of files. Most code
is still unimplemented, so simplifying a stub does not demonstrate that the resulting
application works. The current test suite only checks that modules import.

Keep these parts:

- Independent controller, camera and force threads, plus the GUI thread. Blocking stage
  moves must not interrupt images or overload checks (NFR-002, NFR-004).
- Direct emergency `Stage.stop()` and cancellable waits; do not route emergency stopping
  through a command queue or an event subscriber.
- Pure curvature, vision and sequence logic. These boundaries make numerical regression
  and workflow tests possible without hardware or Qt.
- Hardware interfaces and the result-writer protocol. Real and simulated implementations
  are required, and failed saves need test doubles.
- A shared simulated rig model so camera, force and position describe the same experiment.
- Settings validation, immutable snapshots, and the Qt bridge. Removing these would move
  validation and concurrency responsibilities into widgets.

## Accepted simplifications

### 1. Save on the existing controller thread

Sources: `03_spec/services_recorder.md` sections 2, 4, 6;
`03_spec/services_controller.md` section 4.2;
`03_spec/io_api.md` section 6; `02_architecture.md` section 4.1.

Previously `SaveResult` was specified to run on the controller, start a separate save thread, and send
completion back through the publisher into the controller queue. This creates shared
recorder state, a save lock, and another thread to account for during shutdown.

Accepted: call the writer synchronously from `Recorder.save()` on the controller thread.
Keep `ResultSaved` and `SaveFailed` for the GUI; let the controller choose its next mode
when the call returns. Keep pending data until success, retaining it after any failure.
The existing method signature stays; its documented timing contract now changes.

The GUI, camera and force monitor continue independently. Saving delays subsequent
controller commands and device-fault cleanup, including on a slow filesystem. Emergency
stopping must remain direct. Validate this tradeoff with a delayed writer and an overload
during saving before accepting an implementation. A dedicated save worker is justified
only if independent controller responsiveness during saving is required.

### 2. Inject discovery functions instead of adding another command/event pair

Sources: `03_spec/README.md` ACR-3; `03_spec/ui_bridge.md` sections 1, 2, 4;
`03_spec/hardware_discovery.md` sections 2, 6; `AGENTS.md` layering rules.

The bridge spec previously required a hardware discovery import despite the repository's
UI import rule. The listed alternative added a `ListDevices` command and `DevicesListed` event.

Accepted: `app.main` supplies the bridge with two zero-argument callables returning the
port and camera lists, binding simulation and producer-path arguments there. Keep the
existing background enumeration and public `devices_listed` signal. This removes the UI
hardware dependency without creating a discovery service or more controller messages.
The bridge constructor and `App` scaffold fields now declare these callables. Module
test tables require verifying their wiring and background execution when implemented.

### 3. Keep the remaining scaffold small

Use the existing latest-value hub, one publisher and one controller queue. Do not add a
generic workflow framework, plugin registry, dependency-injection container, event replay,
or worker superclass. The two sequences can remain explicit state machines. Their few
duplicated action dataclasses do not justify a new abstraction by themselves.

Keep the current package and panel split. Consolidating files would require widespread
spec/API changes with little reduction in runtime complexity. Keep the current 15 Hz
display coalescing until implementation provides evidence that it needs changing.

## Specification conflicts resolved by acceptance

The repository's `AGENTS.md` says to stop and ask when specifications contradict each
other; it also forbids independently changing public signatures or behaviour.

1. **Approval status:** `AGENTS.md` calls ACR-1 through ACR-9 accepted;
   `03_spec/README.md`, Architecture change requests, previously said each needed a yes/no.
   Resolved: the spec index now records ACR-1 through ACR-9 as accepted.
2. **Saving guarantee:** `03_spec/io_api.md` section 2 previously promised no partial file
   on failure; `03_spec/README.md` ACR-6 said results and metadata appear or fail together. However,
   `03_spec/io_results_lvm.md` section 4 replaces two files sequentially and explicitly
   permits final files to remain when legacy metadata writing fails. Those operations
   cannot provide the stronger guarantee. Resolved: document recoverable failure with
   pending data retained and final files possibly remaining. Do not add a transaction
   framework to disguise this mismatch.

## Implementation guidance

The affected module specs, architecture, scaffold signatures/docstrings and implementation
instructions are aligned. Requirement IDs and existing test-case IDs are preserved;
the test tables include synchronous saving, injected discovery, partial output failure,
and overload/fault handling during a slow write.

Runtime implementation should proceed through pure logic, one simulated test/save path,
stop/overload/fault cases, UI, and finally real-driver bench validation. The import smoke
test is not evidence that any of those workflows work.
