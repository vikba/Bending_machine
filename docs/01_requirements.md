# Requirements: Bending test application (Python rewrite)

Source: `docs/00_legacy_inventory.md` (the "inventory"). Section references such as "inv. §4.3" point into it.

## Context used

The brief's four context fields (users, must keep, wanted changes, target) were left blank. The values below are working assumptions so the requirements could be written; each one is repeated under "Decisions needing confirmation".

| Field | Working assumption | Basis |
|---|---|---|
| Users | Laboratory researchers running bending tests on thin polymer samples at a single bench PC, one operator at a time, without programming knowledge | Test folders named by operator, material and thickness under `tests/` |
| Must keep | The existing rig: Zaber A-Series stage, FUTEK USB225 load cell, the current camera. The measured quantities of the results file (marker centres, dK, force); its layout changed on 2026-11-15, see D8. The operator workflow: jog, image setup, tare, precondition, run test, save | inv. §5, §6.2, §4.2 |
| Wanted changes | Fix the defects the inventory found; make hard-coded values visible; record enough metadata to reproduce a test; no other new features | inv. Open questions 2, 3, 6, 7, 8 |
| Target | Windows 10/11 64-bit on the existing lab PC. Python version not constrained by this document | FUTEK .NET Framework 4.8 assembly; camera currently reached through a Windows NI driver (inv. §5.1, §5.3) |

Terms: **dK** is curvature relative to the tared reference, in 1/mm. **Force** is the tared load-cell reading. **Marker** is a dark blob on the sample or supports that the camera tracks. **UNVERIFIED** marks a requirement that depends on something the inventory tags INFERRED.

## 1. Purpose and scope

### 1.1 Purpose

The system lets an operator run a displacement-driven bending test on a thin sample. It moves a motorised stage in small steps, measures the sample's curvature optically from tracked markers and the reaction force from a load cell, shows both live, and saves the curvature–force record of each test.

### 1.2 In scope

- Manual positioning of the stage.
- Live camera view, region-of-interest selection, marker detection tuning and tracking.
- Curvature and force measurement with tare.
- Cyclic preconditioning of the sample between curvature limits.
- The measured test run with return to the start position.
- Saving results and test metadata.
- Force-overload protection.

### 1.3 Out of scope

- Post-processing or analysis of saved results (modulus, fitting, plotting across tests).
- Camera pixel-size calibration procedures; the operator enters mm/pixel as today.
- Load-cell calibration; the device's stored calibration is used as today.
- Support for other stage, load-cell or camera families.
- Multi-axis motion, multiple cameras, multiple load-cell channels.
- Remote control, networking, multi-user access.
- Reading or converting calibration `.lvm` files in the older labelled layout (inv. §6.2).
- The two prototype VIs and `test_futek.vi` as user features (inv. §1.1).
- Reproducing the legacy screen layout pixel for pixel.

## 2. Functional requirements

Decision: **KEEP** = same observable behaviour; **IMPROVE** = same purpose, changed behaviour; **DROP** = not carried over. Source abbreviations as in the inventory (MAIN, FM, CO, KF, LRS).

### 2.1 Start-up, shutdown and navigation

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-001 | The operator can choose the stage port and the camera before the devices are opened. | MAIN | KEEP | must | With two serial ports present, selecting each in turn connects to the stage only on the correct one. |
| FR-002 | On start, the system connects to the stage, the camera and the first detected load cell, and reports each device's connection result by name. | MAIN | IMPROVE: legacy fails with a generic error dialog at exit | must | With the load cell unplugged, start-up shows a message naming the load cell and no test function can be started. |
| FR-003 | From the idle condition the operator can start exactly one of: manual jog, image setup, preconditioning, test run, exit. While one is running the others cannot be started. | MAIN | KEEP | must | During a test run, the controls that start the other activities are unavailable or have no effect. |
| FR-004 | Every running activity has a stop command that ends it and returns the system to idle. | MAIN | KEEP | must | Stop during jog, image setup, preconditioning and test run each returns to idle within the time in NFR-003. |
| FR-005 | On exit the system stops stage motion and releases the stage, load cell and camera. | MAIN | KEEP | must | After exit, another program can open all three devices without restarting the PC. |
| FR-006 | Operator settings (ports, camera, marker count, thresholds, size limits, resolution, step size, limits, cycle count) are restored at the next start. | MAIN (defaults saved in VI) | IMPROVE: legacy needs the VI re-saved to change defaults | should | Change every setting, exit, restart: all values are as left. |

