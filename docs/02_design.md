# Design

The whole design of the bending-test application. It replaces the detailed specification
now in `docs/archive/03_spec/`, which is history: consult it for a detail this document
leaves open, but where the two differ, this document wins. Requirement IDs (FR, NFR, IR)
refer to `docs/01_requirements.md`.

## 1. What the program does

It moves a Zaber stage in small steps, reads a FUTEK load cell and tracks dark markers in
a camera image, and records the sample's curvature change (dK, 1/mm) against force (g).
The operator connects the devices, sets up marker detection, tares curvature and force,
optionally preconditions the sample, runs a test and saves the result.

## 2. Structure

One flat package, `src/bending_machine/`:

| File | Content | State |
|---|---|---|
| `models.py` | Settings, rig constants, value types, error types | done |
| `vision.py` | Marker detection, curvature, force arithmetic | done |
| `sequences.py` | Test-run and preconditioning state machines (pure) | done |
| `devices.py` | `Stage`, `LoadCell`, `Camera` interfaces; Zaber, FUTEK, Vimba drivers; discovery | interfaces done |
| `sim.py` | Simulated rig and its three drivers | stub |
| `storage.py` | Settings file, results and metadata files, session log | stub |
| `machine.py` | `Machine`: controller thread, camera thread, force thread, `Status` | stub |
| `gui.py` | PyQt6 window, panels, chart, dialogs | stub |
| `main.py` | Arguments, device choice, start-up and shutdown | placeholder window |

Rules:

- Only `gui.py` and `main.py` import PyQt6 or pyqtgraph.
- `models.py`, `vision.py` and `sequences.py` are pure: no threads, clocks, files or devices.
- Vendor libraries (`zaber_motion`, `clr`, `vmbpy`) are imported inside `connect` and
  discovery, so simulated mode runs without them.
- Each device is used by one thread. The single exception is `Stage.stop()`, which any
  thread may call while the controller thread is blocked in a move.

Threads:

| Thread | Does |
|---|---|
| GUI | Widgets. Calls `Machine` command methods (they only enqueue) and polls `status()`, `images()`, `messages()` on a 15 Hz `QTimer`. No events, signals or callbacks cross threads |
| controller | Takes commands from one `queue.Queue`; all stage moves; workflows; saving |
| camera | Grab newest frame → `vision` → store latest images, markers and `Measurement` |
| force | Read the load cell every 20 ms; store the reading; trip the overload |

The camera and force threads are separate from the controller so that the live view and
the overload check keep running while a stage move blocks (NFR-002, NFR-004).

## 3. Conventions and constants

- Units: mm, px, 1/mm, grams. Timestamps are `time.perf_counter()` seconds. It is
  monotonic too; `time.monotonic()` ticks only every 15.6 ms on Windows with Python 3.12,
  coarser than the 20 ms force period. Wall-clock times are naive local `datetime`.
- Image coordinates: origin top-left, x right, y down, on the frame as the camera delivers
  it. There is no rotation; the camera is mounted with the sample horizontal.
- Directions: **up** moves the sample toward the stage's home (Zaber position 0, negative
  `delta_mm`). Moving up loads the sample: dK and the displayed force both become positive
  (the sample pushes the probe). The parabola fitted in the image is negative when loaded
  (y points down), so curvature = `CURVATURE_SIGN ×` the fit. The legacy program did not
  negate it: its results files have negative dK with positive force, the K column of ours
  has the opposite sign.
- Rig constants live in `models.py` (`OVERLOAD_LIMIT_G = 25.0`, `FORCE_SIGN`,
  `STAGE_UP_SIGN`, `LOADING_STAGE_SIGN`, `CURVATURE_SIGN` all −1, `FORCE_PERIOD_S = 0.02`, Zaber baud 115200,
  address 1, axis 1, `FUTEK_DLL`). There is no `rig.json`; changing a constant is a code
  change, which is the "deliberate step" FR-102 asks for.
- Operator settings are the `Settings` model in `models.py` (fields, defaults and ranges
  are in the code). `tracking` is always off at start-up.

## 4. Vision and measurement (`vision.py`, done)

`prepare` (format check) → `binarise(lower, upper, invert)` → `find_markers(roi, min_area,
max_area, expected)`: 8-connected blobs inside the ROI, blobs touching the ROI border
rejected, holes filled, area filter, centres sorted by x. More or fewer blobs than
expected gives an invalid `MarkerSet`.

