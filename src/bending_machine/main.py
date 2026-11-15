"""Process entry point: parse arguments, choose real or simulated devices, run the GUI.

Design: docs/02_design.md §11.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from bending_machine import __version__, storage
from bending_machine.devices import Camera, FutekLoadCell, LoadCell, Stage, VimbaCamera, ZaberStage
from bending_machine.gui import MainWindow
from bending_machine.machine import Machine
from bending_machine.sim import SimCamera, SimLoadCell, SimRig, SimStage

PROGRAM_NAME = "bending-machine"


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description="Bending test application.")
    parser.add_argument(
        "--simulate", action="store_true", help="use simulated devices (no hardware needed)"
    )
    parser.add_argument(
        "--settings-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="folder for settings.json and logs/",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)
    if args.version:
        print(f"{PROGRAM_NAME} {__version__}")
        return 0

    folder = args.settings_folder or storage.default_folder()
    try:
        storage.start_session_log(folder)
    except OSError as error:  # no log file is no reason not to run
        logging.basicConfig(level=logging.INFO)
        logging.warning("no session log in %s: %s", folder, error)
    logging.info("session start, version %s, simulated=%s", __version__, args.simulate)

    stage: Stage
    loadcell: LoadCell
    camera: Camera
    if args.simulate:
        rig = SimRig()
        stage, loadcell, camera = SimStage(rig), SimLoadCell(rig), SimCamera(rig)
    else:
        stage, loadcell, camera = ZaberStage(), FutekLoadCell(), VimbaCamera()

    qt_app = QApplication.instance() or QApplication(sys.argv[:1])
    machine = Machine(stage, loadcell, camera, storage.load_settings(folder), folder)
    try:
        window = MainWindow(machine, args.simulate)
        window.show()
        return qt_app.exec()
    finally:
        machine.shutdown()  # idempotent; the window already did this on a normal close