### 2.2 Manual stage positioning

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-010 | The operator can move the stage up or down by a set distance per command. | MAIN | KEEP | must | With step 0.05 mm, one "up" command changes the reported position by 0.05 mm in the up direction, within stage resolution. |
| FR-011 | The jog step is entered in millimetres. | MAIN | IMPROVE: legacy offers five units and a relative/absolute selector that the automated modes ignore | must | The step field shows mm; no unit selector is needed to jog. |
| FR-012 | The operator can command an absolute move to an entered position. | MAIN (Movement Type = absolute) | KEEP | could | Entering 10.000 mm moves the stage to a reported position of 10.000 mm. |
| FR-013 | The current stage position is displayed. | MAIN (hidden `Home` only) | IMPROVE: legacy never shows position | should | Displayed position matches the stage's own reported position after each move. |

### 2.3 Imaging and marker tracking

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-020 | A live camera image is shown whenever the system is idle, in image setup, preconditioning or testing. | MAIN | KEEP | must | Moving an object in front of the camera is visible in all four conditions. |
| FR-021 | The displayed and processed image orientation can be set to rotated by 90° or not rotated. | MAIN; `NO_rotate` build | DROP (2026-11-14): no rotation; the camera is mounted with the sample horizontal | — | — |
| FR-022 | The operator can draw a rectangular region of interest on a still image; markers are searched only inside it. | MAIN, CO | KEEP. UNVERIFIED that the rectangle is what limits the search | must | A blob outside the rectangle is never reported as a marker. |
| FR-023 | The operator can set a lower and upper grey-level bound (0–255); pixels inside the bounds are treated as marker candidates. A binary view shows the result live. | MAIN | KEEP | must | Changing a bound changes the binary view within one displayed frame. |
| FR-024 | The operator can invert the binary image. | MAIN | KEEP | should | Toggling invert swaps foreground and background in the binary view. |
| FR-025 | Candidates touching the region border are rejected, holes are filled, and candidates with area outside an operator-set minimum and maximum (pixels) are rejected. | MAIN, CO | KEEP | must | With min 200 and max 15000, blobs of 100 and 20000 pixels are not reported; a 1000-pixel blob is. |
| FR-026 | The operator sets the expected number of markers N. | MAIN, FM | KEEP | must | N is editable and used by FR-027 and FR-040. |
| FR-027 | Detected markers are reported as centre coordinates in pixels, ordered left to right. | FM | KEEP. UNVERIFIED sort order | must | For a test image with known blob centres, reported coordinates match within 1 pixel and X is ascending. |
| FR-028 | The binary view marks each detected marker's centre and index and outlines the search region. | CO; screenshots | KEEP | should | Each detected marker shows an index label at its centre. |
| FR-029 | Tracking can be switched on and off by the operator; preconditioning and test run refuse to start while it is off, with a message that says which activity was refused. | MAIN | IMPROVE: legacy shows the preconditioning text for both | must | Starting a test with tracking off shows a message mentioning the test, and no stage motion occurs. |
| FR-030 | When the number of detected markers differs from N, the system shows this clearly, produces no curvature value for that frame, and does not move the stage on the basis of that frame. | FM (behaviour unknown, inv. Open question 5) | IMPROVE: legacy behaviour undefined | must | Covering one marker during a test stops stage steps and shows a marker-count warning; uncovering it resumes. |
| FR-031 | The measured camera frame rate is displayed. | MAIN | KEEP | could | Displayed rate is within 10% of the camera's configured rate. |

