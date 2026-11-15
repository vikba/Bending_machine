# Specification: index

This folder is the source of truth for the code. Inputs: `docs/01_requirements.md`, with `docs/00_legacy_inventory.md` for legacy behaviour, values and formats. The original architecture and its review are kept for history in `docs/archive/`; where they differ from this folder, this folder wins.

## Files

One file per package. Each `##` section is one module, or one part of a module; read only the section you need.

| File | Modules (sections) | Main requirements |
|---|---|---|
| [core.md](core.md) | `models`, `errors`, `vision`, `measurement` (curvature, force), `sequences` (test run, preconditioning) | FR-022 to FR-027, FR-030, FR-040 to FR-046, FR-061 to FR-064, FR-071, FR-073, FR-100, NFR-007 |
| [hardware.md](hardware.md) | `api`, `zaber_stage`, `futek_loadcell`, `vimba_camera`, `sim` (SimRig, drivers), `discovery` | IR-001 to IR-005, FR-001, NFR-013 |
| [io.md](io.md) | `api`, `settings_store`, `results` (results file, metadata file), `session_log` | FR-006, FR-090 to FR-092, FR-102, IR-010, IR-011, NFR-008 |
| [services.md](services.md) | `events`, `hub` (cancel token, MeasurementHub), `workers` (VisionWorker, ForceMonitor), `rig`, `recorder`, `controller` | FR-003, FR-004, FR-010 to FR-013, FR-020, FR-029, FR-031, FR-040 to FR-048, FR-060 to FR-078, FR-093, FR-100, FR-101, FR-103, NFR-002 to NFR-005, NFR-009 |
| [ui.md](ui.md) | `bridge`, `main_window`, `panels` (six panels), `chart`, `dialogs` | FR-001 to FR-031 (UI side), FR-049, FR-077, NFR-001 to NFR-003, NFR-012 |
| [app.md](app.md) | `main` | FR-005, FR-006, NFR-006, NFR-010, NFR-011, NFR-013, IR-020 |

Each module section has: responsibility and requirement IDs; public API with typed signatures; behaviour; error cases; threading; test cases tagged with requirement IDs. Hardware sections add the vendor calls; `io` sections add file formats with an example; `ui` sections add a layout sketch, a widget table and the legacy front-panel element replaced.

## Structure in brief

- Layers: `core` (pure logic) → `hardware` and `io` (devices, files) → `services` (threads, workflows) → `ui` (PyQt6) → `app` (composition root). Import rules are in `AGENTS.md`.
- Threads: GUI; controller (command queue, all stage moves, synchronous saving); vision (camera, image chain); force (load cell, overload check). Each device has one owning thread; the only cross-thread device call is `Stage.stop()`.
- `services.controller` runs a `Mode` state machine and drives the pure sequences in `core.sequences`. Workers feed the latest frame and force reading into `services.hub`; the GUI receives events through `ui.bridge`.

Shared conventions (defined in [core.md](core.md), core.models §2):

- Timestamps are `time.monotonic()` seconds; wall-clock times are local `datetime`.
- Lengths in mm or px; curvature in 1/mm; force in grams (load-cell device units).
- Image coordinates: origin top-left, y downward, on the image as delivered by the camera (no rotation).
- Up = toward the stage's home = loading: dK negative, force positive.
- Mode abbreviations in UI tables: DIS, IDLE, MOV, IMG, PRE, TEST, PEND, OVL.
- UNVERIFIED marks behaviour that rests on an INFERRED item of the inventory.

Suggested implementation order: `core` → `hardware.api` and `hardware.sim` → `io` → `services` → `app.main` (headless) → `ui` → real drivers on the bench.

## Requirement coverage check

Checked by script over the test-case tables: each of the 75 active requirement IDs (FR-001 to FR-103 excluding the dropped FR-021 and FR-900 to FR-905, NFR-001 to NFR-013, IR-001 to IR-020 excluding the dropped IR-012) is tagged on at least one test case. None is missing.

Requirements covered by exactly one test case, and so most exposed if that test is weakened:

| Requirement | Test |
|---|---|
| FR-028 | `ui.md` (ui.panels) IP9 |
| FR-075 | `services.md` (services.recorder) Q2 |
| IR-005 | `hardware.md` (hardware.vimba_camera) G4 (bench) |
| IR-020 | `app.md` (app.main) X8 |
| NFR-007 | `core.md` (core.measurement) C7 |
| NFR-010 | `app.md` (app.main) X9 (bench) |
| NFR-012 | `ui.md` (ui.main_window) MW1 |

Tests marked "bench" need the real rig; everything else runs with the simulated drivers.

## Architecture change requests

