"""Machine: the controller thread, the camera and force threads, and the shared status.

The GUI calls the command methods (they only enqueue) and polls status(), images()
and messages() on a timer. The one direct cross-thread device call is stage.stop().
Design: docs/02_design.md §9.
"""

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite, isnan, nan
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from bending_machine import storage, vision
from bending_machine.devices import Camera, LoadCell, Stage
from bending_machine.models import (
    CURVATURE_SIGN,
    FORCE_PERIOD_S,
    FORCE_SIGN,
    LOADING_STAGE_SIGN,
    OVERLOAD_LIMIT_G,
    STAGE_UP_SIGN,
    CameraError,
    CameraTimeout,
    DeviceError,
    EndReason,
    ForceReading,
    LoadCellError,
    MarkerSet,
    Measurement,
    Mode,
    MotionInterrupted,
    Sample,
    SaveError,
    Settings,
    StageError,
    TareState,
    TestRecord,
)
from bending_machine.sequences import Finish, Hold, MoveTo, PreconditionSequence, Step
from bending_machine.sequences import TestRunSequence as RunSequence  # not a pytest class

_log = logging.getLogger(__name__)

Image = npt.NDArray[np.uint8]

FRESH_S = 0.1  # a frame or force reading older than this is stale
BUSY = {Mode.MOVING, Mode.PRECONDITIONING, Mode.TESTING}
MODE_TEXT = {
    Mode.DISCONNECTED: "the devices are not connected",
    Mode.IDLE: "idle",
    Mode.MOVING: "the stage is moving",
    Mode.IMAGE_SETUP: "image setup is open",
    Mode.PRECONDITIONING: "preconditioning is running",
    Mode.TESTING: "a test is running",
    Mode.RESULT_PENDING: "a result is waiting to be saved",
    Mode.OVERLOAD: "the force limit is exceeded",
}
# Modes in which each command runs; commands not listed run only in IDLE.
ALLOWED = {
    "connect": {Mode.DISCONNECTED},
    "disconnect": {Mode.DISCONNECTED, Mode.IDLE, Mode.OVERLOAD},
    "apply_settings": set(Mode) - BUSY,
    "set_image_setup": {Mode.IDLE, Mode.IMAGE_SETUP},
    "save_result": {Mode.RESULT_PENDING},
    "discard_result": {Mode.RESULT_PENDING},
    "acknowledge_overload": {Mode.OVERLOAD},
}
NOT_CONNECTED = {d: f"{d}: not connected" for d in ("stage", "load cell", "camera")}


@dataclass(frozen=True, slots=True)
class Status:
    """Everything the GUI shows, replaced as a whole whenever something changes."""

    mode: Mode = Mode.DISCONNECTED
    phase: str = ""  # text of the current or last workflow phase / end reason
    position_mm: float | None = None
    measurement: Measurement | None = None
    tare: TareState = TareState()  # noqa: RUF009
    cycles_done: int = 0
    samples: int = 0  # samples in the running or pending test
    marker_warning: bool = False  # a workflow is holding for a wrong marker count
    device_errors: tuple[str, ...] = ()  # one "device: message" per device not connected
    fps: float = 0.0


@dataclass(frozen=True, slots=True)
class Message:
    """Something to tell the operator once.

    kind: "info" and "rejected" go to the status line; "fault", "overload",
    "not_tracking", "not_tared", "saved" and "save_failed" open a dialog.
    """

    kind: str
    text: str