### 2.4 Curvature and force measurement

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-040 | Curvature is computed from the marker centres by fitting a second-order polynomial y(x) by least squares and evaluating K = y″ / (1 + y′²)^1.5 at the evaluation point, then dividing by the resolution (mm/pixel) to give 1/mm. | KF | KEEP. UNVERIFIED formula (operators inferred) | must | For synthetic markers on a circle of radius 500 px with resolution 0.01 mm/px, the absolute curvature is 0.2 1/mm within 1%. |
| FR-041 | The evaluation point is the X coordinate of the marker at index N/2 when N is even, and the mean of the two central markers' X when N is odd (indices from 0, left to right). | KF | KEEP. UNVERIFIED which parity takes which branch | must | For N = 6 the evaluation X equals the 4th marker's X. |
| FR-042 | Which markers enter the fit is defined and visible to the operator: either all N, or all except the two outermost (support) markers. | KF comment versus wiring (inv. §4.5) | IMPROVE: legacy comment and behaviour disagree; default keeps legacy behaviour (all N) | must | With the default, results for a recorded marker set equal the legacy dK within 1e-6. With the alternative selected, the outer two are excluded. |
| FR-043 | The operator enters the camera resolution in mm/pixel; it is used for FR-040. | MAIN | KEEP | must | Doubling the resolution halves displayed curvature for a static image. |
| FR-044 | The operator can tare curvature: the current curvature becomes the reference and dK reads 0 immediately afterwards. dK is shown only once a reference exists. | MAIN, KF | KEEP | must | After tare with a static sample, dK is 0 within noise. Before the first tare no dK value is shown. |
| FR-045 | Force is read from load-cell channel 0, displayed with the sign convention of the legacy program (device reading negated). | LRS | KEEP. Negation confirmed: legacy results show positive force while loading (negative dK there; dK is positive here, see open question 7) | must | A load that produced a positive value in the legacy program produces a positive value of the same magnitude. |
| FR-046 | The operator can tare force: the current force becomes the reference and the displayed force reads 0 immediately afterwards. | MAIN, LRS | KEEP | must | After tare with constant load, force is 0 within noise. |
| FR-047 | Whether curvature and force have been tared is visible. | MAIN (hidden flags) | IMPROVE: legacy hides the flags | should | Two indicators change state on the respective tare. |
| FR-048 | A test run cannot start until both curvature and force have been tared in the current session, unless the operator explicitly overrides. | MAIN (instructions text only) | IMPROVE: legacy only advises it | should | Starting a test untared prompts; declining starts nothing. |
| FR-049 | Force and dK are plotted live against time on one chart with separate axes; the chart is cleared at the start of preconditioning and of a test. | MAIN | KEEP | must | Starting a test shows an empty chart that then fills. |

### 2.5 Preconditioning

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-060 | The operator sets a curvature amplitude and a number of cycles, then starts preconditioning. | MAIN | KEEP | must | Both values are editable; start is refused per FR-029 when not tracking. |
| FR-061 | In the first cycle the stage steps by the jog step until dK reaches the amplitude in one direction, then steps the other way until dK reaches the amplitude in the opposite direction. The stage position at each extreme is recorded. | MAIN | KEEP. UNVERIFIED directions and comparisons | must | With amplitude 0.05 1/mm, the first cycle reverses when the displayed dK magnitude first reaches 0.05 on each side. |
| FR-062 | Later cycles move directly between the two recorded positions, dwelling at each. | MAIN | KEEP. UNVERIFIED | must | From cycle 2 on, the stage alternates between the two recorded positions within stage resolution. |
| FR-063 | The dwell time at each extreme is an operator setting, default 3 s. | MAIN (constant 3000 ms) | IMPROVE: hard-coded today | could | Setting 1 s gives extremes about 1 s apart plus travel time. |
| FR-064 | The completed cycle count is displayed, and preconditioning ends when it reaches the set number. | MAIN | KEEP. UNVERIFIED exact end test | must | Setting 3 cycles ends after exactly 3 full cycles, with the counter showing 3. |
| FR-065 | When preconditioning ends, for any reason other than force overload, the stage returns to the position it had at the start. | MAIN | KEEP. UNVERIFIED target position | must | End position equals start position within stage resolution, after normal completion and after operator stop. |

