# Bench checklist

First run on the rig. Do the steps in order; stop at the first one that fails.
Run everything in the `bending` conda environment. Prerequisites: .NET Framework 4.8, Vimba X SDK, `FUTEK.Devices.dll` copied into
`src/bending_machine/`, and the camera set to a Mono pixel format in Vimba X Viewer.

## 1. Stage alone (no sample, probe clear of everything)

1. `python -m bending_machine`, press Refresh, choose the stage port, Connect.
   Connect fails as a whole while the other devices are missing, so plug them all in.
2. Press **Up** once. The carriage must move toward the home end and the position must
   decrease by the step. If it moves the other way, `STAGE_UP_SIGN` is wrong.
3. Start a long "Go to" move and press STOP: the stage halts at once.
4. `BENCH_STAGE_PORT=COM3 pytest -m bench -k stage`.

## 2. Load cell

1. Nothing on the probe: the force reads near zero and is steady.
2. Push the probe gently by hand, the way the sample will push it: the displayed force
   must go **positive**. If it goes negative, `FORCE_SIGN` is wrong.
3. Push harder, past 25 g: "Force limit exceeded" appears and motion is refused until
   the load is removed and Acknowledge overload is pressed.
4. `pytest -m bench -k loadcell`.

## 3. Camera

1. The live view shows the sample lying horizontally; the frame rate is 15 fps or more.
2. `BENCH_CAMERA_ID=<id> pytest -m bench -k camera`.

## 4. Signs with a sample (design §3)

1. Mount a sample, set thresholds and ROI, start tracking: all markers found.
2. Tare curvature and force. Jog **Up** a few steps: dK must go **negative** and force
   **positive**. If dK goes positive, `LOADING_STAGE_SIGN` is wrong.

## 5. Full test

1. Small limit first (for example −0.02 1/mm), overload limit active as always.
2. Run the test: it loads, unloads, returns to the start and offers to save.
3. Open the `.lvm` and `.meta.json`; compare the curve with a legacy result.
