# Legacy inventory: Bending Load Cell (LabVIEW 2020)

This document describes the application as it is. It proposes no changes and no Python design.

## Context

The application runs a bending test on a thin sample. A Zaber A-Series motorised stage (serial/VISA) bends the sample, a camera (NI IMAQdx, default name `cam1`) tracks dark markers on it to compute curvature, and a FUTEK USB225 load cell (.NET assembly `FUTEK.Devices.dll` 1.11.5.0) reads force. The top-level VI is `Flex Main Program - GM.vi`. It is built into `builds/Bending_Project/Main Program/Application.exe`; a second build, `NO_rotate/run.exe`, exists without source in this folder.

## How this inventory was produced

The brief assumed `legacy/parsed/` (pylabview output), `legacy/screenshots/` and `legacy/data/`. None of these exist in this workspace. The substitutes used are:

| Brief input | What was used instead |
|---|---|
| `legacy/parsed/` | The eight `.vi` files decoded directly from binary (RSRC container, `BDHb` block-diagram heap, `FPHb` front-panel heap, `VCTP` type strings, `LIbd` link lists) with a purpose-written parser. pylabview was not installed or run; only its tag-name tables were read as reference. |
| `legacy/screenshots/` | `tests/Capture3.PNG`, `Capture4.PNG`, `Capture5.PNG` (front panel while running). There are no block-diagram screenshots. |
| `legacy/data/` | `tests/**/*.lvm`, `tests/**/Config_File.xls`, `Bending_LoadCell_sources/Software/camera_settings.xml`. |

Meaning of the tags used throughout:

- **CONFIRMED**: read directly from the decoded file (node, wire, constant, label or string).
- **INFERRED**: interpretation. The main source of inference is that LabVIEW primitives (add, compare, wait, and so on) are stored only as numeric IDs. Their identity was deduced from wiring, data types and context, and calibrated on the smallest VIs.

Source abbreviations: **MAIN** = `Bending_LoadCell_sources/Software/Flex Main Program - GM.vi`; **KF** = `Software/Library/Imaging -- Find K and F.vi`; **FM** = `Software/Library/Imaging -- Find Marker.vi`; **CO** = `Software/Library/IMAQ Count Objects 2 cEE.vi`; **LRS** = `Software/Library/LoadReadSafe.vi`; **TF** = `Software/test_futek.vi`; **LVPROJ** = `Bending_LoadCell_sources/Bending_Project.lvproj`.

## 1. VI hierarchy

### 1.1 Project VIs (source present, all decoded)

| VI | Class | Purpose | Status |
|---|---|---|---|
| `Flex Main Program - GM.vi` | UI + logic | Top-level: front panel, state machine, all sequencing | CONFIRMED (MAIN, LVPROJ) |
| `Library/Imaging -- Find Marker.vi` | logic | Detect N markers, return X and Y arrays sorted left to right | CONFIRMED (FM) |
| `Library/IMAQ Count Objects 2 cEE.vi` | logic | Threshold-and-blob detection inside a search rectangle; customised copy of NI's `IMAQ Count Objects 2` | CONFIRMED structure (CO); that it is a modified NI VI is INFERRED from name and the NI step comments in the diagram |
| `Library/Imaging -- Find K and F.vi` | logic | Parabola fit and curvature calculation. Computes no force, despite the name | CONFIRMED (KF) |
| `Library/LoadReadSafe.vi` | hardware driver | Read FUTEK channel, apply sign and tare, flag over-limit | CONFIRMED (LRS) |
| `test_futek.vi` | utility | Standalone load-cell read loop; not called by MAIN | CONFIRMED (TF, LVPROJ) |
| `tests/Cristian/Esempio_cella di carico/Esempio algoritmo cella di carico.vi` | utility | Prototype: serial read at 115200 baud, 10-point running mean, tare. Separate project, not called by MAIN | CONFIRMED |
| `tests/Cristian/Esempio_cella di carico/Esempio grafico.vi` | utility | Prototype: XY-graph demo plotting i against i². Not called by MAIN | CONFIRMED wiring; "i²" INFERRED |

### 1.2 Call tree from the top-level VI

All entries CONFIRMED from the subVI nodes in MAIN unless marked. External VIs were not decoded (source not in this folder).