### 2.6 Test run

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-070 | The operator sets a curvature limit and starts the test. | MAIN | KEEP | must | Limit is editable; start is refused per FR-029 and FR-048. |
| FR-071 | The test loads the sample by stepping the stage until dK reaches the curvature limit, then unloads by stepping back until dK returns to the reference (zero), then ends. | MAIN | IMPROVE: the legacy three-phase sequence is UNVERIFIED and its intermediate phase has no evident purpose; the requirement states the evident intent | must | In the saved record, dK moves monotonically (within noise) from about 0 to the limit and back to about 0, and the test ends without operator action. |
| FR-072 | After each step the system waits a settle time, then records one sample. The settle time is an operator setting, default 400 ms. | MAIN (constant 400 ms) | IMPROVE: hard-coded today | must | With settle 400 ms, consecutive samples are at least 400 ms apart. |
| FR-073 | The step size used in the test is shown to the operator in millimetres and is the size actually commanded. | MAIN (0.7794 × Move Distance) | IMPROVE: legacy applies an undocumented factor | must | Logged commanded step equals the displayed step. |
| FR-074 | Each recorded sample contains the N marker X coordinates, the N marker Y coordinates, dK and force, all taken from the same camera frame and the force reading closest to it. | MAIN | KEEP | must | Each saved row has 2N + 2 values; replaying saved coordinates through FR-040 reproduces the saved dK (negated, less the tare reference). |
| FR-075 | The date and time of test start are recorded. | MAIN | KEEP | must | Saved start time is within 2 s of the PC clock at start. |
| FR-076 | When the test ends, for any reason other than force overload, the stage returns to the position it had at test start. | MAIN | KEEP | must | End position equals start position within stage resolution. |
| FR-077 | After the stage has returned, the operator is asked whether to save the results; declining discards them after a confirmation. | MAIN | IMPROVE: legacy discards without confirmation | must | "No" then "Confirm" leaves no file; "No" then "Cancel" returns to the save question. |
| FR-078 | A test stopped by the operator can still be saved. | MAIN | KEEP | must | Stop mid-test, answer yes: file contains the samples taken so far. |

### 2.7 Saving

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-090 | On save, the operator chooses the file name and folder; the results are written in the format of IR-010. | MAIN | KEEP | must | A saved file opens in a spreadsheet or pandas with named columns. |
| FR-091 | With each results file, the system saves the test metadata in the format of IR-011, including the true resolution value. | MAIN | IMPROVE: legacy saves resolution as 0 and overwrites one metadata file per folder | must | Two tests saved in one folder leave two results files and metadata for both; resolution 0.0111 is saved as 0.0111. |
| FR-092 | The system never silently overwrites an existing results file. | MAIN (Express VI behaviour unknown) | IMPROVE | must | Choosing an existing name prompts before replacing. |
| FR-093 | Unsaved test data survives until the operator saves or discards it, including if saving fails. | MAIN | IMPROVE | should | Saving to a read-only folder shows an error and offers save again; data is intact. |

### 2.8 Safety

| ID | Statement | Source | Decision and reason | Priority | Acceptance criterion |
|---|---|---|---|---|---|
| FR-100 | The force is checked against an overload limit on every reading. The limit applies to the untared reading. Default 25 in device units. | LRS | KEEP | must | Applying a load above the limit triggers FR-101 within the time in NFR-004. |
| FR-101 | On overload the system stops stage motion immediately, makes no further automatic move, tells the operator "Force limit exceeded", and keeps any test data recorded so far available to save. | MAIN, LRS | IMPROVE: legacy closes the whole program and loses the data | must | Overload during a test: stage stops, message appears, save is offered, the application stays open. |
| FR-102 | The overload limit is a protected setting, not editable during normal operation. | LRS (constant) | IMPROVE: hard-coded today, but must not be casually changed. Kept as a named constant in `models.py` (2026-11-14) | should | Changing it needs a deliberate step separate from the normal settings. |
| FR-103 | A stage command error or loss of any device during an automatic activity stops the activity, stops motion where possible, and reports which device failed. | MAIN | IMPROVE | must | Unplugging the camera mid-test stops stepping and names the camera. |

### 2.9 Dropped legacy behaviour

