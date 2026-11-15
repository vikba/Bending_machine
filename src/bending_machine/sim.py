"""Simulated rig and drivers, so the application runs with no hardware (NFR-013).

SimRig is one shared physical model; SimStage, SimLoadCell and SimCamera read and
change it. Design: docs/02_design.md §7.
"""

import math
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from bending_machine.devices import Camera, LoadCell, Stage
from bending_machine.models import (
    CameraError,
    CameraTimeout,
    Frame,
    LoadCellError,
    MotionInterrupted,
    StageError,
)

# --- rig_model -----------------------------------------------------------------


@dataclass(slots=True)
class SimRigConfig:
    """Parameters of the simulated rig."""

    start_position_mm: float = 10.0
    travel_min_mm: float = 0.0
    travel_max_mm: float = 25.0
    stage_speed_mm_s: float = 1.0
    k_per_mm: float = 0.08  # 1/mm of curvature per mm of stage travel
    force_per_k: float = 3.5  # displayed force units per 1/mm of curvature
    force_noise: float = 0.005  # std dev, device units
    marker_noise_px: float = 0.05  # std dev of marker centre jitter
    image_width: int = 1280
    image_height: int = 960
    resolution_mm_per_px: float = 0.0111
    marker_xs: tuple[float, ...] = (220.0, 310.0, 500.0, 712.0, 873.0, 1080.0)
    marker_radius_px: int = 12  # area about 452 px²
    baseline_y: float = 600.0
    centre_x: float = 650.0
    background_grey: int = 200
    marker_grey: int = 20
    frame_rate: float = 17.78
    seed: int = 1


class SimRig:
    """Thread-safe model of stage position, sample curvature, markers and force."""

    def __init__(self, config: SimRigConfig = SimRigConfig()) -> None:  # noqa: B008
        self.config = config
        self._lock = threading.Lock()  # guards the position and the random generator
        self._rng = np.random.default_rng(config.seed)
        self._position = config.start_position_mm
        self._force_offset = 0.0
        self._hidden: int | None = None
        self._failed: str | None = None

    # --- state used by the simulated drivers ---

    def position_mm(self) -> float:
        """Current stage position."""
        with self._lock:
            return self._position

    def set_position_mm(self, value: float) -> None:
        """Set the stage position; clamps to the travel limits."""
        with self._lock:
            self._position = min(max(value, self.config.travel_min_mm), self.config.travel_max_mm)

    def curvature(self) -> float:
        """k_per_mm * (start_position - position), in 1/mm: positive above the start (loaded)."""
        return self.config.k_per_mm * (self.config.start_position_mm - self.position_mm())

    def raw_force(self) -> float:
        """Raw load-cell reading for the current curvature, with noise and offset."""
        k = self.curvature()
        with self._lock:
            noise = self._rng.normal(0.0, self.config.force_noise)
        return -self.config.force_per_k * k + self._force_offset + noise

    def render_frame(self) -> npt.NDArray[np.uint8]:
        """uint8 image, image_height x image_width; no rotation."""
        c = self.config
        centres = self.marker_centres()
        with self._lock:
            jitter = self._rng.normal(0.0, c.marker_noise_px, (len(centres), 2))
        image = np.full((c.image_height, c.image_width), c.background_grey, np.uint8)
        for i, (x, y) in enumerate(centres):
            if i != self._hidden:
                # shift=4: coordinates in 1/16 px, so the jitter is not rounded away
                centre = (round((x + jitter[i, 0]) * 16), round((y + jitter[i, 1]) * 16))
                cv2.circle(
                    image, centre, c.marker_radius_px * 16, c.marker_grey, -1, cv2.LINE_AA, 4
                )
        return image

    def marker_centres(self) -> list[tuple[float, float]]:
        """Ground-truth marker centres in image coordinates."""
        c = self.config
        a = -self.curvature() * c.resolution_mm_per_px / 2  # the image fit is negative when loaded
        return [(x, c.baseline_y + a * (x - c.centre_x) ** 2) for x in c.marker_xs]

    # --- fault injection for tests ---

    def set_force_offset(self, raw_offset: float) -> None:
        """Offset added to raw_force()."""
        self._force_offset = raw_offset

    def hide_marker(self, index: int | None) -> None:
        """Stop drawing one marker; None restores all. IndexError if out of range."""
        if index is not None and not 0 <= index < len(self.config.marker_xs):
            raise IndexError(f"no marker {index}")
        self._hidden = index

    def fail(self, device: str | None) -> None:
        """Make "stage", "load cell" or "camera" fail; None clears the failure."""
        self._failed = device

    def failed(self, device: str) -> bool:
        """True if the named device is set to fail."""
        return self._failed == device