**ACR-1 through ACR-9 are accepted.** The user confirmed acceptance on 2026-11-14,
together with the simplifications below. ACR-3 now uses injected discovery functions
instead of a UI hardware import. ACR-6 groups results and metadata in one writer but
does not promise an atomic transaction across files. The module specs define the
current contracts; these decisions do not resolve unrelated TBD or UNVERIFIED items.

| # | Change | Where | Why | If refused |
|---|---|---|---|---|
| ACR-1 | Connecting and disconnecting run on the controller thread as `Connect` / `Disconnect` commands. The architecture had `app.main` call `RigConnection.connect()` | `services.md` (services.controller), `services.md` (services.rig), `app.md` (app.main) | A connection attempt can block for up to 15 s and must not run on the GUI thread; the operator chooses the port before connecting (FR-001) | `app.main` needs its own worker thread for connecting |
| ACR-2 | Command set differs from the architecture's list. `SetRoi` is folded into `ApplySettings` (the ROI is a `Settings` field). `Stop` is the direct call `Controller.stop()`, not a queued command. `Tare` is split into `TareCurvature` and `TareForce`. Added: `Connect`, `Disconnect`, `LeaveImageSetup`, `SaveResult`, `DiscardResult`, `AcknowledgeOverload` | `services.md` (services.controller) | Stop must not wait in the queue; the others are needed by FR-044, FR-046, FR-077, FR-101 | Mechanical renaming |
| ACR-3 | `app.main` injects bound discovery functions into `ui.bridge`; the bridge runs them on its existing discovery thread. Only hardware discovery imports vendor libraries, lazily | `ui.md` (ui.bridge), `hardware.md` (hardware.discovery), `app.md` (app.main) | Fills selection lists while preserving UI layering, without another controller command/event pair | Direct UI hardware import; superseded |
| ACR-4 | Additional events: `SampleRecorded`, `TareChanged`, `WorkflowEnded`, `ResultSaved`, `SaveFailed`, `ResultDiscarded`, `OverloadCleared`, `CommandRejected`, `MarkerWarning` | `services.md` (services.events) | The UI requirements (tare indicators, save flow, refusal messages, marker warning) cannot be met with the nine events the architecture named | None; they are required |
| ACR-5 | `ProtectedSettings` gains `loading_stage_sign`, `force_period_s`, `zaber_baud`, `zaber_device_address`, `zaber_axis`, `futek_dll_path` | `core.md` (core.models), `io.md` (io.settings_store) | Rig-specific values that are still TBD need one home that is not hard-coded | Hard-code them in the drivers |
| ACR-6 | `TestMetadata` is defined in `core.models`; `io.results` calls `io.results`, so one `ResultWriter.write` produces results and metadata. Failure may leave final files; pending data remains available | `core.md` (core.models), `io.md` (io.results), `io.md` (io.results) | One place owns the output formats and reports failure; preserves retryable data (FR-091, FR-093) | `Recorder` calls two writers and handles partial failure itself |
| ACR-7 | Mode machine details: `TESTING → IDLE` directly when a test ends with zero samples; after `RESULT_PENDING` the controller enters `OVERLOAD` or `DISCONNECTED` if those conditions hold; an out-of-travel target on `Jog` / `MoveAbsolute` is a rejected command, not a fault | `services.md` (services.controller) | Cases the architecture diagram did not cover | |
| ACR-8 | The stage's blocking move may be interrupted by `Controller.stop()` (GUI thread calling `stage.stop()`) during `MOVING` and return moves, in addition to the force thread on overload | `services.md` (services.controller) §4.6 | NFR-003 for long absolute moves; the architecture's Risks table already anticipates it | Stop waits for the move to finish |
| ACR-9 | Development-only dependency `pytest-qt` for the GUI tests | `ui_*.md` | Needed to test signal delivery on the GUI thread | Manual GUI testing |

Accepted simplifications (2026-11-14):

- `Recorder.save()` writes synchronously on the controller thread. There is no save
  thread or save-completion notice back into the controller queue. The GUI, camera,
  force checks and direct emergency stop remain independent of file I/O.
- `App` exposes two bound discovery callables for the bridge. No discovery service,
  registry, or controller command/event pair is added.
- Failed saves retain pending data and remove remaining temporary files where possible.
  Final files already replaced are not rolled back. Retrying an existing path still
  requires overwrite confirmation; no atomic multi-file guarantee is made.
- Keep the package split, pure sequence state machines, interfaces, latest-value hub,
  and display coalescing. Do not introduce generic workflow/worker frameworks or a
  dependency-injection container. See [the review](../archive/04_architecture_review.md).

## Additions beyond the requirements

Behaviour the spec had to define because the requirements are silent. Confirm or change.