| ID | Legacy behaviour | Source | Decision and reason |
|---|---|---|---|
| FR-900 | Hidden panel flags (`Test Up`, `Precond Up`, `Go Back`, `Threshold Finished`, `Selection Finished`) | MAIN | DROP: internal program state, not user-observable |
| FR-901 | Unit choices microsteps, microns, inches, degrees | MAIN | DROP: automated modes always use mm; see FR-011 |
| FR-902 | Separate integer `Threshold` setting | MAIN | DROP, pending Open question 4: appears redundant with the grey-level bounds |
| FR-903 | Hidden on-screen "Output Array" and date/time string fields | MAIN | DROP: carriers for saving only; covered by FR-074, FR-075 |
| FR-904 | 0.7794 step factor | MAIN | DROP as a hidden factor; see FR-073 and Open question 1 |
| FR-905 | Program exit on force overload | MAIN | DROP; replaced by FR-101 |

## 3. Non-functional requirements

| ID | Statement | Basis | Priority | Acceptance criterion |
|---|---|---|---|---|
| NFR-001 | In idle, displayed force, dK, chart and live image update at least 5 times per second. | Legacy idle loop: 50 ms wait plus processing (inv. §7) | must | Measured update interval ≤ 200 ms over 1 minute. |
| NFR-002 | The live image is never more than 0.5 s old. | Legacy grabs the newest frame each iteration | must | A visible timer filmed by the camera lags by ≤ 0.5 s on screen. |
| NFR-003 | A stop command takes effect within 1 s plus the time of a stage move already in progress. The screen stays responsive during dwell and settle times. | Legacy blocks up to 3 s in dwells (inv. §7) | must | Stop pressed during a 3 s dwell ends the activity within 1 s. |
| NFR-004 | Force overload is detected and motion stopped within 250 ms of the reading crossing the limit, in every condition including dwells. | Legacy checks once per loop pass | must | Step load applied during a dwell: stage stop command issued within 250 ms. |
| NFR-005 | During a test, marker coordinates, dK and force in one sample are taken no more than 100 ms apart. | Legacy takes them in sequence in one pass | should | Timestamps of frame and force reading in the log differ by ≤ 100 ms. |
| NFR-006 | The system runs a test of at least 10 000 samples and a session of at least 8 hours without restart, growing slowness or data loss. | Bench use | should | 8-hour soak with repeated tests completes; memory use stable. |
| NFR-007 | Curvature computed from identical marker coordinates matches the legacy program within 1e-6 1/mm (with FR-042 default). | Continuity of results | must | Replay of marker columns from five legacy `.lvm` files reproduces their K column. |
| NFR-008 | Every session writes a timestamped log of connections, operator commands, state changes, stage commands, warnings and errors. | Legacy has none | should | After a test, the log shows start, each phase change, end reason and save path. |
| NFR-009 | No single device failure causes loss of already recorded test data. | FR-101, FR-103 | must | Covered by FR-101 and FR-103 tests. |
| NFR-010 | Runs on Windows 10 and 11, 64-bit, on the existing lab PC. | Working assumption | must | Full workflow passes on the lab PC. |
| NFR-011 | Installable by a non-developer on a PC without a LabVIEW development licence; any separately installed vendor drivers are listed with versions. | Legacy needs LabVIEW runtime and NI Vision runtime | must | Clean PC plus documented steps reaches a working application. |
| NFR-012 | Usable on a 1366 × 768 display without scrolling for the main workflow. | Legacy panel size in screenshots | could | All workflow controls visible at that resolution. |
| NFR-013 | The measurement and control behaviour can be exercised without hardware attached, for acceptance testing. | Needed to test FR-040, FR-071, FR-101 repeatably | should | The acceptance tests for FR-040, FR-071 and FR-101 run on a PC with no devices. |

## 4. Interface requirements