```
Flex Main Program - GM.vi
├─ Init frame
│   ├─ .NET FUTEK.Devices: DeviceRepository(), DetectDevices(), IList.get_Item(0)
│   └─ VI method "Default Vals.Reinit All"
├─ Main frame
│   ├─ IMAQdx Open Camera.vi, IMAQdx Configure Grab.vi        (NI Vision Acquisition, driver)
│   ├─ IMAQ Create ×2 ("Live Image", "Binary Image")           (NI Vision, utility)
│   └─ state machine loop
│       ├─ Initialize:  Zaber A Series.lvlib:Initialize.vi     (driver)
│       ├─ Idle:        Move.vi (Zaber) ×2, IMAQdx Grab.vi, IMAQ Cast Image,
│       │               Calculate Frames per Second.vi, IMAQ Rotate, IMAQ Threshold,
│       │               IMAQ Inverse, Imaging -- Find Marker.vi, LoadReadSafe.vi ×2,
│       │               Imaging -- Find K and F.vi ×2
│       ├─ Image Setup: IMAQdx Grab.vi ×2, IMAQ Rotate ×2, IMAQ Select Rectangle,
│       │               IMAQ Cast Image, IMAQ Threshold, IMAQ Inverse,
│       │               IMAQ Count Objects 2 cEE.vi
│       ├─ Save Data:   Convert to Dynamic Data, Write To Measurement File (Express),
│       │               Write Delimited Spreadsheet.vi
│       ├─ Precondition: Display Message to User (Express), Read Current Position.vi (Zaber) ×3,
│       │               Move.vi ×5, same imaging/force/curvature chain as Idle
│       ├─ Run Test:    Display Message to User2 (Express), Read Current Position.vi,
│       │               Move.vi ×4, same imaging/force/curvature chain as Idle
│       └─ Close:       (no subVIs)
├─ Stop frame:    Zaber Stop.vi
└─ Cleanup frame: Zaber Close.vi, DisconnectAllDevices() (.NET), IMAQdx Close Camera.vi,
                  IMAQ Dispose ×2, Simple Error Handler.vi

Imaging -- Find Marker.vi
└─ IMAQ Count Objects 2 cEE.vi
    └─ IMAQ GetImageSize, IMAQ Convert Rectangle to ROI, IMAQ TransformROI ×2,
       IMAQ GetImageInfo ×2, IMAQ Create ×2, IMAQ Extract, IMAQ ROIToMask, IMAQ Mask,
       IMAQ Xor, IMAQ Or, IMAQ RejectBorder, IMAQ FillHole, IMAQ Particle Filter 2,
       IMAQ Particle Analysis, Overlay Results (Blob).vi, IMAQ Dispose ×2

Imaging -- Find K and F.vi
└─ NI_AALPro.lvlib:General Polynomial Fit.vi

LoadReadSafe.vi
└─ .NET DeviceUSB225.GetChannelXReading(channelNumber)
```

## 2. Front panels

### 2.1 `Flex Main Program - GM.vi`

Names, control class, defaults and numeric limits are CONFIRMED from the front-panel heap of MAIN. Direction (control or indicator) is INFERRED from how the terminal is wired; several are written through local variables as well as read. "No limit" means the stored data range is the full range of the type. Units are INFERRED from labels and the screenshots unless the label states them.

