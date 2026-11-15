# Bending machine

Desktop application for bending tests on thin samples. It drives a Zaber stage, reads a
FUTEK load cell and tracks markers in a camera image to record curvature against force.
It replaces the LabVIEW program in `Bending_LoadCell_sources/`.

**Status:** implemented and tested against the simulator; the real drivers have not yet been run on the rig (see `docs/bench_checklist.md`).

## Requirements

- [conda](https://docs.conda.io/) (Miniconda is enough); the environment uses Python 3.12
- Target platform for the real rig: Windows 10/11, 64-bit. Development and the simulated
  mode also work on macOS and Linux.

## Setup

```sh
conda env create -f environment.yml
conda activate bending
```

This creates the `bending` environment with Python 3.12 and installs the project and its
runtime and development dependencies with pip (from `pyproject.toml`). After pulling
changes that add dependencies, run `pip install -e .[dev]` again.

## Run

```sh
python -m bending_machine --simulate   # simulated stage, load cell and camera
python -m bending_machine              # real devices
```

| Option | Meaning |
|---|---|
| `--simulate` | Use the simulated hardware drivers; no devices or vendor drivers needed |
| `--settings-folder PATH` | Folder for `settings.json` and `logs/` |
| `--version` | Print the version and exit |

## Tests and checks

```sh
pytest -m "not bench"    # tests; all use simulated hardware
ruff check               # lint
ruff format --check      # formatting (drop --check to reformat)
mypy                     # type check
```

Tests marked `bench` need the real rig and are never run in CI.

Test code lives in `tests/unit/`, `tests/integration/`, `tests/regression/` and
`tests/ui/`. The other folders under `tests/` are legacy measurement data.

To run the checks on every commit (needs a git repository):

```sh
pre-commit install
```

## Layout

| Path | Content |
|---|---|
| `src/bending_machine/` | The application: nine flat modules (see `docs/02_design.md` §2) |
| `tests/unit`, `integration`, `regression`, `ui` | Test code |
| `docs/` | Legacy inventory, requirements, design; `docs/archive/` is history |
| `Bending_LoadCell_sources/`, `builds/`, `tests/Antonino/`, `tests/Cristian/`, `tests/Capture*.PNG`, `packages*`, `README.txt` | Legacy LabVIEW program and its data; read-only |

## Documentation

- `docs/01_requirements.md`: what the application must do
- `docs/02_design.md`: how it is built; the source of truth for behaviour
- `docs/00_legacy_inventory.md`: what the LabVIEW program does

Coding agents: see `AGENTS.md`.