| ID | Interface | Requirement | Basis | Status |
|---|---|---|---|---|
| IR-001 | Zaber A-Series stage, serial port | Connect on an operator-selected port. Command relative and absolute moves in millimetres, read position, stop motion, release the port. One device, one axis. | inv. §5.2 | Current Zaber device; `zaber-motion` ASCII protocol at 115200 (confirmed 2026-11-14); microstep size not needed |
| IR-002 | Stage motion completion | The system knows when a commanded move has finished before taking the sample that follows it. | inv. Open question 4 | UNVERIFIED whether legacy waits |
| IR-003 | FUTEK USB225 load cell | Use the first detected device; read channel 0 as a floating-point value in the device's configured units; disconnect on exit. Works with the vendor library FUTEK.Devices 1.11.5.0 shipped with the legacy source. | inv. §5.1 | CONFIRMED calls; units grams, read rate 50 Hz confirmed |
| IR-004 | Camera | Acquire continuous 8-bit greyscale frames from the existing camera, selected by name or identifier, always delivering the most recent frame, at ≥ 15 frames/s. | inv. §5.3; `camera_settings.xml` (GigE, 17.78 fps) | Allied Vision GigE camera, Vimba X / vmbpy (confirmed 2026-11-14) |
| IR-005 | Camera configuration | Exposure, gain and frame rate are those already stored for the camera; the system does not need to edit them. | `camera_settings.xml` not loaded by legacy | UNVERIFIED how settings reach the camera |
| IR-010 | Results file | Comma-separated ASCII text, CRLF line endings, file extension `.csv`. A header row naming each column with its unit, then one line per sample: time since test start (s), stage position (mm), dK (1/mm, positive while loaded), tared force (g), X1…XN, Y1…YN (pixels of the processed image, left to right). Decimal point `.`, fixed decimals per column (time 3, stage 4, dK 6, force 4, X/Y 4), no exponent form. | D8; legacy layout in inv. §6.2 | CHANGED 2026-11-15 from the legacy headerless `.lvm` layout |
| IR-011 | Metadata file | Plain text saved beside each results file and tied to it by name. Contains at least: start date, start time, N, resolution (mm/pixel, full precision), curvature limit, step size, settle time, which markers were fitted, K and F tare references, end reason, software version. | inv. §6.3 extended | Changed from legacy; see decision D7 |
| IR-012 | Legacy metadata | A file named `Config_File.xls` with the four legacy lines (date, time, N, resolution) is also written if existing tooling reads it. | inv. §6.3 | DROPPED (2026-11-14): nothing needs it; decision D7 |
| IR-020 | Network | None. The system needs no network connection, apart from the camera link if the camera is Ethernet-attached. | inv. §5.4 | CONFIRMED absence |

## 5. Traceability

| Inventory item | Requirement IDs |
|---|---|
| `Flex Main Program - GM.vi` — start-up, cleanup | FR-001, FR-002, FR-005, FR-006 |
| — state machine, Idle | FR-003, FR-004, FR-020, FR-049, NFR-001, NFR-003 |
| — manual stage move | FR-010 to FR-013 |
| — Image Setup | FR-021 to FR-026, FR-028, FR-029 |
| — tare | FR-044, FR-046, FR-047, FR-048 |
| — Precondition | FR-060 to FR-065 |
| — Run Test | FR-070 to FR-078 |
| — Save Data | FR-090 to FR-093, IR-010, IR-011, IR-012 |
| — overload exit, error handling | FR-100 to FR-103, NFR-004, NFR-009 |
| — hidden flags, unit ring, `Threshold`, 0.7794 factor | Dropped: FR-900 to FR-905 |
| `Imaging -- Find Marker.vi` | FR-026, FR-027, FR-030 |
| `IMAQ Count Objects 2 cEE.vi` | FR-022, FR-025, FR-028 |
| `Imaging -- Find K and F.vi` | FR-040 to FR-044, NFR-007 |
| `LoadReadSafe.vi` | FR-045, FR-046, FR-100, IR-003 |
| `test_futek.vi` | Dropped as a feature: standalone bench check, not part of the application. Its purpose is covered by FR-002 and NFR-013 |
| `Esempio algoritmo cella di carico.vi` | Dropped: prototype for a different (serial) load cell, not called by the application |
| `Esempio grafico.vi` | Dropped: plotting demo, not called by the application |
| Zaber A Series driver VIs (Initialize, Move, Read Current Position, Stop, Close) | IR-001, IR-002 |
| NI IMAQdx / NI Vision VIs | IR-004, IR-005, FR-020 to FR-028 |
| FUTEK.Devices .NET calls | IR-003 |
| Write To Measurement File, Write Delimited Spreadsheet | IR-010, IR-011, IR-012 |
| `camera_settings.xml` | IR-005 |

## Decisions needing confirmation

Proposed without guidance; each can be reversed without affecting the rest.