| Label (button caption) | Type | Dir. | Default | Range | Units / notes |
|---|---|---|---|---|---|
| Motor COM port | VISA resource | ctl | none | n/a | Zaber serial port |
| Units (0: microsteps) | ring U16: 0 microsteps, 1 microns, 2 millimeters, 3 inches, 4 degrees | ctl | 2 (millimeters) | 0–4 | |
| Movement Type (0: relative) | ring U16: 0 relative, 1 absolute | ctl | 0 | 0–1 | |
| Move Distance (0) | slider, DBL | ctl | 0.0612244898 | data: no limit; scale drawn 0–0.5 | per Units; panel caption reads "Motor Speed (mm/s)" (see Open questions) |
| Start Manual Stage Move (START Stage Adjust) | Boolean | ctl | False | | reset by program |
| Stop Manual Stage Move (STOP Stage Adjust) | Boolean | ctl | False | | |
| Stage Up (Up) / Stage Down (Down) | Boolean | ctl | False | | |
| Camera Name | IMAQdx session name | ctl | `cam1` | | |
| Number Objects | I32 | ctl | 6 | no limit | markers |
| Min Size / Max Size | I32 | ctl | 200 / 15000 | no limit | pixels (area) |
| Threshold | I32 | ctl | 0 | no limit | not visible in screenshots |
| Image Threshold | cluster {Lower value SGL, Upper value SGL} | ctl | 2.0 / 60.0 | sliders 0–255 | grey level |
| Invert Image (INVERT Image) | Boolean | ctl | False | | |
| Image Setup (START Image Setup) | Boolean | ctl | False | | reset by program |
| Stop Image Setup (STOP Image Setup) | Boolean | ctl | False | | |
| Track (START Object Tracking) | Boolean | ctl | False | | |
| Frame Rate | DBL | ind | | | frames/s |
| Precondition (START Precondition) | Boolean | ctl | False | | reset by program |
| PC Stop (STOP Precondition) | Boolean | ctl | False | | |
| PC K Limit (shown as "dK") | DBL | ctl | 0 | no limit | 1/mm |
| PC Cycle Limit (shown as "Cycle Set") | DBL | ctl | 0 | no limit | cycles |
| PC Cycles (shown as "Cycle Count") | DBL | ind | 0 | | cycles |
| Set K Ref (TARE Curvature) | Boolean | ctl | False | | |
| Set Force Ref (TARE Force) | Boolean | ctl | False | | |
| K is set / F is set | Boolean | ind | False | | hidden in screenshots |
| dK | DBL | ind | | | 1/mm |
| Force | DBL | ind | | | g (chart axis "Force, g") |
| Resolution (mm/pixel) (shown as "Camera Res. mm/pixel") | DBL | ctl | 0.0111 | no limit | mm/pixel |
| K Limit (shown as "Curvature Limit") | DBL | ctl | −0.12 | no limit | 1/mm |
| Run Test (START Test) | Boolean | ctl | False | | reset by program |
| Stop Test (STOP Test) | Boolean | ctl | False | | |
| stop (END PROGRAM) | Boolean | ctl | False | | |
| Waveform Chart | chart, 2 plots: Force, Curvature | ind | | Y axes "Force, g" and "Curvature, 1/mm" | |
| Original Image (Live View) | IMAQ image display | ind | | | |
| Binary Image (Threshold and Marker Tracking View) | IMAQ image display | ind | | | |
| Output Array | 2D array DBL | ind | empty | | hidden |
| date string / time string | string | ind | | | hidden |
| Home, Precond Up Limit, Precond Down Limit | numeric | ind | | | stage position; hidden |
| Test Up, Precond Up, Go Back | Boolean | internal flags | False | | hidden; used as program state |
| Threshold Finished, Selection Finished | Boolean | ind | False | | hidden; only ever written False |

The panel also carries a block of user instructions (CONFIRMED text in MAIN) that mentions a "bar constant" and "bending marker"; no such controls exist on this panel.

### 2.2 SubVI panels (connector interfaces)

| VI | Inputs | Outputs | Status |
|---|---|---|---|
| Imaging -- Find Marker | Image In; Settings (cluster, §3); Search Rect; Number Objects I32; error in | Image Out; Sample X Coords [SGL]; Sample Y Coords [SGL]; error out | CONFIRMED (FM) |
| Imaging -- Find K and F | Sample X Coords [SGL]; Sample Y Coords [SGL]; Number Objects I32; resolution DBL; input cluster {K init, F init} | dK DBL; Best Polynomial Fit [DBL]; Polynomial Coefficients [DBL] | CONFIRMED (KF) |
| LoadReadSafe | reference (DeviceUSB225); input cluster {K init, F init}; error in | GetChannelXReading DBL; Limit exceeded Bool; reference out; error out | CONFIRMED (LRS) |
| IMAQ Count Objects 2 cEE | Image; Settings; Search Rect; Coordinate System; error in | Image Out; Objects [cluster]; Number of Objects I32; Coordinate System (duplicate); Elapsed time (ms); error out | CONFIRMED (CO) |
| test_futek | STOP | Reading DBL | CONFIRMED (TF) |
| Esempio algoritmo cella di carico | VISA resource name; Fai Tara; stop | Peso Netto DBL | CONFIRMED |
| Esempio grafico | Start; stop; Esci dal programma | XY Graph | CONFIRMED |

## 3. Data types

No project-owned `.ctl` files exist (CONFIRMED: none in the folder or LVPROJ). The types below are defined inline or come from NI libraries.