`compute_curvature(xs, ys, resolution, fit_subset)`: least-squares parabola through the
marker centres, K = y″ / (1 + y′²)^1.5 at the evaluation point, divided by the resolution.
Checked against the legacy results files by `tests/regression/test_curvature_legacy.py`.
It returns the fit with the legacy sign; `Machine` multiplies it by `CURVATURE_SIGN`.

Force: displayed = `FORCE_SIGN × raw − f_ref`; overload is `abs(raw) > 25` on the untared
reading. Exactly ±25 is allowed. NaN is a load-cell read error, not an overload.

## 5. Sequences (`sequences.py`, done)

Pure state machines; `next(measurement)` returns `Step(delta_mm)`, `MoveTo(target_mm)`,
`Hold()` or `Finish(reason)`.

- **Test run**: step toward the limit until dK reaches `k_limit` (loading), then step
  back until dK returns to zero (unloading), then finish. Unusable measurement → `Hold`.
- **Preconditioning**: step up until dK ≥ +amplitude and record position A; step down
  until dK ≤ −amplitude and record position B (cycle 1 done); then alternate
  `MoveTo(A)`, `MoveTo(B)`, counting a cycle at each arrival at B, until `pc_cycles`.

## 6. Devices (`devices.py`)

Interfaces (done): `Stage` (`connect(port)`, `disconnect`, `is_connected`, `position_mm`,
blocking `move_relative` / `move_absolute`, `stop`), `LoadCell` (`connect`, `disconnect`,
`is_connected`, `read_raw`), `Camera` (`connect(camera_id)`, `disconnect`, `is_connected`,
`grab_latest(timeout_s)` returning a frame newer than the last one). Failures raise
`StageError`, `LoadCellError`, `CameraError` (`CameraTimeout`); a move ended by `stop()`
raises `MotionInterrupted`. `disconnect` and `stop` never raise.

**ZaberStage** (`zaber-motion`, ASCII protocol, checked against 10.3.0):

| Method | Calls |
|---|---|
| `connect(port)` | `Connection.open_serial_port(port, baud_rate=115200)`; `detect_devices()`; `get_device(1).get_axis(1)`; read the position once |
| `position_mm` | `axis.get_position(Units.LENGTH_MILLIMETRES)` |
| moves | `axis.move_relative / move_absolute(x, Units.LENGTH_MILLIMETRES, wait_until_idle=True)` |
| `stop` | `axis.stop(wait_until_idle=False)` |
| `disconnect` | `connection.close()` |

`MovementInterruptedException` becomes `MotionInterrupted`; every other `zaber_motion`
exception becomes `StageError`. No homing and no speed settings. A closed or failed connection
also marks the stage disconnected, which is how the machine tells a lost stage from a
refused move.

**FutekLoadCell** (USB225, `FUTEK.Devices.dll` 1.11.5.0, .NET Framework 4.8, pythonnet):

```python
from pythonnet import load; load("netfx")      # once per process, before importing clr
import clr; clr.AddReference(absolute_dll_path)
from FUTEK.Devices import DeviceRepository, DeviceUSB225
repo = DeviceRepository(); dev = repo.DetectDevices()[0]   # must be a DeviceUSB225
raw = float(dev.GetChannelXReading(0))
repo.DisconnectAllDevices()
```

No device → `LoadCellError("no FUTEK device detected")`. The driver never changes sign,
tare or units. The DLL is looked for beside the package's own files
(`src/bending_machine/FUTEK.Devices.dll`) unless `FUTEK_DLL` is an absolute path.

**VimbaCamera** (Allied Vision GigE, Vimba X SDK, `vmbpy`, checked against 1.2.3; wheels
exist for Windows and Linux only):

- `connect`: enter `vmbpy.VmbSystem.get_instance()`, `get_camera_by_id(id)`, enter the
  camera, run the stream feature `GVSPAdjustPacketSize` once, `start_streaming(handler,
  buffer_count=3)`.
- handler (vmbpy thread): if `frame.get_status() == FrameStatus.Complete`, copy
  `frame.as_numpy_ndarray()` to a 2-D uint8 array (Mono10/12/16 are shifted down to 8
  bits; other formats are a `CameraError` at connect), store it as the latest frame under
  a `threading.Condition`; always `cam.queue_frame(frame)`.
- `grab_latest`: wait on the condition for a newer frame, else `CameraTimeout`.
- `disconnect`: `stop_streaming`, leave both contexts.
- The driver never writes exposure, gain, frame rate or pixel format (IR-005).