# --- sim_stage -----------------------------------------------------------------


class SimStage(Stage):
    """Stage adapter over SimRig."""

    def __init__(self, rig: SimRig) -> None:
        self._rig = rig
        self._connected = False
        self._stop = threading.Event()

    def connect(self, port: str) -> None:
        if port != "SIM":
            raise StageError(f"no stage on port {port!r}")
        self._connected = True
        self._check()

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _check(self) -> None:
        if self._rig.failed("stage"):
            self._connected = False
            raise StageError("simulated failure")
        if not self._connected:
            raise StageError("not connected")

    def position_mm(self) -> float:
        self._check()
        return self._rig.position_mm()

    def move_relative(self, delta_mm: float) -> None:
        self.move_absolute(self.position_mm() + delta_mm)

    def move_absolute(self, target_mm: float) -> None:
        self._check()
        c = self._rig.config
        if not c.travel_min_mm <= target_mm <= c.travel_max_mm:
            raise StageError("target out of travel")
        self._stop.clear()
        step = c.stage_speed_mm_s * 0.01
        while (position := self._rig.position_mm()) != target_mm:
            if self._stop.wait(0.01):  # the 10 ms slice; ends early when stop() is called
                raise MotionInterrupted("move stopped")
            self._check()
            remaining = target_mm - position
            self._rig.set_position_mm(
                target_mm if abs(remaining) <= step else position + math.copysign(step, remaining)
            )

    def stop(self) -> None:
        self._stop.set()


# --- sim_loadcell --------------------------------------------------------------


class SimLoadCell(LoadCell):
    """Load-cell adapter over SimRig."""

    def __init__(self, rig: SimRig) -> None:
        self._rig = rig
        self._connected = False

    def connect(self) -> None:
        if self._rig.failed("load cell"):
            raise LoadCellError("no FUTEK device detected")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def read_raw(self) -> float:
        if self._rig.failed("load cell"):
            self._connected = False
            raise LoadCellError("simulated failure")
        if not self._connected:
            raise LoadCellError("not connected")
        return self._rig.raw_force()


# --- sim_camera ----------------------------------------------------------------


class SimCamera(Camera):
    """Camera adapter over SimRig."""

    def __init__(self, rig: SimRig) -> None:
        self._rig = rig
        self._connected = False
        self._last = 0.0  # timestamp of the last frame returned

    def connect(self, camera_id: str) -> None:
        if camera_id != "SIM-CAM" or self._rig.failed("camera"):
            raise CameraError(f"camera {camera_id!r} not found")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def grab_latest(self, timeout_s: float = 1.0) -> Frame:
        if self._rig.failed("camera"):
            self._connected = False
            raise CameraError("simulated failure")
        if not self._connected:
            raise CameraError("not connected")
        # The next frame exists one frame period after the last one returned.
        wait = self._last + 1 / self._rig.config.frame_rate - time.perf_counter()
        if wait > timeout_s:
            time.sleep(timeout_s)
            raise CameraTimeout("no frame within the timeout")
        time.sleep(max(wait, 0.0))
        self._last = time.perf_counter()
        return Frame(self._rig.render_frame(), self._last)