| Type | Fields | Where | Status |
|---|---|---|---|
| State enum (U16) | 0 Initialize, 1 Idle, 2 Image Setup, 3 Save Data, 4 Precondition, 5 Run Test, 6 Close | MAIN, type strings and case range table | CONFIRMED names and count; pairing of name to number INFERRED from order and from what each case contains |
| input cluster | K init (DBL), F init (DBL), in that order | MAIN, KF, LRS | CONFIRMED |
| Image Threshold / Range | Lower value (SGL), Upper value (SGL) | MAIN | CONFIRMED |
| Settings | Objects (ring U16), Threshold (I32), Reject Border (Bool), Fill Holes (Bool), Min Object Size (Bool), Min Size (I32), Max Object Size (Bool), Max Size (I32), Show Search Area (Bool), Show Center (Bool), Show Bounding Box (Bool) | MAIN, FM, CO | CONFIRMED field names and order; numeric widths INFERRED |
| IMAQ Rectangle (Search Rect) | Left, Top, Right, Bottom (I32), Rotation (DBL) | NI typedef used in MAIN, FM, CO | CONFIRMED names |
| Object Info (array element) | Object Center {X, Y}, Bounding Box {X Left, Y Top, X Right, Y Bottom}, Area, Orientation, Aspect Ratio, Nb Holes | FM, CO | CONFIRMED names |
| Chart sample | cluster {Force DBL, dK DBL} | MAIN | CONFIRMED |
| Output Array | 2D DBL, one row per test iteration | MAIN | CONFIRMED |
| Error cluster | status, code, source | standard | CONFIRMED |
| NI_AALPro Curve Fitting Criteria | enum: Least Square, Least Absolute Residual, Bisquare | KF | CONFIRMED |

## 4. Program structure

### 4.1 Overall shape (MAIN)

- CONFIRMED: one top-level flat sequence with four frames: Init, Main, Stop, Cleanup.
- CONFIRMED: there is exactly one long-running loop at top level. There are no parallel loops, queues, notifiers, event structures, functional globals or global variables anywhere in the project VIs (none of these node classes occur in any decoded diagram).
- CONFIRMED: data is shared between nested loops through local variables of front-panel controls (77 local-variable nodes in MAIN). The hidden Booleans `Test Up`, `Precond Up` and `Go Back` are used as program state.
- CONFIRMED: polling design. Buttons are read by polling; nested while loops block the outer state machine while they run.

### 4.2 Main state machine (MAIN)

A while loop containing a 7-case structure. Shift registers on the loop (all CONFIRMED):

| Shift register | Initial value |
|---|---|
| state (U16 enum) | 0 (Initialize) |
| error cluster | from camera/image setup |
| IMAQdx session | from `IMAQdx Configure Grab` |
| Live Image, Binary Image | from `IMAQ Create` |
| Zaber VISA resource | `Motor COM port` |
| Settings cluster | constant |
| input cluster {K init, F init} | zeros |
| dK (DBL) | 0 |
| Output Array (2D DBL) | empty |
| Image Threshold cluster | panel value |
| U32 tick value | written in Initialize; not consumed elsewhere (INFERRED: millisecond tick count) |
| Search Rect | set in Image Setup |

Loop stop condition (CONFIRMED wiring, OR-type compound node; "OR" INFERRED): error status, the stop flag emitted by the Close case, or `Limit exceeded` from the force read.

Transitions:

| From | To | Condition | Status |
|---|---|---|---|
| Initialize | Idle | always | CONFIRMED constant 1 |
| Idle | Image Setup / Precondition / Close / Run Test / Idle | first True in [Image Setup, Precondition, stop, Run Test] selects from constant table [1, 2, 4, 6, 5] (index 0 = none pressed) | CONFIRMED array and table; "search for True, add 1" INFERRED |
| Image Setup | Idle | when `Stop Image Setup` pressed | CONFIRMED |
| Precondition | Idle | when its loop ends | CONFIRMED |
| Run Test | Save Data | user answers "Yes" to "Save results?" | CONFIRMED constants 3 and 1; which button maps to which INFERRED |
| Run Test | Idle | user answers "No", or tracking is off | CONFIRMED |
| Save Data | Idle | always | CONFIRMED |
| Close | (loop exits) | emits state 6 and stop = True | CONFIRMED |

### 4.3 What each state does (MAIN)

**Initialize.** Calls Zaber `Initialize.vi` with the COM port. Writes False to `Image Setup`, `Precondition`, `Run Test`, `Selection Finished`, `Threshold Finished`. CONFIRMED.

**Idle.** Each iteration, in dataflow order:

1. Wait 50 ms (CONFIRMED constant; "Wait (ms)" INFERRED).
2. If `Start Manual Stage Move`: run an inner loop until `Stop Manual Stage Move`. Inside it, `Stage Up` calls `Move.vi` with the negated `Move Distance`, and `Stage Down` calls it with `Move Distance`; both pass `Units`, `Movement Type` and Microstep Size 0.05. Afterwards `Start Manual Stage Move` is set False. CONFIRMED wiring; "negate" INFERRED.
3. Image chain (§4.4) producing marker coordinates.
4. If `Set Force Ref`: call `LoadReadSafe` with a zero input cluster, store the result in `F init`, set `F is set`. CONFIRMED.
5. If `Set K Ref`: call `Find K and F` with a zero input cluster, store the result in `K init`, set `K is set`. CONFIRMED.
6. Call `LoadReadSafe` with the input cluster; write `Force`. CONFIRMED.
7. If `K is set`: call `Find K and F`; write `dK`. Otherwise dK keeps its previous value. CONFIRMED.
8. Append {Force, dK} to the chart. CONFIRMED.