**Discovery**: `list_stage_ports(simulated)` uses `zaber_motion.Tools.list_serial_ports()`;
`list_cameras(simulated)` uses `VmbSystem.get_all_cameras()` ids. Simulated: `["SIM"]` and
`["SIM-CAM"]`. Both return `[]` on any failure. Refresh starts one background task,
only while disconnected; it stores the lists for the GUI timer to collect. Disable
Refresh and Connect until it finishes. No discovery or thread joins run on the GUI thread.

## 7. Simulator (`sim.py`)

`SimRig` is one locked model shared by `SimStage`, `SimLoadCell`, `SimCamera`. With stage
position `p` and the defaults in `SimRigConfig`:

- curvature `k = 0.08 × (10.0 − p)` 1/mm, so moving up 1.5 mm gives +0.12;
- marker centres `y_i = 600 − (k × 0.0111 / 2) × (x_i − 650)²` for six fixed `x_i`, with
  0.05 px jitter; frame 1280 × 960, background 200, discs of radius 12 at grey 20;
- raw force `= −3.5 × k + offset + noise`, so displayed force is positive when dK is positive.

`SimStage` moves in 10 ms slices at 1 mm/s and raises `MotionInterrupted` when `stop()`
sets its event; a target outside 0–25 mm raises `StageError("target out of travel")`.
`SimCamera` has no thread and no queue: `grab_latest` waits until one frame period
(17.78 fps) after the frame it last returned, then renders the rig as it is now. The
drivers accept only the ids `"SIM"` and `"SIM-CAM"`.

Test hooks on the rig: `set_force_offset`, `hide_marker`, `fail(device)`. `fail` names one
device at a time; that driver then raises its error from every call (a move in progress
included) and reports itself disconnected, until `fail(None)` and a new `connect`. The
jitter is applied to the drawn frame only; `marker_centres()` is the noise-free truth.

## 8. Files (`storage.py`)

Folder: `%APPDATA%/bending_machine`, else `~/.bending_machine`, or `--settings-folder`.

**`settings.json`**: the `Settings` fields as JSON. Saved through a temporary file and
`os.replace`. An absent file gives default settings; an invalid one gives defaults for
the whole file and a logged warning. `tracking` is always loaded as off. The file also
carries `"loading_positive": true`; a file without it was written when loading gave
negative dK, and its `k_limit` is negated on load.

**Results `name.csv`** (IR-010): ASCII, comma-separated, CR LF after every line. A header
row, then one line per sample: time since the test started, stage position, dK, tared
force, then the marker centres. Fixed decimals, never exponent form, decimal point `.`:
time 3 (1 ms), stage 4 (0.1 µm), dK 6 (1e-6 1/mm, against noise of about 5e-5), force 4
(0.1 mg), markers 4 (1e-4 px against 0.05 px jitter; replaying them reproduces dK within
1e-7, where 3 decimals would give up to 4e-7). A value that rounds to zero is written
without a minus sign.
dK is positive while loaded, the opposite of the legacy `.lvm` files (§3), which were the
same numbers without a header, time or position, TAB-separated as `X1…XN, Y1…YN, dK, force`.

```
t_s,stage_mm,dk_per_mm,force_g,x1_px,…,x6_px,y1_px,…,y6_px
0.412,9.9523,0.000105,0.0001,219.7973,…,612.5090
```

**Metadata `name.meta.json`** (IR-011): UTF-8, two-space indent, keys in this order:
`schema` (2: CSV results, dK positive while loaded), `results_file`, then the `TestMetadata` fields in `models.py`
(`start_date` `YYYY-MM-DD`, `start_time` `HH:MM:SS`, `marker_count`,
`resolution_mm_per_px`, `k_limit`, `test_step_mm`, `settle_s`, `fit_subset`, `k_ref`,
`f_ref`, `tare_overridden`, `end_reason`, `sample_count`, `start_position_mm`,
`software_version`). The legacy `Config_File.xls` is not written.

`write_results` writes both files as `.tmp` and replaces them (results first). It refuses
an empty record and, unless `overwrite`, an existing target. Any failure is a `SaveError`;
a failed save may leave one final file, and the machine keeps the record for a retry.