| # | Addition | Where |
|---|---|---|
| S1 | Preconditioning requires curvature to be tared (no override), because its limits are defined in dK | `services.md` (services.controller) §4.3 |
| S2 | Test and preconditioning refuse to start if not all markers are currently detected | `services.md` (services.controller) §4.3 |
| S3 | A test's first sample is taken before any stage step | `services.md` (services.controller) §4.4 |
| S4 | A cycle is one excursion to each limit; the count increases on reaching the second limit; `pc_cycles = 1` ends after the seek cycle | `core.md` (core.sequences) |
| S5 | More blobs than expected: all are reported and the set is invalid (no "take the first N") | `core.md` (core.vision) |
| S6 | With "exclude outer two", the evaluation point is chosen among the fitted markers only | `core.md` (core.measurement) |
| S7 | Overload latches until acknowledged with the force back inside the limit; all motion is refused meanwhile | `services.md` (services.workers), `ui.md` (ui.dialogs) D9 |
| S8 | Three consecutive camera timeouts (3 s) or three consecutive load-cell read errors count as device loss; loss of the load cell stops the stage | `services.md` (services.workers), `services.md` (services.workers) |
| S9 | Tracking is always off at start-up, whatever was saved | `core.md` (core.models), `io.md` (io.settings_store) |
| S10 | Changing the resolution does not clear the curvature tare; the UI warns | `ui.md` (ui.panels) |
| S11 | An unsaved result is held in memory only; it is lost if the process is killed | `services.md` (services.recorder) |
| S12 | Default `test_step_mm` 0.0477 (legacy 0.7794 × 0.0612 rounded); default `pc_cycles` 1 (legacy default 0 cannot run) | `core.md` (core.models) |
| S13 | Settings cannot be changed while a move, preconditioning or a test is running | `services.md` (services.controller), UI panels |
| S14 | Log files: 20 MB per file, 5 rollovers, 200 sessions kept | `io.md` (io.session_log) |

## Open questions

Answered by the user on 2026-11-14 unless marked open. The module files carry the answers.

| # | Question | Answer | Affects |
|---|---|---|---|
| Q1 | Zaber protocol, baud, address | Current `zaber-motion` library, ASCII protocol; 115200, address 1. Binary dropped | `hardware.md` (hardware.zaber_stage), `core.md` (core.models) |
| Q2 | `zaber-motion` call names; `stop()` during a blocked move | Names checked against 10.3.0; concurrent stop supported (bench Z4 confirms) | `hardware.md` (hardware.zaber_stage), `hardware.md` (hardware.discovery) |
| Q3 | Camera model and library | Allied Vision GigE camera, Vimba X SDK, `vmbpy`. Driver renamed `vimba_camera`; no producer path setting | `hardware.md` (hardware.vimba_camera) |
| Q4 | FUTEK DLL through pythonnet at 20 ms | Yes | `hardware.md` (hardware.futek_loadcell) |
| Q5 | Rotation direction | No rotation at all; the camera is mounted (or turned) so the sample is horizontal. FR-021 dropped | `core.md` (core.vision), `core.md` (core.models), `ui.md` (ui.panels), simulator |
| Q6 | Signs | Up = toward home = loading: dK negative (concavity down), force positive (sample pushes probe). All three signs stay −1; legacy results files agree. Simulator force sign corrected | `core.md` (core.models), `hardware.md` (hardware.sim) |
| Q7 | Force unit and limit | Grams; 25 g is right | `core.md` (core.models), `ui.md` (ui.panels) |
| Q8 | Legacy resolution for C7 | Not recoverable; operator enters it in the resolution field; C7 keeps fitting a scale factor | `core.md` (core.measurement) |
| Q9 | Exponent threshold | 1e-4 confirmed | `io.md` (io.results) |
| Q10 | Stage travel limits, max step count | **Open (TBD)** | `core.md` (core.sequences), `services.md` (services.controller) |
| Q11 | Preconditioning first direction; S4 cycle definition | Negative dK first, moving up toward home. **Cycle definition (S4) still open** | `core.md` (core.sequences) |
| Q12 | Two-phase test (D2) | Accepted | `core.md` (core.sequences) |
| Q13 | `name.meta.json`; `Config_File.xls` | JSON accepted; legacy file not written (IR-012 and `write_legacy_config` dropped) | `io.md` (io.results), `io.md` (io.results), `io.md` (io.api) |
| Q14 | Recovery file | No; save at the end only | `services.md` (services.recorder) |
| Q15 | Skip return move | No; return always happens | `services.md` (services.controller) |
| Q16 | Frame size | 1280 × 960 | `core.md` (core.vision), `services.md` (services.workers) |
| Q17 | Install prerequisites | Vimba X SDK, `vmbpy`, `zaber-motion`, .NET 4.8 for FUTEK | `app.md` (app.main) §4.4 |

Requirements decisions D1–D16 of `docs/01_requirements.md` still apply, except D2 (accepted), D7 (resolved: no legacy file) and D13 (superseded: no rotation).