**Image Setup.**

1. Grab, rotate 90°, call `IMAQ Select Rectangle` with prompt "Select a ROI". Set `Track` and `Stop Image Setup` False. CONFIRMED. That the returned rectangle becomes the Search Rect shift register is INFERRED (tunnel wiring was filtered out of the read).
2. Loop until `Stop Image Setup`: grab, cast, rotate, threshold with `Image Threshold`, optional `IMAQ Inverse` if `Invert Image`, rebuild Settings from `Threshold`, `Min Size`, `Max Size`; if `Track`, run `IMAQ Count Objects 2 cEE` and show the result in `Binary Image`. CONFIRMED.
3. Set `Image Setup` False. CONFIRMED.

**Precondition.** If `Track` is False, show the message "The markers are not being tracked. To precondition the sample, ensure that the markers are being tracked and the T…" and return. Otherwise:

1. Set `Precond Up` True, `Precondition` False, `PC Cycles` 0; clear chart history; wait 400 ms; call `Read Current Position.vi` (value goes to `Home`, INFERRED). CONFIRMED otherwise.
2. Loop. Each iteration runs the image chain, force read and curvature, updates the chart, then branches on a comparison of `PC Cycles` with constant 0:
   - **First cycle (curvature-controlled).** Depending on `Precond Up`, compare dK with ±`PC K Limit`. While the limit is not reached, call `Move.vi` relative (type 0, units 2) by ±`Move Distance`. When reached: wait 400 ms, call `Read Current Position.vi` and store it in `Precond Up Limit` or `Precond Down Limit`, flip `Precond Up`; on one of the two branches also increment `PC Cycles`.
   - **Later cycles (position-controlled).** Wait 3000 ms, then `Move.vi` absolute (type 1, units 2) to `Precond Up Limit` or `Precond Down Limit` alternately, flipping `Precond Up`; increment `PC Cycles` on one branch.
   - Structure, constants and variable names CONFIRMED. Which comparison is "greater" or "less", which direction is "up", and the exact cycle-count test are INFERRED.
3. Loop ends on `PC Stop`, error, `Limit exceeded`, or `PC Cycles` compared against `PC Cycle Limit` (after a unary operation on the limit, INFERRED "minus one"). CONFIRMED inputs.
4. After the loop: wait 400 ms, `Move.vi` absolute (INFERRED: to `Home`).

**Run Test.** If `Track` is False, show the same message (the text still says "precondition") and go to Idle. Otherwise:

1. Set `Test Up` True, `Go Back` False, `Run Test` False. Format the current time into `date string` and `time string`. Clear `Output Array` and chart history. Call `Read Current Position.vi`. CONFIRMED.
2. Loop. Each iteration: stage step (below), image chain, force, curvature, chart update, append one row to the output array, wait 400 ms. CONFIRMED.
   - Row layout is built from: Find Marker output 2, Find Marker output 1, dK, Force. CONFIRMED order of wiring; that output 2 then output 1 means X then Y is INFERRED from README.txt.
   - Stage step, branch on `Test Up`, then `Go Back`. Every move is `Move.vi` relative (type 0) by `Move Distance` multiplied by a hard-coded constant 0.7794, with or without negation. CONFIRMED constants and wiring; "multiply" and "negate" INFERRED.

     | Phase | Flags | Comparison (CONFIRMED operands) | While true | When false |
     |---|---|---|---|---|
     | 1 | Test Up = T | dK against `K Limit` | move −0.7794 × Move Distance | Test Up := F |
     | 2 | Test Up = F, Go Back = F | dK against `K Limit` (opposite comparator) | move +0.7794 × Move Distance | Go Back := T |
     | 3 | Test Up = F, Go Back = T | dK against constant 0 | move −0.7794 × Move Distance | Test Up := T, Go Back := F, finished := T |

     The direction of each comparison is INFERRED and the phase table should be verified against a block-diagram screenshot before relying on it (see Open questions).
3. Loop ends on `Stop Test`, finished, `Limit exceeded` or error. CONFIRMED.
4. After the loop: wait 400 ms; `Move.vi` absolute (type 1) to the position read in step 1; two-button dialog "Save results?" with buttons "Yes" and "No". CONFIRMED.

**Save Data.** See §6.

