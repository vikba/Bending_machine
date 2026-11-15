"""Behaviour of the main window against the simulated rig (docs/02_design.md §10, §12)."""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from bending_machine import devices, gui
from bending_machine.gui import Button, MainWindow
from bending_machine.machine import Machine
from bending_machine.main import main
from bending_machine.models import Mode, Settings
from bending_machine.sim import SimCamera, SimLoadCell, SimRig, SimStage

FAST = {
    "tracking": True,
    "test_step_mm": 0.05,
    "settle_s": 0.05,
    "k_limit": 0.012,
    "stage_port": "SIM",
    "camera_id": "SIM-CAM",
}


class Dialogs:
    """Stands in for the modal dialogs: records each title and gives the planned answer."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.asked: list[str] = []
        self.answers: dict[str, Button] = {}  # dialog title -> button; default No / Cancel
        self.file = ""  # what the file chooser returns; "" means cancelled

        def box(_parent: object, title: str, _text: str, buttons: Any = None) -> Button:
            self.asked.append(title)
            return self.answers.get(title, Button.Cancel if buttons else Button.No)

        for name in ("question", "warning", "information"):
            monkeypatch.setattr(QMessageBox, name, box)
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_: (self.file, ""))


@pytest.fixture
def dialogs(monkeypatch: pytest.MonkeyPatch) -> Dialogs:
    return Dialogs(monkeypatch)


@pytest.fixture
def window(qtbot: QtBot, tmp_path: Path, dialogs: Dialogs) -> Iterator[MainWindow]:
    rig = SimRig()
    machine = Machine(
        SimStage(rig), SimLoadCell(rig), SimCamera(rig), Settings.model_validate(FAST), tmp_path
    )
    made = MainWindow(machine, simulated=True)
    made.rig = rig  # type: ignore[attr-defined]
    qtbot.addWidget(made)
    made.show()
    yield made
    made._timer.stop()  # qtbot's close is deferred by the window, so stop it here
    machine.shutdown()


def mode(window: MainWindow) -> Mode:
    return window._machine.status().mode


def connect(qtbot: QtBot, window: MainWindow) -> None:
    qtbot.waitUntil(window.connect_button.isEnabled)
    window.connect_button.click()
    qtbot.waitUntil(lambda: window.marker_label.text() == "6 of 6 markers", timeout=5000)


def start_test(qtbot: QtBot, window: MainWindow) -> None:
    connect(qtbot, window)
    window.tare_k_button.click()
    window.tare_f_button.click()
    qtbot.waitUntil(lambda: window.f_tare_label.text() == window.k_tare_label.text() == "tared")
    window.test_button.click()
    qtbot.waitUntil(lambda: window.mode_label.text().startswith("Test running"))


def test_connect_enables_the_controls(qtbot: QtBot, window: MainWindow) -> None:
    qtbot.waitUntil(window.connect_button.isEnabled)  # discovery found the simulated devices
    assert window.port_combo.currentText() == "SIM"
    assert window.camera_combo.currentText() == "SIM-CAM"
    assert window.mode_label.text() == "Not connected"
    assert not window.up_button.isEnabled() and not window.stop_button.isEnabled()
    assert window._spins["k_limit"].isEnabled()  # settings can be prepared beforehand

    connect(qtbot, window)
    assert window.mode_label.text() == "Idle"
    assert window.position_label.text() == "10.000 mm"
    assert window.up_button.isEnabled() and window.test_button.isEnabled()
    assert not window.connect_button.isEnabled() and window.disconnect_button.isEnabled()

    window.up_button.click()
    qtbot.waitUntil(lambda: window.position_label.text() != "10.000 mm")
    assert window.position_label.text() == f"{10 - Settings().jog_step_mm:.3f} mm"


def test_run_shows_phases_and_saves(
    qtbot: QtBot, window: MainWindow, dialogs: Dialogs, tmp_path: Path
) -> None:
    dialogs.answers["Test finished"] = Button.Save
    dialogs.file = str(tmp_path / "result")  # the .csv suffix is added
    start_test(qtbot, window)
    assert not window.test_button.isEnabled() and not window._spins["k_limit"].isEnabled()
    assert window.stop_button.isEnabled()
    seen = set()

    def finished() -> bool:
        seen.add(window.mode_label.text())
        return mode(window) is Mode.IDLE

    qtbot.waitUntil(finished, timeout=10000)
    qtbot.waitUntil(lambda: window.status_line.text().startswith("Saved"))
    assert {"Test running · loading", "Test running · unloading"} <= seen
    assert dialogs.asked == ["Test finished"]
    assert window.status_line.text() == f"Saved {tmp_path / 'result.csv'}"
    rows = (tmp_path / "result.csv").read_bytes().split(b"\r\n")
    assert f"Samples: {len(rows) - 2}" == window.samples_label.text()
    assert len(window._chart) > 10 and window.test_button.isEnabled()


def test_time_chart_scrolls_over_recent_history(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gui, "CHART_SECONDS", 1.0)
    connect(qtbot, window)
    qtbot.waitUntil(lambda: window._chart[-1][0] - window._chart_start > 2, timeout=5000)
    assert window._chart[-1][0] - window._chart[0][0] <= 1.0
    left, right = window._history.viewRange()[0]
    assert right == pytest.approx(window._chart[-1][0] - window._chart_start)
    assert right - left == pytest.approx(1.0)


def test_not_tared_offers_to_start_anyway(
    qtbot: QtBot, window: MainWindow, dialogs: Dialogs
) -> None:
    connect(qtbot, window)
    window.test_button.click()
    qtbot.waitUntil(lambda: dialogs.asked == ["Not tared"])
    assert mode(window) is Mode.IDLE  # the answer was No

    dialogs.answers["Not tared"] = Button.Yes
    window.test_button.click()
    qtbot.waitUntil(lambda: mode(window) is Mode.TESTING)
    qtbot.waitUntil(lambda: window.k_tare_label.text() == "override")


def test_overload_is_shown_and_acknowledged(
    qtbot: QtBot, window: MainWindow, dialogs: Dialogs
) -> None:
    connect(qtbot, window)
    window.rig.set_force_offset(30.0)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: dialogs.asked == ["Force limit exceeded"])
    assert window.mode_label.text() == "Force limit exceeded"
    assert window.ack_button.isEnabled() and not window.up_button.isEnabled()
    window.rig.set_force_offset(0.0)  # type: ignore[attr-defined]
    qtbot.wait(150)
    window.ack_button.click()
    qtbot.waitUntil(window.up_button.isEnabled)


def test_invalid_setting_is_refused_and_restored(qtbot: QtBot, window: MainWindow) -> None:
    lower = window._spins["threshold_lower"]
    lower.setValue(200)  # above the upper threshold of 60
    lower.editingFinished.emit()
    assert window.status_line.text().startswith("Not changed:")
    qtbot.waitUntil(lambda: lower.value() == 2)
    assert window._machine.settings.threshold_lower == 2

    lower.setValue(10)
    lower.editingFinished.emit()
    qtbot.waitUntil(lambda: window._machine.settings.threshold_lower == 10)


def test_slow_discovery_keeps_the_window_alive(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    qtbot.waitUntil(window.connect_button.isEnabled)

    def slow(simulated: bool) -> list[str]:
        time.sleep(0.5)
        return ["SIM-CAM", "OTHER"]

    monkeypatch.setattr(devices, "list_cameras", slow)
    window.refresh_button.click()
    ticks: list[float] = []
    window._timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    qtbot.wait(300)
    assert len(ticks) >= 3  # the timer kept running during the discovery
    assert not window.refresh_button.isEnabled() and not window.connect_button.isEnabled()
    qtbot.waitUntil(window.connect_button.isEnabled)
    assert window.camera_combo.count() == 2 and window.camera_combo.currentText() == "SIM-CAM"


def test_close_while_running_asks_then_resolves_the_result(
    qtbot: QtBot, window: MainWindow, dialogs: Dialogs
) -> None:
    start_test(qtbot, window)
    qtbot.waitUntil(lambda: window._machine.status().samples >= 2)
    window.close()  # answer: No
    assert dialogs.asked == ["Exit"] and window.isVisible()
    assert mode(window) is Mode.TESTING

    dialogs.answers["Exit"] = Button.Yes  # stop; then the result is offered: Cancel
    window.close()
    qtbot.waitUntil(lambda: "Test finished" in dialogs.asked, timeout=5000)
    qtbot.wait(300)
    assert window.isVisible() and mode(window) is Mode.RESULT_PENDING  # data and window kept

    dialogs.answers["Test finished"] = Button.Discard  # close again: Discard, confirmed
    dialogs.answers["Discard result"] = Button.Yes
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert dialogs.asked.count("Discard result") == 1  # no duplicate discard
    assert not window._machine._controller.is_alive()


def test_close_with_failed_save_keeps_the_result(
    qtbot: QtBot, window: MainWindow, dialogs: Dialogs, tmp_path: Path
) -> None:
    start_test(qtbot, window)  # the result is offered when the test ends: Cancel
    qtbot.waitUntil(lambda: mode(window) is Mode.RESULT_PENDING, timeout=10000)
    qtbot.waitUntil(lambda: dialogs.asked == ["Test finished"])
    assert window.result_button.isEnabled()

    dialogs.answers["Test finished"] = Button.Save
    dialogs.file = str(tmp_path / "missing" / "result.csv")
    window.close()
    qtbot.waitUntil(lambda: "Not saved" in dialogs.asked)
    dialogs.answers["Test finished"] = Button.Cancel  # the retry offer: decide later
    qtbot.wait(300)
    assert window.isVisible() and mode(window) is Mode.RESULT_PENDING

    dialogs.answers["Test finished"] = Button.Save
    dialogs.file = str(tmp_path / "result.csv")
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert (tmp_path / "result.csv").exists()


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("bending-machine ")