| # | Decision proposed | Alternative | Affects |
|---|---|---|---|
| D1 | Users, must-keep list, wanted changes and target platform are as in "Context used" | Your actual context | Whole document |
| D2 | Test sequence is simplified to load-to-limit then unload-to-zero (ACCEPTED 2026-11-14) | Reproduce the legacy three-phase sequence exactly, once verified from a block-diagram screenshot | FR-071 |
| D3 | The hidden 0.7794 step factor is removed; the displayed step is the commanded step | Keep the factor, documented, if it is a geometric or calibration ratio | FR-073, comparability of step counts with old tests |
| D4 | Fit uses all N markers by default (legacy behaviour), with excluding the two support markers as an option | Make exclusion the default, as the legacy comment intended; changes K values relative to old data | FR-042, NFR-007 |
| D5 | Force overload stops motion and keeps the application and data alive | Legacy behaviour: exit | FR-101 |
| D6 | No automatic stage move after an overload, including no return to start | Back off automatically by a fixed distance | FR-065, FR-076, FR-101 |
| D7 | One metadata file per results file with extended content; legacy `Config_File.xls` not written (RESOLVED 2026-11-14) | Keep writing exactly the legacy file, with resolution fixed | FR-091, IR-011, IR-012 |
| D8 | Results are a CSV file with a header row, time and stage position (CHANGED 2026-11-15: the dK sign change already made new files differ from legacy ones) | Stay in the legacy `.lvm` layout with no header | IR-010 |
| D9 | Stage units fixed to millimetres; other units dropped | Keep the unit selector | FR-011, FR-901 |
| D10 | Integer `Threshold` setting dropped | Keep it if it is found to affect detection | FR-902 |
| D11 | Settle time, dwell time and overload limit become settings with the legacy values as defaults | Keep them fixed | FR-063, FR-072, FR-102 |
| D12 | Test start requires both tares, with operator override | Advisory only, as today | FR-048 |
| D13 | SUPERSEDED 2026-11-14: no rotation at all (FR-021 dropped) | Fixed 90° | FR-021 |
| D14 | Settings persist between sessions | Fixed defaults at every start, as today | FR-006 |
| D15 | Absolute manual move kept as "could"; position display added | Drop absolute move | FR-012, FR-013 |
| D16 | Wrong marker count pauses stepping and withholds curvature | Abort the test | FR-030 |

## Open questions

1. What is the 0.7794 factor in the test step (inv. Open question 2)? If it converts stage travel to something physical, FR-073 needs it kept and named.
2. Is "Move Distance" used as a speed proxy (inv. Open question 3)? Test folders are named by velocity. If operators think in mm/s, should the test be specified by a rate (step size together with settle time) instead of a step?
3. What are the exact legacy test and preconditioning sequences? A block-diagram screenshot of the Run Test and Precondition cases would let FR-061, FR-062, FR-064 and FR-071 be marked verified, or corrected.
4. Does the integer `Threshold` setting change detection once the image is already binarised (inv. Open question 9)?
5. Which markers should be fitted (D4), and should the evaluation point be the centre marker or the bending-bar contact point?
6. Force units: ANSWERED 2026-11-14 — grams; 25 is right.
7. Sign conventions: ANSWERED 2026-11-14 — negative dK is the loading direction in the legacy program (moving up toward home); limits stay signed. CHANGED 2026-11-15 — curvature is negated so that loading gives positive dK, like force; the K column of new results files has the opposite sign to legacy files, and the default limit is +0.12.
8. Does the stage report move completion, and what are its travel limits? Completion: yes (`zaber-motion` waits until idle). Travel limits: still open.
9. What camera model and interface is installed? ANSWERED 2026-11-14 — Allied Vision GigE camera via Vimba X / vmbpy; no NI path.
10. Does anything read `Config_File.xls` today (D7)? ANSWERED 2026-11-14 — no; it is not written.
11. Should old result files with the 16-column layout be readable by anything in the new system? Currently out of scope.
12. What should happen to the stage after an overload (D6)?
13. Is one operator-chosen marker count per session enough, or do samples with different marker counts get tested back to back?
14. Is a hardware emergency stop present on the rig, independent of software?