**Close.** Emits stop. After the loop: Zaber `Stop.vi`; Zaber `Close.vi`; `DisconnectAllDevices()`; close .NET references; if `Limit exceeded`, one-button dialog "Force limit exceeded"; `IMAQdx Close Camera`; `IMAQ Dispose` ×2; `Simple Error Handler`. CONFIRMED.

### 4.4 Image chain (MAIN, FM, CO)

Used identically in Idle, Precondition and Run Test.

1. `IMAQdx Grab.vi` into the live image. CONFIRMED.
2. `IMAQ Cast Image` to Image Type 0 (INFERRED: Grayscale U8). CONFIRMED constant.
3. `Calculate Frames per Second.vi` → `Frame Rate`. CONFIRMED.
4. `IMAQ Rotate`, angle 90.0°. CONFIRMED constant. Result shown in `Original Image`.
5. `IMAQ Threshold`, Range = `Image Threshold`, Replace Value 255.0. CONFIRMED.
6. If `Invert Image`: `IMAQ Inverse`. CONFIRMED.
7. Settings rebuilt every iteration: Objects = 0, Threshold = `Threshold`, Reject Border = T, Fill Holes = T, Min Object Size = T, Min Size = `Min Size`, Max Object Size = T, Max Size = `Max Size`, Show Search Area = T, Show Center = T. CONFIRMED.
8. `Imaging -- Find Marker.vi` (Settings, Search Rect, `Number Objects`). Inside (FM):
   - `IMAQ Count Objects 2 cEE.vi` returns the Objects array. CONFIRMED.
   - A for-loop over `Number Objects` takes each Object Center {X, Y}. CONFIRMED.
   - The (X, Y) pairs are sorted (INFERRED: Sort 1D Array on clusters, so ascending X) and split into `Sample X Coords` and `Sample Y Coords`. CONFIRMED wiring.
   - If fewer than `Number Objects` objects are found, behaviour is not established (see Open questions).
9. Inside CO, the diagram comments (CONFIRMED text) list: convert rectangle to ROI; extract the search region; threshold range; reject border objects; fill holes; particle filter on Min/Max size; particle analysis for shape parameters; overlay results; dispose temporary image. The internal wiring of CO was decoded but read only at the level of its subVI calls and comments.

### 4.5 Curvature (KF)

All wiring CONFIRMED; arithmetic operator identities INFERRED (they give a standard curvature formula and consistent units).

1. `General Polynomial Fit.vi`, polynomial order 2, method 0 (Least Square), on (X, Y). Coefficients c0, c1, c2; b = c1, a = c2.
2. Evaluation point x_m: X[N/2] where N = `Number Objects`. A parity branch on N mod 2 selects between X[N/2] and the mean of X[floor(N/2)] and X[ceil(N/2)]; which parity picks which is INFERRED.
3. R = (1 + (2·a·x_m + b)²)^1.5 / (2·a), in pixels.
4. K = 1 / (R × resolution).
5. dK = K − K init.

A diagram comment reads "Note that bending bar coords are removed and X and Y are sorted from left to right". No code removing points was found in KF, FM or MAIN: all N marker coordinates reach the fit (CONFIRMED wiring). A second comment gives the model as "y=a^2 x + bx+c".

### 4.6 Force (LRS)

1. `DeviceUSB225.GetChannelXReading(channelNumber = 0)` → reading. CONFIRMED.
2. x = −reading (INFERRED negate; unary numeric node CONFIRMED).
3. Output `GetChannelXReading` = x − F init. Operands CONFIRMED; "subtract" INFERRED.
4. `Limit exceeded` = (x > 25) OR (x < −25). Constant 25.0 CONFIRMED; the limit is applied before tare.

## 5. External interfaces

### 5.1 .NET: FUTEK load cell

Assembly `FUTEK.Devices`, version 1.11.5.0, shipped at `Software/DLL/x64/net48/FUTEK.Devices.dll`. The VIs store a link path of `…\Users\Alberto\Desktop\Cella di carico\test\FUTEK.Devices.dll`. All CONFIRMED (MAIN, LRS, TF).

| Call | Arguments | Returns | Used in |
|---|---|---|---|
| `new FUTEK.Devices.DeviceRepository()` | none | repository | MAIN init, TF |
| `DeviceRepository.DetectDevices()` | none | `IList<FUTEK.Devices.Device>` | MAIN init, TF |
| `IList<Device>.get_Item(index)` | index = 0 (Int32) | `Device` | MAIN init, TF |
| cast `Device` → `FUTEK.Devices.DeviceUSB225` | | | MAIN init, TF |
| `DeviceUSB225.GetChannelXReading(channelNumber)` | channelNumber = 0 (Int32) | `System.Double` | LRS, TF |
| `DeviceRepository.DisconnectAllDevices()` | none | void | MAIN cleanup, TF |

