# AGENTS.md

## Purpose

This application runs bending tests on thin samples: it moves a Zaber stage, reads a FUTEK
load cell and tracks markers in a camera image to record curvature against force. It is a
Python 3.12 / PyQt6 replacement for the legacy LabVIEW program kept in this repository.

## Documentation

| File | Content |
|---|---|
| `docs/02_design.md` | The design: structure, threads, device calls, file formats, workflows, GUI. **Source of truth** |
| `docs/01_requirements.md` | Requirements (FR, NFR, IR identifiers) |
| `docs/00_legacy_inventory.md` | What the LabVIEW program does |
| `docs/archive/` | Earlier architecture and detailed spec. History only; the design wins where they differ |

Read the relevant section of `docs/02_design.md` before changing a module. The older
documents call the package `bendtest` and the flag `--sim`; here they are
`bending_machine` and `--simulate`.

## Code style: keep it small

- Aim for the least code that does the job and reads clearly. Prefer one clear function
  over several small helpers; add a function or class only when it is used in more than
  one place or makes a real algorithm easier to follow.
- No speculative frameworks, registries, event buses, injection containers, or unused
  options. Constructor-supplied devices and the existing device interfaces are fine.
- Keep the nine-module layout; split a module only for a concrete responsibility.
- Comments only where the reason is not obvious from the code.
- The design leaves small decisions to you. If you simplify or deviate from it, change
  the design section in the same commit. Ask first only when the change alters what the
  operator sees, what is saved to disk, or how motion and overload protection behave.

## Structure rules

- Only `gui.py` and `main.py` import PyQt6 or pyqtgraph.
- `models.py`, `vision.py` and `sequences.py` are pure: no threads, clocks, files, devices.
- Vendor libraries (`zaber_motion`, `clr`/pythonnet, `vmbpy`) are imported inside
  `connect` and discovery, never at module import, so simulated mode works without them.
- Each device is used by one thread. The single exception is `Stage.stop()`.
- The GUI never blocks: it enqueues commands on `Machine` and polls its status.

## Legacy material is read-only

Never modify, move, rename or delete legacy material. It is the reference for behaviour
and file formats, and the regression tests read it.

- `Bending_LoadCell_sources/`, `builds/`, `packages.txt`, `packages*.PNG`, `README.txt`
- `tests/Antonino/`, `tests/Cristian/`, `tests/Capture*.PNG` (legacy measurement data)

New test code goes only in `tests/unit/`, `tests/integration/`, `tests/regression/` and
`tests/ui/`.

## Commands

```sh
conda env create -f environment.yml    # once; then: conda activate bending
pytest -m "not bench"                  # tests (simulated hardware)
ruff check                             # lint
ruff format                            # format
mypy                                   # type check (strict)
python -m bending_machine --simulate   # run with simulated hardware
```

Everything runs inside the `bending` conda environment (install packages with pip there,
not conda). Run all four checks before reporting work as done. Tests marked `bench` need the real rig;
do not run them and do not remove the marker.

## Conventions

- Unimplemented functions raise `NotImplementedError`; replace the body.
- Tests: one file per source module in `tests/unit/`; workflows against the simulator in
  `tests/integration/` (the list in `docs/02_design.md` §12 must be covered). Test what an
  operator or a saved file would notice, and keep tests short.
- Units: mm, px, 1/mm, grams. Timestamps are `time.monotonic()` seconds.