**Session log**: `logs/bending_YYYYMMDD_HHMMSS.log`, one per session, the newest 50 kept.
Format `%(asctime)s.%(msecs)03d %(levelname)-5s %(threadName)-10s %(name)s: %(message)s`.
Log at INFO: connections, every command, mode and phase changes, each step with the
resulting position, end reason, save path. WARNING: rejections, marker-count pauses,
overload. ERROR: faults with traceback.

## 9. Machine (`machine.py`)

### 9.1 Interface

Command methods enqueue and return: `connect`, `disconnect`, `apply_settings`, `jog`,
`move_absolute`, `set_image_setup`, `tare_curvature`, `tare_force`, `start_precondition`,
`start_test(override_tare)`, `save_result(path, overwrite)`, `discard_result`,
`acknowledge_overload`. Immediate: `stop()`, `shutdown()`.

The GUI reads three things:

- `status()` → `Status`: mode, phase text, position, latest `Measurement`, tare state,
  cycles done, sample count, marker warning, device errors, fps. The machine replaces
  the whole object under a lock.
- `images()` → latest image, binary image and markers.
- `messages()` → `Message(kind, text)` list since the last call. Kinds `info` and
  `rejected` go to the status line; `fault`, `overload`, `not_tracking`, `not_tared`,
  `saved`, `save_failed` open a dialog.

### 9.2 Modes

`DISCONNECTED`, `IDLE`, `MOVING`, `IMAGE_SETUP`, `PRECONDITIONING`, `TESTING`,
`RESULT_PENDING`, `OVERLOAD`.

- When anything ends, the next mode is: `RESULT_PENDING` if a record with samples is
  waiting; else `DISCONNECTED` if a device is missing; else `OVERLOAD` if the overload is
  latched; else `IDLE`.
- Motion and workflow commands are accepted only in `IDLE`; save and discard only in
  `RESULT_PENDING`; acknowledge only in `OVERLOAD`; settings changes in any mode except
  `MOVING`, `PRECONDITIONING`, `TESTING`. Anything else is rejected with the message
  "Not available while <mode>." Commands arriving during a move or workflow are rejected,
  not deferred.
- `IMAGE_SETUP` exists only so nothing can move while the operator draws the ROI.

### 9.3 Camera and force threads

Camera loop: `grab_latest(1.0)` → `prepare` → `binarise` → `find_markers` (only when
`tracking`) → build a `Measurement` with curvature (if markers are valid) and
`dk = curvature − k_ref` → store, and notify the controller's condition.
Three timeouts in a row are a camera fault.

Keep force readings in `deque(maxlen=200)`. Pair each frame with the reading minimizing
`abs(reading.timestamp - frame.timestamp)` in the available history (earlier on a tie).
Copy the history under the shared lock; pair and process images outside it. With no
reading, force is NaN. `Measurement.usable` requires valid markers, dK, finite force and
timestamp skew ≤0.1 s. Missing or stale force during a workflow is a load-cell fault:
stop motion, record no sample from that frame, keep prior samples, and make no return
move. In idle the live images continue; workflow start is refused until data is fresh.

Force loop, every 20 ms: `read_raw`; reject NaN as a read error, otherwise store the
timestamped reading. Use `is_overload(raw, OVERLOAD_LIMIT_G)`; if true and not latched:
`stage.stop()`, latch, cancel the running workflow with reason `OVERLOAD`, post an
`overload` message. Three read errors in a row are a load-cell fault, which also stops
the stage. A successful read resets the consecutive-error count. The latch clears only
by `acknowledge_overload` with a finite reading, no older than 0.1 s, inside the limit.

A fault (from any thread): `stage.stop()`, cancel the workflow with reason `FAULT`, post a
`fault` message naming the device, mark the device disconnected.

A move the stage refuses while still connected (target out of travel) is not a device
fault: it posts a `rejected` message with the stage's reason, and a running workflow ends
with reason `FAULT` and no return move. The stage stays connected.

`connect` is all or nothing: it first releases everything, then connects the three
devices; if any fails, all are released and one `fault` message lists the failures. After
a fault, a new `connect` therefore brings every device back, not only the lost one.

Taring stores the current absolute curvature as `k_ref`, or
`tare_reference(raw, FORCE_SIGN)` as `f_ref`, regardless of the previous tare. Repeating
tare at constant load still displays zero. Require a fresh valid frame for curvature
tare and a finite force reading no older than 0.1 s for force tare. Disconnecting clears both.

### 9.4 Starting a workflow

Checked in order; the first failure posts a message and nothing moves:

1. tracking is on (`not_tracking`: "The markers are not being tracked. To run a test /
   precondition the sample, switch on object tracking.");
2. all markers are detected on a frame no older than 0.1 s (the check first waits up to
   0.5 s for such a frame, so a briefly busy GUI does not cause a refusal) ("Not all markers are detected
   (found X of N)." for wrong count; "Waiting for fresh measurement." for stale data);
   force is finite and paired within 0.1 s, otherwise "Waiting for fresh force reading.";
3. curvature tared; for a test also force tared (`not_tared`). `start_test(override_tare=
   True)` skips this and uses zero for a missing reference, marking the tare as overridden;
   preconditioning has no override;
4. preconditioning only: `pc_amplitude > 0`.

### 9.5 Test run

```
start = position; record = new TestRecord; chart cleared; after = perf_counter()
loop:
    m = wait for frame_time > after (timeout 2 s → camera fault)
    missing, non-finite or stale force: load-cell fault, leave loop
    action = sequence.next(m)
    if m.usable: add a sample (xs, ys, dk, force, time since start, position)
    Step(d): move_relative(d); read position; wait settle_s
    Hold:    marker warning on; wait 0.2 s
    Finish:  leave the loop
    after = perf_counter()
reason = COMPLETED, or STOPPED / OVERLOAD / FAULT if cancelled
if reason is COMPLETED or STOPPED: wait 0.4 s; move_absolute(start)
```

The first sample is taken before any step. Every wait is cancellable, so `stop()` ends
the loop within one step. Advance `after` only after the settle or marker-retry wait:
each iteration requires a new frame captured after that wait, never one from during
motion or settling. The record is kept in memory until saved or discarded.

### 9.6 Preconditioning

Same loop with `PreconditionSequence(pc_amplitude, pc_cycles, jog_step_mm, LOADING_STAGE_SIGN)`: wait 0.4 s at
the start; `Step` has no settle wait; after a limit is recorded wait 0.4 s; before each
`MoveTo` wait `pc_dwell_s`. Nothing is recorded. It ends with the same return move.

### 9.7 Stop, save, shutdown

- `stop()`: cancel the workflow (reason `STOPPED`) and call `stage.stop()` directly, so a
  long move or a return move halts at once. In `IMAGE_SETUP` it leaves image setup.
- No automatic move ever follows an overload or a fault.
- `save_result` runs `storage.write_results` on the controller thread. On success the
  record is dropped and a `saved` message posted; on `SaveError` the record stays and a
  `save_failed` message is posted. The force thread still stops the stage during a save.
- `shutdown()`: cancel, `stage.stop()`, end the three threads, disconnect the devices. No
  return move. Join a thread before releasing its devices. Never hold the shared status
  lock across device calls, file I/O, waits or joins.

## 10. GUI (`gui.py`)

```
+----------------------------------------------------------------------------------+
| Bending test   [ state pill ]  next-step hint                  [Ack] [  STOP  ]  |
+--------------------------------------------------+-------------------------------+
|                                                  | dK, 1/mm      | Force, g      |
|   Live camera image                              |  -0.0534      |  0.191        |
|   + search area (dashed) + rings on markers      | [Tare] tared  | [Tare] tared  |
|                                                  +-------------------------------+
| [Threshold view]  6 of 6 markers        16 fps   | Setup | Run                   |
+-------------------------+------------------------+  Setup: devices, marker       |
| Force & dK vs time      | Force vs dK, this test |  detection, search area,      |
|                         |                        |  scale. Run: stage, precond., |
|                         |                        |  test, save/discard result    |
+-------------------------+------------------------+-------------------------------+
| status line                                                                      |
+----------------------------------------------------------------------------------+
```

- Dark theme, one palette (`gui.py` `STYLE`), Fusion style, decimal point everywhere.
- The state pill is coloured by mode; the hint names the operator's next step
  (connect, start tracking, find all markers, tare, ready, remove load…).
- One live view; "Threshold view" switches it to the binary image. Markers are drawn as
  rings so the blob underneath stays visible.
- Readout tiles with their Tare buttons are always visible; everything else is on two
  tabs. Buttons that only make sense in one mode (Connect/Disconnect, Acknowledge
  overload, Save or discard) are shown only in that mode.
- Second chart: force against dK for the running or last test, the curve the test is for.

- One `MainWindow` class builds everything; group the widgets with small builder methods
  only where it helps reading. Fits 1366 × 768 (NFR-012).
- One 15 Hz timer: read `status()`, update labels and the enabled state of each control
  from the mode; read `images()` and redraw; drain `messages()`.
- A setting widget calls `machine.apply_settings(settings.model_copy(update=...))`; an
  invalid value restores the widget and shows the reason in the status line.
- Controls that start motion are enabled only in `IDLE`; settings are editable whenever
  nothing is moving; STOP is enabled while something runs.
- Chart: the last 20 s of force and dK, scrolling, with time in seconds since the chart was
  cleared; cleared when preconditioning or a test starts.
- Discovery runs on a short background thread started by Refresh (and once at start-up);
  the timer fills the two lists when it finishes. Refresh and Connect are disabled meanwhile.
- Dialogs are plain `QMessageBox` / `QFileDialog` calls:
  - connection failure and faults name the device;
  - `not_tared` offers "Start anyway" (override) for a test; preconditioning only informs;
  - when a test ends with samples: "Save the result?" with Save, Discard, Cancel → file
    chooser (default `Test_YYYYMMDD_HHMMSS.csv`). The file chooser itself asks before
    replacing an existing file, so the save is sent with `overwrite=True`. A failed save
    shows the reason and offers the same question again; discarding asks for confirmation;
    after Cancel the "Save or discard result…" button brings the question back;
  - overload: a "Force limit exceeded" message; the operator removes the load and presses
    the "Acknowledge overload" button, which is enabled only in `OVERLOAD`;
  - closing while an activity runs asks first, then requests stop and waits through timer
    polling. With pending data offer Save, Discard (with confirmation), or Cancel; Cancel
    keeps the window open. A save failure keeps it open with the record intact;
  - while saving, disable Save/Discard and defer close until the result arrives. Run
    shutdown on a background task after the record is resolved; keep polling and close
    only when cleanup finishes. No file I/O, blocking waits or joins on the GUI thread.

## 11. Start-up (`main.py`)

`--simulate` chooses `SimRig` and the simulated drivers, else the real drivers;
`--settings-folder PATH`; `--version`. Start the session log, load settings, create the
`Machine`, show `MainWindow`, run Qt. The window completes background shutdown before
accepting close; a `finally` calls the idempotent `machine.shutdown()` for startup failures
and abnormal event-loop exit. Target:
Windows 10/11 64-bit, Python 3.12. Prerequisites on the rig PC: .NET Framework 4.8,
Allied Vision Vimba X SDK.

## 12. Tests

One test file per source file in `tests/unit/`; workflows against the simulator in
`tests/integration/`; GUI behaviour with pytest-qt in `tests/ui/`; tests that need the
rig are marked `bench`. Test what an operator or a saved file would notice. The workflow
tests that must exist:

| Test | Checks |
|---|---|
| Full test run | loading then unloading, ends `COMPLETED`, stage back at start, result pending, every step is `test_step_mm` |
| Save | `.csv` matches the format byte for byte; metadata parses; pending cleared |
| Failed save | record kept; retry to another path works |
| Stop during a test | ends `STOPPED` within a step, returns to start, samples kept |
| Stop during a long move | stage halts at once |
| Marker hidden mid-test | no steps and no samples while hidden; continues after |
| Overload mid-test | stage stopped within 250 ms, no return move, samples kept, motion refused until acknowledged |
| Camera or stage lost mid-test | ends `FAULT`, device named, samples kept |
| Preconditioning, 3 cycles | limits found at ±amplitude, then alternates between exactly those positions, returns to start |
| Start refusals | tracking off, markers missing, not tared (and the override) |
| Wrong-mode commands | rejected during a test without disturbing it |
| Overload boundaries | both signs; exactly ±25 allowed; magnitudes above 25 trip; NaN is a read error |
| Repeated tare | tare twice at constant load, then at a changed load; each time displays zero |
| Force pairing | nearest available timestamp (earlier on ties); skew ≤100 ms; missing/stale/non-finite force records nothing and faults an active workflow |
| Fresh frames | no sample from before settle ends; marker retries wait for a new frame |
| Slow discovery | GUI timer and STOP stay responsive; Refresh/Connect disabled until completion |
| Close with pending data | Save/confirmed Discard closes; Cancel or failed save keeps data and window |
| Close during save/cleanup | no duplicate save or discard; timer keeps running; devices released only after owning threads exit |

## 13. Still open

- Stage travel limits and a maximum step count per test: none enforced.
- Whether the preconditioning cycle definition (§5) is what operators expect.