class Machine:
    """Owns the devices and runs every workflow."""

    def __init__(
        self, stage: Stage, loadcell: LoadCell, camera: Camera, settings: Settings, folder: Path
    ) -> None:
        self._stage, self._loadcell, self._camera = stage, loadcell, camera
        self._folder = folder
        # One lock guards everything below that more than one thread touches. It is
        # never held across a device call, file I/O, a wait or a join.
        self._lock = threading.Condition()
        self._settings = settings
        self._status = Status(device_errors=tuple(NOT_CONNECTED.values()))
        self._errors = dict(NOT_CONNECTED)  # device -> "device: message", for missing devices
        self._messages: list[Message] = []
        self._images: tuple[Image, Image, MarkerSet | None] | None = None
        self._forces: deque[ForceReading] = deque(maxlen=200)
        self._latched = False  # overload tripped and not yet acknowledged
        self._cancel = threading.Event()  # ends the running move or workflow
        self._reason: EndReason | None = None  # why it was cancelled
        # Controller thread only:
        self._record: TestRecord | None = None  # finished test waiting to be saved
        self._image_setup = False
        self._workers: list[threading.Thread] = []
        self._workers_stop = threading.Event()
        self._closed = False
        self._queue: queue.Queue[tuple[str, tuple[Any, ...]]] = queue.Queue()
        self._controller = threading.Thread(target=self._control, name="controller", daemon=True)
        self._controller.start()

    # --- read by the GUI (any thread, never blocks) ---

    def status(self) -> Status:
        with self._lock:
            return self._status

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings

    def images(self) -> tuple[Image, Image, MarkerSet | None] | None:
        """Latest camera image, binary image and markers; None before the first frame."""
        with self._lock:
            return self._images

    def messages(self) -> list[Message]:
        """Messages since the last call, oldest first."""
        with self._lock:
            drained, self._messages = self._messages, []
            return drained

    # --- commands (any thread; enqueue and return) ---

    def _submit(self, name: str, *args: Any) -> None:
        """Queue a command; while something moves it is rejected, never deferred."""
        mode = self.status().mode
        if mode in BUSY:
            self._post("rejected", f"Not available while {MODE_TEXT[mode]}.")
        else:
            self._queue.put((name, args))

    def connect(self, stage_port: str, camera_id: str) -> None:
        self._submit("connect", stage_port, camera_id)

    def disconnect(self) -> None:
        self._submit("disconnect")

    def apply_settings(self, settings: Settings) -> None:
        self._submit("apply_settings", settings)

    def jog(self, direction: int) -> None:
        """One relative move of jog_step_mm; +1 up, -1 down."""
        self._submit("jog", direction)

    def move_absolute(self, target_mm: float) -> None:
        self._submit("move_absolute", target_mm)

    def set_image_setup(self, on: bool) -> None:
        self._submit("set_image_setup", on)

    def tare_curvature(self) -> None:
        self._submit("tare_curvature")

    def tare_force(self) -> None:
        """Enqueue tare using the fresh signed raw reading, independent of prior tare."""
        self._submit("tare_force")

    def start_precondition(self) -> None:
        self._submit("start_precondition")

    def start_test(self, override_tare: bool = False) -> None:
        self._submit("start_test", override_tare)

    def save_result(self, path: Path, overwrite: bool) -> None:
        self._submit("save_result", path, overwrite)

    def discard_result(self) -> None:
        self._submit("discard_result")

    def acknowledge_overload(self) -> None:
        self._submit("acknowledge_overload")

    # --- immediate (any thread) ---

    def stop(self) -> None:
        """Stop whatever is running: cancels the workflow and calls stage.stop() directly."""
        mode = self.status().mode
        if mode is Mode.IMAGE_SETUP:
            self._queue.put(("set_image_setup", (False,)))
        elif mode in BUSY:
            self._cancel_with(EndReason.STOPPED)
            self._stage.stop()

    def shutdown(self) -> None:
        """Idempotent cleanup: stop motion, join threads, release devices. Never raises.

        Called off the GUI thread during normal close.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._cancel_with(EndReason.STOPPED)
            self._stage.stop()
            self._queue.put(("quit", ()))
            self._controller.join()
            self._release()
        except Exception:
            _log.exception("shutdown failed")

    # --- shared by all threads ---

    def _set(self, **changes: Any) -> None:
        with self._lock:
            self._status = replace(self._status, **changes)

    def _post(self, kind: str, text: str) -> None:
        _log.log(
            logging.INFO if kind in ("info", "saved") else logging.WARNING, "%s: %s", kind, text
        )
        with self._lock:
            self._messages.append(Message(kind, text))

    def _cancel_with(self, reason: EndReason) -> None:
        with self._lock:
            if not self._cancel.is_set():
                self._reason = reason  # the first cause wins
            self._cancel.set()
            self._lock.notify_all()  # wakes a wait for a measurement

    def _fault(self, error: DeviceError) -> None:
        """A device failed: stop motion, end the workflow, mark the device as missing."""
        _log.error("fault: %s", error)
        self._stage.stop()
        with self._lock:
            self._errors[error.device] = str(error)
            self._status = replace(self._status, device_errors=tuple(self._errors.values()))
        self._post("fault", str(error))
        self._cancel_with(EndReason.FAULT)
        self._queue.put(("rest", ()))  # lets an idle controller leave IDLE

    def _fresh_force(self) -> float | None:
        """The newest raw force reading, or None if it is missing or stale."""
        with self._lock:
            reading = self._forces[-1] if self._forces else None
        if reading is None or time.perf_counter() - reading.timestamp > FRESH_S:
            return None
        return reading.raw

    # --- camera and force threads ---

    def _watch_camera(self, stop: threading.Event) -> None:
        timeouts, fps, last = 0, 0.0, None
        while not stop.is_set():
            try:
                frame = self._camera.grab_latest(1.0)
            except CameraTimeout as error:
                timeouts += 1
                if timeouts == 3:
                    return self._fault(error)
                continue
            except CameraError as error:
                return self._fault(error)
            timeouts = 0
            with self._lock:
                s, tare, forces = self._settings, self._status.tare, list(self._forces)
            image = vision.prepare(frame.image)
            binary = vision.binarise(image, s.threshold_lower, s.threshold_upper, s.invert)
            markers = curvature = None
            if s.tracking:
                markers = vision.find_markers(
                    binary, s.roi, s.min_area_px, s.max_area_px, s.marker_count, frame.timestamp
                )
                if markers.valid:
                    try:
                        curvature = CURVATURE_SIGN * vision.compute_curvature(
                            markers.xs, markers.ys, s.resolution_mm_per_px, s.fit_subset
                        )
                    except ValueError:  # for example two markers at the same x
                        curvature = None
            # The reading nearest in time to the frame; min() keeps the earlier one on a tie.
            near = min(forces, key=lambda r: abs(r.timestamp - frame.timestamp), default=None)
            raw = near.raw if near else nan
            measurement = Measurement(
                markers=markers,
                curvature=curvature,
                dk=None if curvature is None or tare.k_ref is None else curvature - tare.k_ref,
                force_raw=raw,
                force=vision.apply_tare(raw, FORCE_SIGN, tare.f_ref),
                frame_time=frame.timestamp,
                force_time=near.timestamp if near else frame.timestamp,
            )
            if last is not None:
                fps = (
                    0.9 * fps + 0.1 / (frame.timestamp - last)
                    if fps
                    else 1 / (frame.timestamp - last)
                )
            last = frame.timestamp
            with self._lock:
                self._images = (image, binary, markers)
                self._status = replace(self._status, measurement=measurement, fps=fps)
                self._lock.notify_all()

    def _watch_force(self, stop: threading.Event) -> None:
        errors = 0
        due = time.perf_counter()
        while not stop.wait(max(due - time.perf_counter(), 0)):
            due = max(due + FORCE_PERIOD_S, time.perf_counter())  # no catching up after a slow read
            try:
                raw = self._loadcell.read_raw()
                if isnan(raw):
                    raise LoadCellError("the reading is not a number")
            except LoadCellError as error:
                errors += 1
                if errors == 3:
                    return self._fault(error)
                continue
            errors = 0
            with self._lock:
                self._forces.append(ForceReading(raw, time.perf_counter()))
                trip = vision.is_overload(raw, OVERLOAD_LIMIT_G) and not self._latched
                self._latched = self._latched or trip
            if trip:
                self._stage.stop()
                self._cancel_with(EndReason.OVERLOAD)
                self._post(
                    "overload", f"Force limit exceeded ({raw:.1f} g, limit {OVERLOAD_LIMIT_G:g} g)."
                )
                self._queue.put(("rest", ()))

    # --- controller thread ---

    def _control(self) -> None:
        while True:
            name, args = self._queue.get()
            if self._closed:
                return
            self._rest()  # a fault or overload may have arrived since the last command
            mode = self.status().mode
            if name == "rest":
                continue
            if mode not in ALLOWED.get(name, {Mode.IDLE}):
                self._post("rejected", f"Not available while {MODE_TEXT[mode]}.")
                continue
            _log.info("command %s%s", name, args)
            try:
                getattr(self, "_do_" + name)(*args)
            except Exception as error:
                _log.exception("command %s failed", name)
                self._stage.stop()
                self._post("fault", f"application: {error}")
            self._rest()

    def _rest(self) -> None:
        """Enter the mode that follows from what is pending, missing or latched."""
        with self._lock:
            if self._record:
                mode = Mode.RESULT_PENDING
            elif self._errors:
                mode = Mode.DISCONNECTED
            elif self._latched:
                mode = Mode.OVERLOAD
            else:
                mode = Mode.IMAGE_SETUP if self._image_setup else Mode.IDLE
            self._image_setup = mode is Mode.IMAGE_SETUP
            if mode is not self._status.mode:
                _log.info("mode %s", mode.value)
                self._status = replace(self._status, mode=mode)

    def _release(self) -> None:
        """End the camera and force threads, then disconnect the devices."""
        self._workers_stop.set()
        for worker in self._workers:
            worker.join()
        self._workers = []
        for device in (self._camera, self._loadcell, self._stage):
            device.disconnect()

    def _do_connect(self, stage_port: str, camera_id: str) -> None:
        self._release()
        errors = {}
        connects: dict[str, Callable[[], None]] = {
            "stage": lambda: self._stage.connect(stage_port),
            "load cell": self._loadcell.connect,
            "camera": lambda: self._camera.connect(camera_id),
        }
        for device, connect in connects.items():
            try:
                connect()
            except DeviceError as error:
                errors[device] = str(error)
        try:
            position = self._stage.position_mm() if not errors else None
        except StageError as error:
            errors["stage"], position = str(error), None
        with self._lock:
            self._errors = errors
            self._forces.clear()
            self._latched = False
            self._status = replace(
                self._status, position_mm=position, device_errors=tuple(errors.values())
            )
        if errors:  # all or nothing, so the next Connect starts clean
            self._release()
            return self._post("fault", "Connection failed.\n" + "\n".join(errors.values()))
        self._workers_stop = threading.Event()
        self._workers = [
            threading.Thread(target=loop, args=(self._workers_stop,), name=name, daemon=True)
            for name, loop in (("camera", self._watch_camera), ("force", self._watch_force))
        ]
        for worker in self._workers:
            worker.start()

    def _do_disconnect(self) -> None:
        self._release()
        with self._lock:
            self._errors = dict(NOT_CONNECTED)
            self._latched = False
            self._images = None
            self._status = replace(
                self._status,
                position_mm=None,
                measurement=None,
                tare=TareState(),
                device_errors=tuple(self._errors.values()),
                fps=0.0,
            )

    def _do_apply_settings(self, settings: Settings) -> None:
        with self._lock:
            self._settings = settings
        try:
            storage.save_settings(self._folder, settings)
        except SaveError as error:
            _log.warning("settings not saved: %s", error)

    def _do_set_image_setup(self, on: bool) -> None:
        self._image_setup = on

    def _do_tare_curvature(self) -> None:
        m = self.status().measurement
        if m is None or m.curvature is None or time.perf_counter() - m.frame_time > FRESH_S:
            return self._post(
                "rejected", "Curvature cannot be tared: not all markers are detected."
            )
        self._set(tare=replace(self.status().tare, k_ref=m.curvature))

    def _do_tare_force(self) -> None:
        raw = self._fresh_force()
        if raw is None or not isfinite(raw):
            return self._post("rejected", "No force reading available.")
        self._set(tare=replace(self.status().tare, f_ref=vision.tare_reference(raw, FORCE_SIGN)))

    def _do_acknowledge_overload(self) -> None:
        raw = self._fresh_force()
        if raw is None or vision.is_overload(raw, OVERLOAD_LIMIT_G):
            return self._post("rejected", "Force is still above the limit.")
        with self._lock:
            self._latched = False

    def _do_save_result(self, path: Path, overwrite: bool) -> None:
        assert self._record is not None
        try:
            storage.write_results(self._record, path, overwrite)
        except SaveError as error:
            return self._post("save_failed", str(error))
        self._record = None
        self._post("saved", str(path))

    def _do_discard_result(self) -> None:
        self._record = None

    def _do_jog(self, direction: int) -> None:
        self._begin(Mode.MOVING)
        self._move(delta=direction * STAGE_UP_SIGN * self.settings.jog_step_mm)

    def _do_move_absolute(self, target_mm: float) -> None:
        self._begin(Mode.MOVING)
        self._move(target=target_mm)

    # --- workflows (controller thread) ---

    def _can_start(self, what: str, force_tare: bool, override: bool) -> bool:
        """The start checks of design §9.4; posts the reason when one fails."""

        def fresh() -> bool:
            m = self._status.measurement
            return m is not None and time.perf_counter() - m.frame_time <= FRESH_S

        with self._lock:  # a busy GUI thread can delay a frame; allow it half a second
            self._lock.wait_for(fresh, timeout=0.5)
        s, status, now = self.settings, self.status(), time.perf_counter()
        m, tare = status.measurement, status.tare
        if not s.tracking:
            self._post(
                "not_tracking",
                f"The markers are not being tracked. To {what}, switch on object tracking.",
            )
        elif m is None or now - m.frame_time > FRESH_S:
            self._post("rejected", "Waiting for fresh measurement.")
        elif m.markers is None or not m.markers.valid:
            found = len(m.markers.markers) if m.markers else 0
            self._post(
                "rejected", f"Not all markers are detected (found {found} of {s.marker_count})."
            )
        elif not isfinite(m.force) or abs(m.frame_time - m.force_time) > FRESH_S:
            self._post("rejected", "Waiting for fresh force reading.")
        elif not override and not (tare.k_set and (tare.f_set or not force_tare)):
            self._post(
                "not_tared",
                "Curvature and force must be tared before a test."
                if force_tare
                else "Tare curvature before preconditioning.",
            )
        else:
            if override and not (tare.k_set and tare.f_set):
                self._set(tare=TareState(tare.k_ref or 0.0, tare.f_ref or 0.0, overridden=True))
            return True
        return False

    def _begin(self, mode: Mode) -> float:
        """Enter a moving mode with a clean cancel state; returns the start position."""
        with self._lock:
            self._reason = None
            self._cancel.clear()
            self._status = replace(
                self._status, mode=mode, phase="", cycles_done=0, samples=0, marker_warning=False
            )
            _log.info("mode %s", mode.value)
            assert self._status.position_mm is not None
            return self._status.position_mm

    def _move(self, delta: float | None = None, target: float | None = None) -> bool:
        """One blocking move, relative or absolute. False if it did not complete."""
        try:
            try:
                if delta is not None:
                    self._stage.move_relative(delta)
                elif target is not None:
                    self._stage.move_absolute(target)
                done = True
            except MotionInterrupted:
                done = False
            position = self._stage.position_mm()
        except StageError as error:
            if not self._stage.is_connected:
                self._fault(error)
            else:  # the stage refused the move (out of travel); it is still there
                self._post("rejected", str(error))
                self._cancel_with(EndReason.FAULT)
            return False
        _log.info("move %s -> %.4f mm", delta if delta is not None else target, position)
        self._set(position_mm=position)
        return done

    def _next_measurement(self, after: float) -> Measurement | None:
        """Wait for a frame captured after `after`; None when the workflow must end."""

        def arrived() -> bool:
            m = self._status.measurement
            return self._cancel.is_set() or (m is not None and m.frame_time > after)

        with self._lock:
            in_time = self._lock.wait_for(arrived, timeout=2.0)
            m = self._status.measurement
        if self._cancel.is_set():
            return None
        if not in_time or m is None:
            self._fault(CameraError("no new image for 2 s"))
        elif not isfinite(m.force) or abs(m.frame_time - m.force_time) > FRESH_S:
            self._fault(LoadCellError("no fresh force reading"))
        else:
            return m
        return None

    def _end(self, name: str, start: float) -> EndReason:
        """Finish a workflow: return to the start unless an overload or fault ended it."""
        reason = self._reason or EndReason.COMPLETED
        self._cancel.clear()  # the return move is stoppable on its own
        if (
            reason in (EndReason.COMPLETED, EndReason.STOPPED)
            and not self._closed
            and not self._cancel.wait(0.4)
        ):
            self._move(target=start)
        self._set(phase=reason.value, marker_warning=False)
        self._post("info", f"{name} ended: {reason.value}.")
        return reason

    def _do_start_test(self, override_tare: bool) -> None:
        if not self._can_start("run a test", force_tare=True, override=override_tare):
            return
        s = self.settings
        start = self._begin(Mode.TESTING)
        sequence = RunSequence(s.k_limit, s.test_step_mm, LOADING_STAGE_SIGN)
        record = TestRecord([], datetime.now(), s, self.status().tare, start)
        began = after = time.perf_counter()
        while (m := self._next_measurement(after)) is not None:
            action = sequence.next(m)
            if m.usable and m.markers is not None and m.dk is not None:
                position = self.status().position_mm or start
                record.samples.append(
                    Sample(
                        m.markers.xs, m.markers.ys, m.dk, m.force, m.frame_time - began, position
                    )
                )
            self._set(
                phase=sequence.phase.value,
                samples=len(record.samples),
                marker_warning=isinstance(action, Hold),
            )
            if isinstance(action, Finish):
                break
            if isinstance(action, Step):
                if not self._move(delta=action.delta_mm) or self._cancel.wait(s.settle_s):
                    break
            elif self._cancel.wait(0.2):  # Hold: try again with a later frame
                break
            after = time.perf_counter()  # only a frame from after the wait counts
        record.end_reason = self._end("Test", start)
        self._record = record if record.samples else None

    def _do_start_precondition(self) -> None:
        s = self.settings
        if not self._can_start("precondition the sample", force_tare=False, override=False):
            return
        if s.pc_amplitude <= 0:
            return self._post("rejected", "Set a curvature amplitude greater than 0.")
        start = self._begin(Mode.PRECONDITIONING)
        sequence = PreconditionSequence(
            s.pc_amplitude, s.pc_cycles, s.jog_step_mm, LOADING_STAGE_SIGN
        )
        limits = 0  # how many of the two limit positions are known
        self._cancel.wait(0.4)
        after = time.perf_counter()
        while (m := self._next_measurement(after)) is not None:
            action = sequence.next(m, self.status().position_mm or start)
            self._set(
                phase=sequence.phase.value,
                cycles_done=sequence.cycles_done,
                marker_warning=isinstance(action, Hold),
            )
            if isinstance(action, Finish):
                break
            known = (sequence.limit_a_mm is not None) + (sequence.limit_b_mm is not None)
            if known > limits and self._cancel.wait(0.4):  # pause at a newly found limit
                break
            limits = known
            if isinstance(action, Step):
                done = self._move(delta=action.delta_mm)
            elif isinstance(action, MoveTo):
                done = not self._cancel.wait(s.pc_dwell_s) and self._move(target=action.target_mm)
            else:  # Hold: try again with a later frame
                done = not self._cancel.wait(0.2)
            if not done:
                break
            after = time.perf_counter()
        self._end("Preconditioning", start)
