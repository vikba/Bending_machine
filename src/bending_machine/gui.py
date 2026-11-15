"""The PyQt6 window: live view, readouts, workflow cards, charts and dialogs.

The only module that imports PyQt6 or pyqtgraph, apart from main.py, which creates the
QApplication. A 15 Hz QTimer polls machine.status(), images() and messages().
Design: docs/02_design.md §10.
"""

import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from math import isfinite, nan
from pathlib import Path
from typing import Any

import pyqtgraph as pg
from PyQt6.QtCore import QLocale, Qt, QTimer
from PyQt6.QtGui import QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsRectItem,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from bending_machine import devices
from bending_machine.machine import BUSY, Machine
from bending_machine.models import OVERLOAD_LIMIT_G, FitSubset, Mode

# One palette for the whole window, the images and the charts.
BG, SURFACE, BORDER = "#14171c", "#1d2128", "#2f3540"
TEXT, MUTED = "#e6e9ee", "#8b94a3"
BLUE, GREEN, AMBER, RED = "#4c9aff", "#3ecf8e", "#f5a524", "#e5484d"
MODE_TEXT = {  # mode -> (text, colour) of the state pill
    Mode.DISCONNECTED: ("Not connected", MUTED),
    Mode.IDLE: ("Idle", GREEN),
    Mode.MOVING: ("Moving", BLUE),
    Mode.IMAGE_SETUP: ("Selecting ROI", AMBER),
    Mode.PRECONDITIONING: ("Preconditioning", BLUE),
    Mode.TESTING: ("Test running", BLUE),
    Mode.RESULT_PENDING: ("Result not saved", AMBER),
    Mode.OVERLOAD: ("Force limit exceeded", RED),
}
STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QLabel, QCheckBox, QWidget#row {{ background: transparent; }}
QLabel[role="caption"] {{ color: {MUTED}; font-size: 11px; }}
QLabel[role="value"] {{ font-size: 30px; font-weight: 600; }}
QLabel[role="title"] {{ font-size: 16px; font-weight: 600; }}
QFrame[role="tile"], QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
}}
QGroupBox {{ margin-top: 20px; padding: 6px 8px 6px 8px; }}
QGroupBox::title {{
    subcontrol-origin: margin; left: 2px; top: 2px; color: {MUTED}; font-weight: 600;
}}
QPushButton {{
    background: #2a303a; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
}}
QPushButton:hover {{ background: #353c49; }}
QPushButton:checked, QPushButton[kind="primary"] {{
    background: {BLUE}; border-color: {BLUE}; color: #0b0d10; font-weight: 600;
}}
QPushButton[kind="danger"] {{
    background: {RED}; border-color: {RED}; color: white; font-weight: 700; font-size: 15px;
}}
QPushButton:disabled {{ background: #20242b; border-color: {BORDER}; color: #59606c; }}
QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 6px;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {BLUE}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: #59606c; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 22px;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {BLUE}; }}
QProgressBar {{
    background: {BG}; border: 1px solid {BORDER}; border-radius: 5px; text-align: center;
}}
QProgressBar::chunk {{ background: {BLUE}; border-radius: 4px; }}
"""
Button = QMessageBox.StandardButton


class MainWindow(QMainWindow):
    """Resolve pending data, then shut down in the background before accepting close."""

    def __init__(self, machine: Machine, simulated: bool) -> None:
        super().__init__()
        self._machine = machine
        self._simulated = simulated
        self._spins: dict[str, QSpinBox | QDoubleSpinBox] = {}  # Settings field -> its spin box
        self._idle: list[QWidget] = []  # enabled only in IDLE
        self._editable: list[QWidget] = []  # enabled unless something is moving
        self._shown: Any = None  # the settings and images objects last drawn
        self._drawn: Any = None
        self._last_mode: Mode | None = None
        self._chart: deque[tuple[float, float, float]] = deque(maxlen=15 * 300)  # 5 minutes
        self._curve: list[tuple[float, float]] = []  # (dK, force) of the running or last test
        self._found: tuple[list[str], list[str]] | None = None  # result of a discovery
        self._discovering = False
        self._in_dialog = False
        self._asked = False  # the pending result was already offered for saving
        self._resolving = False  # a save or discard is on its way to the machine
        self._closing = False
        self._shutdown: threading.Thread | None = None
        self._folder = Path.home()  # where the last result was saved

        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyle("Fusion")  # the same look on every platform
        self.setWindowTitle("Bending test [SIMULATED]" if simulated else "Bending test")
        self.resize(1366, 768)
        centre = QWidget()
        centre.setLocale(QLocale.c())  # decimal point everywhere, as in the result files
        centre.setStyleSheet(STYLE)
        self.setCentralWidget(centre)
        root = QVBoxLayout(centre)
        root.setContentsMargins(12, 10, 12, 8)
        pg.setConfigOptions(
            imageAxisOrder="row-major", antialias=True, background=SURFACE, foreground=MUTED
        )

        def label(text: str = "", role: str = "") -> QLabel:
            made = QLabel(text)
            made.setProperty("role", role)
            return made

        def button(
            text: str, action: Callable[[], object], enabled_in: list[QWidget], kind: str = ""
        ) -> QPushButton:
            made = QPushButton(text)
            made.setProperty("kind", kind)
            made.clicked.connect(lambda: action())
            enabled_in.append(made)
            return made

        def spin(field: str, low: float, high: float, decimals: int, suffix: str = "") -> QWidget:
            made: QSpinBox | QDoubleSpinBox
            if decimals:
                made = QDoubleSpinBox()
                made.setDecimals(decimals)
                made.setRange(low, high)
            else:
                made = QSpinBox()
                made.setRange(int(low), int(high))
            made.setSuffix(suffix)
            made.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

            def edited() -> None:  # also fires when focus leaves; ignore the displayed rounding
                if made.value() != round(getattr(machine.settings, field), decimals):
                    self._change(**{field: made.value()})

            made.editingFinished.connect(edited)
            self._spins[field] = made
            self._editable.append(made)
            return made

        def row(*widgets: QWidget) -> QWidget:
            made = QWidget()
            made.setObjectName("row")  # transparent inside cards
            line = QHBoxLayout(made)
            line.setContentsMargins(0, 0, 0, 0)
            for widget in widgets:
                line.addWidget(widget, 1)
            return made

        def card(title: str, tab: QVBoxLayout) -> QFormLayout:
            box = QGroupBox(title)
            form = QFormLayout(box)
            form.setVerticalSpacing(6)
            tab.addWidget(box)
            return form

        # --- Header: what the machine is doing, what to do next, and STOP ---
        header = QHBoxLayout()
        root.addLayout(header)
        self.mode_label, self.hint_label = QLabel(), label(role="caption")
        self.hint_label.setStyleSheet("font-size: 13px")
        self.ack_button = button("Acknowledge overload", machine.acknowledge_overload, [])
        self.stop_button = button("STOP", machine.stop, [], "danger")
        self.stop_button.setShortcut(QKeySequence("Esc"))
        self.stop_button.setToolTip("Stop whatever is running (Esc)")
        self.stop_button.setFixedSize(130, 40)
        header.addWidget(label("Bending test", "title"))
        if simulated:
            header.addWidget(label("SIMULATED", "caption"))
        header.addSpacing(12)
        header.addWidget(self.mode_label)
        header.addWidget(self.hint_label, 1)
        header.addWidget(self.ack_button)
        header.addWidget(self.stop_button)

        body = QHBoxLayout()
        root.addLayout(body, 1)
        main, side = QVBoxLayout(), QVBoxLayout()
        body.addLayout(main, 1)
        body.addLayout(side)

        # --- Live view: the camera image with the ROI and the found markers drawn on it ---
        view = pg.GraphicsLayoutWidget()
        main.addWidget(view, 1)
        box = view.addViewBox(lockAspect=True, invertY=True, enableMenu=False)
        self._image = pg.ImageItem()
        self._roi = pg.RectROI((0, 0), (100, 100), pen=pg.mkPen(AMBER, width=2))  # while selecting
        self._roi_outline = QGraphicsRectItem()  # the ROI in use, otherwise
        self._roi_outline.setPen(pg.mkPen(AMBER, style=Qt.PenStyle.DashLine))
        self._marker_rings = pg.ScatterPlotItem(pen=pg.mkPen(GREEN, width=2), brush=None, size=26)
        for item in (self._image, self._roi_outline, self._roi, self._marker_rings):
            box.addItem(item)
        self._roi.hide()
        self.threshold_button = QPushButton("Threshold view")
        self.threshold_button.setCheckable(True)
        self.threshold_button.setToolTip("Show the black-and-white image the detection works on")
        self.threshold_button.clicked.connect(lambda: setattr(self, "_drawn", None))
        self.marker_label, self.fps_label = QLabel(), label(role="caption")
        under = QHBoxLayout()
        main.addLayout(under)
        under.addWidget(self.threshold_button)
        under.addWidget(self.marker_label)
        under.addStretch()
        under.addWidget(self.fps_label)

        # --- Charts: history against time, and force against curvature for the test ---
        charts = QHBoxLayout()
        main.addLayout(charts)
        history, curve = pg.PlotWidget(), pg.PlotWidget()
        for plot, stretch in ((history, 3), (curve, 2)):
            plot.setFixedHeight(200)
            plot.setMenuEnabled(False)
            plot.showGrid(x=True, y=True, alpha=0.15)
            charts.addWidget(plot, stretch)
        item = history.getPlotItem()
        item.setLabel("left", "Force, g", color=BLUE)
        item.setLabel("right", "dK, 1/mm", color=AMBER)
        item.setLabel("bottom", "Time, s")
        for axis in ("left", "right", "bottom"):  # plain numbers, not "x0.001"
            item.getAxis(axis).enableAutoSIPrefix(False)
        dk_view = pg.ViewBox()  # a second view box gives dK its own axis on the right
        item.scene().addItem(dk_view)
        item.getAxis("right").linkToView(dk_view)
        dk_view.setXLink(item)
        item.vb.sigResized.connect(lambda: dk_view.setGeometry(item.vb.sceneBoundingRect()))
        self._force_curve = item.plot(pen=pg.mkPen(BLUE, width=2))
        self._dk_curve = pg.PlotCurveItem(pen=pg.mkPen(AMBER, width=2), connect="finite")
        dk_view.addItem(self._dk_curve)
        curve.setLabel("left", "Force, g")
        curve.setLabel("bottom", "dK, 1/mm  (this test)")
        for axis in ("left", "bottom"):
            curve.getPlotItem().getAxis(axis).enableAutoSIPrefix(False)
        self._test_curve = curve.plot(pen=pg.mkPen(GREEN, width=2))

        # --- Readouts with their tare buttons, always visible ---
        tiles = QHBoxLayout()
        side.addLayout(tiles)
        self.dk_label, self.force_label = label("—", "value"), label("—", "value")
        self.k_tare_label, self.f_tare_label = label(role="caption"), label(role="caption")
        self.tare_k_button = button("Tare", machine.tare_curvature, self._idle)
        self.tare_f_button = button("Tare", machine.tare_force, self._idle)
        for caption, value, tare, state in (
            ("CURVATURE CHANGE dK, 1/mm", self.dk_label, self.tare_k_button, self.k_tare_label),
            (
                f"FORCE, g  (limit {OVERLOAD_LIMIT_G:g})",
                self.force_label,
                self.tare_f_button,
                self.f_tare_label,
            ),
        ):
            tile = QFrame()
            tile.setProperty("role", "tile")
            inside = QVBoxLayout(tile)
            inside.addWidget(label(caption, "caption"))
            inside.addWidget(value)
            inside.addWidget(row(tare, state))
            tiles.addWidget(tile)

        tabs = QTabWidget()
        tabs.setFixedWidth(400)
        side.addWidget(tabs, 1)
        pages = []
        for name in ("Setup", "Run"):
            page = QWidget()
            pages.append(QVBoxLayout(page))
            pages[-1].setContentsMargins(0, 4, 0, 0)
            tabs.addTab(page, name)
        setup, run = pages

        # --- Setup: devices ---
        form = card("Devices", setup)
        self.port_combo, self.camera_combo = QComboBox(), QComboBox()
        self.port_combo.activated.connect(
            lambda: self._change(stage_port=self.port_combo.currentText())
        )
        self.camera_combo.activated.connect(
            lambda: self._change(camera_id=self.camera_combo.currentText())
        )
        self.refresh_button = button("Refresh", self._discover, [])
        self.connect_button = button(
            "Connect",
            lambda: machine.connect(self.port_combo.currentText(), self.camera_combo.currentText()),
            [],
            "primary",
        )
        self.disconnect_button = button("Disconnect", machine.disconnect, [])
        self.device_label = label(role="caption")
        self.device_label.setWordWrap(True)
        form.addRow("Stage port", self.port_combo)
        form.addRow("Camera", self.camera_combo)
        form.addRow(self.refresh_button, row(self.connect_button, self.disconnect_button))
        form.addRow(self.device_label)

        # --- Setup: marker detection ---
        form = card("Marker detection", setup)
        self.tracking_button = QPushButton()
        self.tracking_button.setCheckable(True)
        self.tracking_button.clicked.connect(lambda on: self._change(tracking=on))
        self.invert_check = QCheckBox("Invert")
        self.invert_check.clicked.connect(lambda on: self._change(invert=on))
        self.fit_combo = QComboBox()
        self.fit_combo.addItem("Fit all", FitSubset.ALL)
        self.fit_combo.addItem("Fit without outer two", FitSubset.EXCLUDE_OUTER)
        self.fit_combo.activated.connect(
            lambda: self._change(fit_subset=self.fit_combo.currentData())
        )
        self._editable += [self.tracking_button, self.invert_check, self.fit_combo]
        self.roi_button = button("Select…", lambda: machine.set_image_setup(True), self._idle)
        self.roi_done_button = button("Done", self._roi_done, [], "primary")
        clear = button("Clear", lambda: self._change(roi=None), self._editable)
        form.addRow(self.tracking_button)
        form.addRow("Markers", row(spin("marker_count", 3, 50, 0), self.fit_combo))
        form.addRow(
            "Grey level",
            row(
                spin("threshold_lower", 0, 255, 0),
                spin("threshold_upper", 0, 255, 0),
                self.invert_check,
            ),
        )
        form.addRow(
            "Area, px²",
            row(spin("min_area_px", 1, 10_000_000, 0), spin("max_area_px", 1, 10_000_000, 0)),
        )
        form.addRow("Search area", row(self.roi_button, self.roi_done_button, clear))
        form.addRow("Scale", spin("resolution_mm_per_px", 0.000001, 10, 6, " mm/px"))
        setup.addStretch()

        # --- Run: stage ---
        form = card("Stage", run)
        self.position_label = label("—", "title")
        self.up_button = button("▲  Up", lambda: machine.jog(+1), self._idle)
        self.down_button = button("▼  Down", lambda: machine.jog(-1), self._idle)
        self.goto_spin = QDoubleSpinBox()
        self.goto_spin.setRange(-1000, 1000)
        self.goto_spin.setDecimals(3)
        self.goto_spin.setSuffix(" mm")
        self.goto_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._idle.append(self.goto_spin)
        go = button("Go", lambda: machine.move_absolute(self.goto_spin.value()), self._idle)
        form.addRow("Position", self.position_label)
        form.addRow(
            "Jog", row(spin("jog_step_mm", 0.0001, 5, 4, " mm"), self.up_button, self.down_button)
        )
        form.addRow("Go to", row(self.goto_spin, go))

        # --- Run: preconditioning ---
        form = card("Preconditioning", run)
        self.precondition_button = button(
            "Start preconditioning", machine.start_precondition, self._idle
        )
        self.cycle_bar = QProgressBar()
        self.cycle_bar.setFormat("cycle %v of %m")
        form.addRow("Amplitude", spin("pc_amplitude", 0, 5, 4, " 1/mm"))
        form.addRow(
            "Cycles, dwell", row(spin("pc_cycles", 1, 1000, 0), spin("pc_dwell_s", 0, 60, 1, " s"))
        )
        form.addRow(row(self.precondition_button, self.cycle_bar))

        # --- Run: test ---
        form = card("Test", run)
        self.test_button = button("Start test", machine.start_test, self._idle, "primary")
        self.result_button = button("Save or discard result…", self._offer_result, [], "primary")
        self.samples_label = label(role="caption")
        form.addRow("Curvature limit", spin("k_limit", -5, 5, 4, " 1/mm"))
        form.addRow(
            "Step, settle",
            row(spin("test_step_mm", 0.0001, 5, 4, " mm"), spin("settle_s", 0.05, 10, 2, " s")),
        )
        form.addRow(row(self.test_button, self.samples_label))
        form.addRow(self.result_button)
        run.addStretch()

        self.status_line = label(role="caption")
        root.addWidget(self.status_line)

        self._discover()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // 15)
        self._tick()

    # --- operator actions ---

    def _change(self, **update: Any) -> None:
        """Apply changed settings; an invalid value is refused and the widget restored."""
        current = self._machine.settings
        try:
            changed = current.model_copy(update=update)
        except ValueError as error:
            self.status_line.setText(f"Not changed: {str(error).splitlines()[-1].strip()}")
            self._shown = None  # the next tick puts the old values back
            return
        if changed != current:
            self._machine.apply_settings(changed)

    def _discover(self) -> None:
        """List ports and cameras on a background thread; _tick fills the combo boxes."""

        def look() -> None:
            found = devices.list_stage_ports(self._simulated), devices.list_cameras(self._simulated)
            self._found = found

        self._discovering = True
        threading.Thread(target=look, name="discovery", daemon=True).start()

    def _roi_done(self) -> None:
        x, y = self._roi.pos()
        width, height = self._roi.size()
        box = {"left": int(x), "top": int(y), "right": int(x + width), "bottom": int(y + height)}
        self._change(roi={name: max(value, 0) for name, value in box.items()})
        self._machine.set_image_setup(False)

    def _offer_result(self) -> None:
        """Ask what to do with the finished test: save, discard or decide later."""
        self._asked = True
        choice = QMessageBox.question(
            self,
            "Test finished",
            f"Save the result ({self._machine.status().samples} samples)?",
            Button.Save | Button.Discard | Button.Cancel,
        )
        if choice == Button.Save:
            name = self._folder / f"Test_{datetime.now():%Y%m%d_%H%M%S}.lvm"
            # The file dialog itself asks before replacing an existing file.
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save result", str(name), "Results (*.lvm)"
            )
            if chosen:
                self._folder = Path(chosen).parent
                self._resolving = True
                return self._machine.save_result(Path(chosen).with_suffix(".lvm"), True)
        elif choice == Button.Discard:
            sure = QMessageBox.question(self, "Discard result", "Discard the result for good?")
            if sure == Button.Yes:
                self._resolving = True
                return self._machine.discard_result()
        self._closing = False  # undecided: the result and the window stay

    def closeEvent(self, event: QCloseEvent | None) -> None:
        """Never closes directly: _tick stops, resolves the result and shuts down first."""
        assert event is not None
        if self._shutdown is not None and not self._shutdown.is_alive():
            self._timer.stop()
            return event.accept()
        event.ignore()
        if self._closing or self._in_dialog:
            return
        if self._machine.status().mode in BUSY:
            sure = QMessageBox.question(self, "Exit", "Stop the running activity and exit?")
            if sure != Button.Yes:
                return
            self._machine.stop()
        self._closing = True
        self._asked = False  # offer a pending result once more

    # --- the 15 Hz refresh ---

    def _tick(self) -> None:
        machine = self._machine
        status, settings, mode = machine.status(), machine.settings, machine.status().mode
        busy = mode in BUSY
        m, tare = status.measurement, status.tare
        found = len(m.markers.markers) if m is not None and m.markers is not None else None

        if self._found is not None:  # a discovery finished
            for combo, names, stored in (
                (self.port_combo, self._found[0], settings.stage_port),
                (self.camera_combo, self._found[1], settings.camera_id),
            ):
                combo.clear()
                combo.addItems(names)
                combo.setCurrentIndex(max(combo.findText(stored), 0))
            self._found, self._discovering = None, False
            if mode is Mode.DISCONNECTED and self.port_combo.count() and self.camera_combo.count():
                self._change(
                    stage_port=self.port_combo.currentText(),
                    camera_id=self.camera_combo.currentText(),
                )

        # Enabled and visible state follows the mode.
        for widget in self._idle:
            widget.setEnabled(mode is Mode.IDLE and not self._closing)
        for widget in self._editable:
            widget.setEnabled(not busy)
        can_connect = mode is Mode.DISCONNECTED and not self._discovering and not self._closing
        for widget in (self.port_combo, self.camera_combo, self.refresh_button):
            widget.setEnabled(can_connect)
        self.connect_button.setEnabled(
            can_connect and bool(self.port_combo.count() and self.camera_combo.count())
        )
        self.connect_button.setVisible(mode is Mode.DISCONNECTED)
        self.disconnect_button.setVisible(mode is not Mode.DISCONNECTED)
        self.disconnect_button.setEnabled(mode in (Mode.IDLE, Mode.OVERLOAD))
        self.roi_done_button.setEnabled(mode is Mode.IMAGE_SETUP)
        self.result_button.setEnabled(mode is Mode.RESULT_PENDING and not self._resolving)
        self.result_button.setVisible(mode is Mode.RESULT_PENDING)
        self.ack_button.setEnabled(mode is Mode.OVERLOAD)
        self.ack_button.setVisible(mode is Mode.OVERLOAD)
        self.stop_button.setEnabled(busy or mode is Mode.IMAGE_SETUP)

        # Settings into the widgets, when they changed and are not being edited.
        if settings is not self._shown:
            self._shown = settings
            for field, spin in self._spins.items():
                if not spin.hasFocus():
                    spin.setValue(getattr(settings, field))
            self.invert_check.setChecked(settings.invert)
            self.fit_combo.setCurrentIndex(self.fit_combo.findData(settings.fit_subset))
            self.tracking_button.setChecked(settings.tracking)
            self.tracking_button.setText("Tracking on" if settings.tracking else "Start tracking")
            self.cycle_bar.setMaximum(settings.pc_cycles)

        # The state pill, and a hint at the operator's next step.
        text, colour = MODE_TEXT[mode]
        phase = status.phase.replace("_", " ")
        self.mode_label.setText(f"{text} · {phase}" if phase else text)
        if mode is not self._last_mode:
            self.mode_label.setStyleSheet(
                f"background: {colour}; color: #0b0d10; font-weight: 600;"
                "border-radius: 11px; padding: 3px 12px"
            )
        if mode is Mode.DISCONNECTED:
            hint = "Choose the stage port and the camera, then connect."
        elif mode is Mode.OVERLOAD:
            hint = "Remove the load, then acknowledge the overload."
        elif mode is Mode.RESULT_PENDING:
            hint = "Save or discard the result."
        elif mode is Mode.IMAGE_SETUP:
            hint = "Drag the rectangle around the markers, then press Done."
        elif busy:
            hint = "Waiting for all markers…" if status.marker_warning else "STOP or Esc ends it."
        elif not settings.tracking:
            hint = "Set up the marker detection and start tracking."
        elif found != settings.marker_count:
            hint = "Adjust the detection until all markers are found."
        elif not (tare.k_set and tare.f_set):
            hint = "Tare curvature and force."
        else:
            hint = "Ready: precondition the sample or start a test."
        self.hint_label.setText(hint)

        # Readouts.
        self.device_label.setText("\n".join(status.device_errors) or "All devices connected")
        position = status.position_mm
        self.position_label.setText("—" if position is None else f"{position:.3f} mm")
        self.dk_label.setText("—" if m is None or m.dk is None else f"{m.dk:.4f}")
        self.force_label.setText(f"{m.force:.3f}" if m is not None and isfinite(m.force) else "—")
        for state, is_set in ((self.k_tare_label, tare.k_set), (self.f_tare_label, tare.f_set)):
            state.setText("override" if tare.overridden else "tared" if is_set else "not tared")
        self.fps_label.setText(f"{status.fps:.1f} fps")
        all_found = found == settings.marker_count
        self.marker_label.setText(
            "Tracking off" if found is None else f"{found} of {settings.marker_count} markers"
        )
        self.marker_label.setStyleSheet(
            f"color: {MUTED if found is None else GREEN if all_found else RED}; font-weight: 600"
        )
        self.cycle_bar.setValue(status.cycles_done)
        self.samples_label.setText(f"Samples: {status.samples}")

        # Image, ROI and markers.
        images = machine.images()
        if images is not None and images is not self._drawn:
            self._drawn = images
            shown = images[1] if self.threshold_button.isChecked() else images[0]
            self._image.setImage(shown, autoLevels=False, levels=(0, 255))
            markers = images[2]
            self._marker_rings.setData(markers.xs if markers else [], markers.ys if markers else [])
        selecting, roi = mode is Mode.IMAGE_SETUP, settings.roi
        if selecting and not self._roi.isVisible():  # start from the ROI in use, or a default
            self._roi.setPos((roi.left, roi.top) if roi else (200, 200))
            self._roi.setSize((roi.right - roi.left, roi.bottom - roi.top) if roi else (800, 400))
        self._roi.setVisible(selecting)
        self._roi_outline.setVisible(roi is not None and not selecting)
        if roi is not None:
            self._roi_outline.setRect(roi.left, roi.top, roi.right - roi.left, roi.bottom - roi.top)

        # Charts.
        started = mode is not self._last_mode and mode in (Mode.PRECONDITIONING, Mode.TESTING)
        if started:
            self._chart.clear()
        if started and mode is Mode.TESTING:
            self._curve.clear()
        if m is not None and (not self._chart or m.frame_time != self._chart[-1][0]):
            self._chart.append((m.frame_time, m.force, nan if m.dk is None else m.dk))
            times = [t - self._chart[0][0] for t, _, _ in self._chart]
            self._force_curve.setData(times, [force for _, force, _ in self._chart])
            self._dk_curve.setData(times, [dk for _, _, dk in self._chart])
            if mode is Mode.TESTING and m.usable and m.dk is not None:
                self._curve.append((m.dk, m.force))
            self._test_curve.setData([dk for dk, _ in self._curve], [f for _, f in self._curve])

        if mode is not Mode.RESULT_PENDING:
            self._asked = self._resolving = False
        self._last_mode = mode
        if self._in_dialog:  # a dialog of ours is open; it must not be opened again
            return
        self._in_dialog = True
        try:
            self._talk(mode)
        finally:
            self._in_dialog = False

    def _talk(self, mode: Mode) -> None:
        """Everything that may open a dialog: messages, the pending result, closing."""
        for message in self._machine.messages():
            kind, text = message.kind, message.text
            if kind in ("info", "rejected", "saved"):
                self.status_line.setText(f"Saved {text}" if kind == "saved" else text)
            elif kind == "fault":
                QMessageBox.warning(self, "Device problem", text)
            elif kind == "overload":
                QMessageBox.warning(
                    self,
                    "Force limit exceeded",
                    f"{text}\n\nThe stage was stopped. Remove the load, then press "
                    "“Acknowledge overload”.",
                )
            elif kind == "not_tracking":
                QMessageBox.information(self, "Not started", text)
            elif kind == "not_tared" and "test" in text:
                if (
                    QMessageBox.question(self, "Not tared", f"{text}\n\nStart anyway?")
                    == Button.Yes
                ):
                    self._machine.start_test(override_tare=True)
            elif kind == "not_tared":
                QMessageBox.information(self, "Not started", text)
            elif kind == "save_failed":
                self._resolving = self._asked = self._closing = False  # offered again below
                QMessageBox.warning(self, "Not saved", f"The result was not saved.\n\n{text}")

        mode = self._machine.status().mode
        if mode is Mode.RESULT_PENDING and not self._asked and not self._resolving:
            self._offer_result()
        elif self._closing and mode not in BUSY and mode is not Mode.RESULT_PENDING:
            if self._shutdown is None:  # joins threads and releases devices off the GUI thread
                self._shutdown = threading.Thread(target=self._machine.shutdown, name="shutdown")
                self._shutdown.start()
            elif not self._shutdown.is_alive():
                self.close()