No sampling-rate, unit or calibration calls are made. `FUTEK_USB_DLL.dll`, `FTD2XX_NET` and `FTDI2XX.dll` are present only in the prototype folder under `tests/`.

### 5.2 VISA/serial: Zaber stage

Through the NI instrument driver `Zaber A Series.lvlib` (in `<instr.lib>`, not in this folder; CONFIRMED by LVPROJ). The driver's serial settings and command protocol were not available to read.

| Driver VI | Parameters used in MAIN | Status |
|---|---|---|
| `Initialize.vi` | VISA resource name = `Motor COM port`; a "Baud Rate (115200)" input exists and is left at default | CONFIRMED |
| `Move.vi` | Units (panel value in manual jog; constant 2 = millimeters elsewhere); Movement Type (0 relative, 1 absolute); Move Distance (DBL); Microstep Size = 0.05; "Device and Axis" left unwired | CONFIRMED |
| `Read Current Position.vi` | Microstep Size = 0.05; returns Position | CONFIRMED |
| `Stop.vi`, `Close.vi` | resource only | CONFIRMED |

### 5.3 Camera: NI IMAQdx and NI Vision

| Call | Parameters | Status |
|---|---|---|
| `IMAQdx Open Camera.vi` | Session In = `Camera Name` (default `cam1`); Camera Control Mode default | CONFIRMED |
| `IMAQdx Configure Grab.vi` | defaults | CONFIRMED |
| `IMAQdx Grab.vi` | "Wait for Next Buffer?" unwired (default Yes) | CONFIRMED |
| `IMAQ Create` ×2 | names "Live Image", "Binary Image" | CONFIRMED |

`Software/camera_settings.xml` is an NI-IMAQdx camera attribute file (GigE; frame rate 17.7765 fps; FrameStart trigger Freerun; packet size 8000). No node in MAIN loads it (CONFIRMED: no file-path constant or attribute call); it is INFERRED to be applied through NI MAX.

### 5.4 Not present

DAQmx, TCP/UDP, direct DLL calls (Call Library Function), ActiveX and shared variables: none in any project VI (CONFIRMED by absence of those node classes). A "DAQmx Task" type string appears in MAIN only as an unused terminal of the Write To Measurement File Express VI.

## 6. Persistence

### 6.1 Inputs

No configuration file is read. All settings are front-panel defaults saved in the VI (CONFIRMED: no file-read node in any project VI).

### 6.2 Results file (`*.lvm`)

Written in Save Data by the Express VI `Write To Measurement File`, fed with the transposed Output Array (CONFIRMED wiring; "transpose" INFERRED). The Express VI's configuration (prompting, headers, delimiter) is stored in a form that was not decoded; the format below is CONFIRMED from the sample files instead.

- Plain ASCII, CRLF line endings, no header.
- One line per test iteration.
- Each line starts with a tab, then tab-separated decimal numbers with `.` as decimal separator. Parsers that split on tab get an empty first field.
- Columns, per README.txt: `X1…XN, Y1…YN, K, F`, where N = `Number Objects`. With N = 6 this is 14 values per line (CONFIRMED in `tests/Cristian/5.Testing load cell/Test 16-07/PCL 100%/1/Test 1.lvm`).
- X and Y are marker centres in pixels of the rotated image, sorted left to right; K is dK in 1/mm; F is tared force. Values print with six decimals, with exponent notation for very small numbers (for example `6.283017E-5`).
- Older files have a different width (16 values per line in `tests/Cristian/5.Testing procedure/Test-1 (21-04-26).lvm`), from an earlier program version. Calibration files such as `tests/Cristian/4.Beam Calibration/Calibrazione (21-04).lvm` have a different, labelled layout and are not produced by this program version (INFERRED).

### 6.3 `Config_File.xls`

Written by `Write Delimited Spreadsheet.vi` into the same folder as the results file (path of the `.lvm` with the file name replaced; CONFIRMED constant "Config_File.xls"). Despite the extension it is plain text with CRLF line endings. It is overwritten for each save in the same folder (INFERRED: append input unwired).

Four lines (CONFIRMED from wiring and from `tests/Cristian/5.Testing load cell/Test 16-07/PCL 100%/2/Config_File.xls`):

```
7/16/2026        date string, captured at test start
4:28 PM          time string, captured at test start
6                Number Objects
0                Resolution (mm/pixel), see note
```

Note: the resolution is converted with a number-to-string node that produces an integer, so 0.0111 is saved as `0` (CONFIRMED in sample files; cause INFERRED). The format string `%.15f` is wired to the spreadsheet writer but the values are already strings.

### 6.4 Other files

`tests/Cristian/Dati input/*.txt` ("Bar constant", "Risoluzione mm/pixel") are hand-written notes, not read or written by the program (INFERRED).

## 7. Timing

| Item | Value | Source | Status |
|---|---|---|---|
| Idle loop wait | 50 ms per iteration, plus image and force time | MAIN | CONFIRMED constant |
| Run Test loop wait | 400 ms after each measurement | MAIN | CONFIRMED |
| Precondition, first cycle | 400 ms wait at each limit | MAIN | CONFIRMED |
| Precondition, later cycles | 3000 ms wait before each absolute move | MAIN | CONFIRMED |
| Waits before start and before return-to-home | 400 ms | MAIN | CONFIRMED |
| Image Setup loop, manual-jog loop | no explicit wait found | MAIN | CONFIRMED absence in decoded nodes |
| Camera frame rate | 17.78 fps in `camera_settings.xml`; observed rate shown in `Frame Rate` | settings file | CONFIRMED value; that it is the active setting INFERRED |
| Camera grab timeout | 5000 ms in `camera_settings.xml` | settings file | CONFIRMED value |
| Load cell | one synchronous read per loop iteration; no rate configured | LRS | CONFIRMED |
| Stage moves | `Move.vi` is called synchronously inside the loop; whether it waits for motion to finish is unknown | MAIN | see Open questions |
| `test_futek.vi` | free-running loop, no wait | TF | CONFIRMED |
| Prototype serial VI | 100 ms loop wait, 50-byte reads, 115200 baud | prototype | CONFIRMED |

## Coverage check

All eight `.vi` files in the workspace appear in §1.1: the six listed in or under `Bending_LoadCell_sources/Software/` and the two prototypes under `tests/Cristian/Esempio_cella di carico/`. There are no `.ctl` files. External library VIs appear in §1.2 by name only.

## Open questions

1. **Comparison directions in Run Test and Precondition.** The phase logic depends on which primitive is "greater" and which is "less", and on which input is dK. A block-diagram screenshot of the Run Test case (inner case structures on `Test Up` and `Go Back`) and of the Precondition case (case structures on `Precond Up`) would resolve it. None exists yet.
2. **The 0.7794 factor** applied to `Move Distance` in Run Test: its meaning is not documented anywhere in the files.
3. **"Move Distance" versus "Motor Speed (mm/s)".** The control is wired to `Move.vi`'s Move Distance input, but the panel caption next to it reads "Motor Speed (mm/s)" and test folders are named by velocity (`Velo_0.25`). Is the step size being used as a speed proxy?
4. **Does Zaber `Move.vi` block until the move completes?** This determines the real loop period. The driver source is needed.
5. **Fewer markers than `Number Objects`.** Behaviour of Find Marker and the fit when detection returns fewer objects is not established.
6. **"Bending bar coords are removed"** comment versus wiring that fits all N markers: which is intended?
7. **Force units and the ±25 limit.** The chart says grams; the DLL call returns whatever the device is configured for. The limit is hard-coded and applied before tare.
8. **Resolution saved as 0** in `Config_File.xls`: known defect or intended?
9. **`Threshold` (I32) control** feeds the Settings cluster while the image is already binarised by `Image Threshold`. Its effective role inside `IMAQ Count Objects 2 cEE` was not traced.
10. **`NO_rotate` build**: no matching source. Presumably the same program without the 90° rotation.
11. **Write To Measurement File configuration** (file dialog behaviour, overwrite or append) is stored in undecoded Express VI data. A screenshot of its configuration dialog would resolve it.
12. **`camera_settings.xml`**: how and when it is applied to the camera.

## Assumptions

- Primitive identities (add, subtract, multiply, divide, negate, reciprocal, square, power, increment, comparisons, OR, Wait (ms), Search 1D Array, Sort 1D Array, Transpose, Build/Strip Path, Format Date/Time, dialogs) were assigned from wiring and data types, not from names. They are internally consistent across VIs but were not checked in LabVIEW.
- For two-input numeric primitives, the upper terminal is the first operand (x in x − y, x / y), as calibrated on `LoadReadSafe.vi`.
- State numbers map to the enum names in the order the names are stored.
- Control or indicator direction was taken from wiring, not from the front-panel object flags.
- Sample data under `tests/` from June–July 2026 was produced by the program version in `Bending_LoadCell_sources/` (the main VI was last saved 7 July).
- NI and Zaber library VIs behave as documented by their vendors; none were decoded.
