"""Value types, operator settings, rig constants and error types.

No behaviour beyond validation and small derived properties. Design: docs/02_design.md §3.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import isfinite
from typing import Any, Self

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Enums -------------------------------------------------------------------


class Mode(Enum):
    """Application mode held by the controller."""

    DISCONNECTED = "disconnected"
    IDLE = "idle"
    MOVING = "moving"
    IMAGE_SETUP = "image_setup"
    PRECONDITIONING = "preconditioning"
    TESTING = "testing"
    RESULT_PENDING = "result_pending"
    OVERLOAD = "overload"


class TestPhase(Enum):
    """Phase of a test run."""

    LOADING = "loading"
    UNLOADING = "unloading"
    PAUSED = "paused"


class PrecondPhase(Enum):
    """Phase of a preconditioning run."""

    SEEK_FIRST_LIMIT = "seek_first"
    SEEK_SECOND_LIMIT = "seek_second"
    CYCLING = "cycling"


class EndReason(Enum):
    """Why a workflow ended."""

    COMPLETED = "completed"
    STOPPED = "stopped"
    OVERLOAD = "overload"
    FAULT = "fault"


class FitSubset(Enum):
    """Which markers enter the curvature fit."""

    ALL = "all"
    EXCLUDE_OUTER = "exclude_outer"


# --- Runtime value types -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class Roi:
    """Region of interest in px on the processed image; right and bottom exclusive.

    Valid: 0 <= left < right, 0 <= top < bottom, width >= 20, height >= 20.
    """

    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if (
            self.left < 0
            or self.top < 0
            or self.right - self.left < 20
            or self.bottom - self.top < 20
        ):
            raise ValueError(f"invalid ROI: {self}")


@dataclass(frozen=True, slots=True)
class Frame:
    """One camera frame: uint8 image of shape (H, W) and its monotonic timestamp."""

    image: npt.NDArray[np.uint8]
    timestamp: float


@dataclass(frozen=True, slots=True)
class Marker:
    """One detected marker: centroid in px and area in px²."""

    x: float
    y: float
    area: int


@dataclass(frozen=True, slots=True)
class MarkerSet:
    """Markers of one frame in ascending x, with the expected count."""

    markers: tuple[Marker, ...]
    expected: int
    timestamp: float

    @property
    def valid(self) -> bool:
        """len(markers) == expected."""
        return len(self.markers) == self.expected

    @property
    def xs(self) -> tuple[float, ...]:
        """Marker x coordinates, left to right."""
        return tuple(m.x for m in self.markers)

    @property
    def ys(self) -> tuple[float, ...]:
        """Marker y coordinates, in the same order as xs."""
        return tuple(m.y for m in self.markers)


@dataclass(frozen=True, slots=True)
class ForceReading:
    """One raw load-cell reading and its monotonic timestamp."""

    raw: float
    timestamp: float


@dataclass(frozen=True, slots=True)
class TareState:
    """Tare references. None means not tared."""

    k_ref: float | None = None
    f_ref: float | None = None
    overridden: bool = False

    @property
    def k_set(self) -> bool:
        """True if the curvature reference is set."""
        return self.k_ref is not None

    @property
    def f_set(self) -> bool:
        """True if the force reference is set."""
        return self.f_ref is not None


@dataclass(frozen=True, slots=True)
class Measurement:
    """One frame's markers, curvature and dK paired with the nearest force reading."""

    markers: MarkerSet | None
    curvature: float | None
    dk: float | None
    force_raw: float
    force: float
    frame_time: float
    force_time: float

    @property
    def usable(self) -> bool:
        """Valid markers and dK, with finite force paired within 100 ms."""
        return (
            self.markers is not None
            and self.markers.valid
            and self.dk is not None
            and isfinite(self.force)
            and abs(self.frame_time - self.force_time) <= 0.1
        )


@dataclass(frozen=True, slots=True)
class Sample:
    """One recorded row of a test."""

    xs: tuple[float, ...]
    ys: tuple[float, ...]
    dk: float
    force: float
    t: float
    stage_mm: float


# --- Settings ----------------------------------------------------------------


class Settings(BaseModel):
    """Operator-editable settings, persisted by storage.save_settings."""

    model_config = ConfigDict(frozen=True)

    stage_port: str = ""
    camera_id: str = ""
    roi: Roi | None = None
    threshold_lower: int = Field(2, ge=0, le=255)
    threshold_upper: int = Field(60, ge=0, le=255)
    invert: bool = False
    min_area_px: int = Field(200, ge=1, le=10_000_000)
    max_area_px: int = Field(15000, ge=1, le=10_000_000)
    marker_count: int = Field(6, ge=3, le=50)
    tracking: bool = False
    resolution_mm_per_px: float = Field(0.0111, gt=0, le=10)
    fit_subset: FitSubset = FitSubset.ALL
    jog_step_mm: float = Field(0.0612244898, gt=0, le=5)
    test_step_mm: float = Field(0.0477, gt=0, le=5)
    settle_s: float = Field(0.4, ge=0.05, le=10)
    k_limit: float = Field(0.12, ge=-5, le=5)
    pc_amplitude: float = Field(0.0, ge=0, le=5)
    pc_cycles: int = Field(1, ge=1, le=1000)
    pc_dwell_s: float = Field(3.0, ge=0, le=60)

    @model_validator(mode="after")
    def _check_cross_fields(self) -> Self:
        if self.threshold_lower > self.threshold_upper:
            raise ValueError("threshold_lower must not exceed threshold_upper")
        if self.min_area_px > self.max_area_px:
            raise ValueError("min_area_px must not exceed max_area_px")
        if self.fit_subset is FitSubset.EXCLUDE_OUTER and self.marker_count < 5:
            raise ValueError("excluding the outer markers needs at least 5 markers")
        if self.k_limit == 0:
            raise ValueError("k_limit must be non-zero")
        return self

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy with changes, re-validated (pydantic's own model_copy does not validate)."""
        return self.model_validate({**dict(self), **(update or {})})


# --- Rig constants (confirmed on the rig; change here, not at run time) --------------

OVERLOAD_LIMIT_G = 25.0  # limit on the absolute untared load-cell reading (FR-100, FR-102)
FORCE_SIGN = -1  # displayed force = FORCE_SIGN * raw: positive while the sample pushes the probe
STAGE_UP_SIGN = -1  # sign of delta_mm that moves the sample up, toward the stage's home
LOADING_STAGE_SIGN = -1  # sign of delta_mm that loads the sample: dK more positive (up)
CURVATURE_SIGN = -1  # curvature = CURVATURE_SIGN * image fit: positive while loaded, like force
FORCE_PERIOD_S = 0.02  # load-cell polling period
ZABER_BAUD = 115200
ZABER_DEVICE_ADDRESS = 1
ZABER_AXIS = 1
FUTEK_DLL = "FUTEK.Devices.dll"  # relative paths resolve against the install folder


# --- Test record and metadata ----------------------------------------------


@dataclass(slots=True)
class TestRecord:
    """Samples and context of one test. Mutable only while recording."""

    samples: list[Sample]
    started: datetime
    settings: Settings
    tare: TareState
    start_position_mm: float
    end_reason: EndReason | None = None


class TestMetadata(BaseModel):
    """Content of the per-test metadata file (IR-011)."""

    start_date: str
    start_time: str
    marker_count: int
    resolution_mm_per_px: float
    k_limit: float
    test_step_mm: float
    settle_s: float
    fit_subset: str
    k_ref: float | None
    f_ref: float | None
    tare_overridden: bool
    end_reason: str
    sample_count: int
    start_position_mm: float
    software_version: str

    @classmethod
    def from_record(cls, record: TestRecord, software_version: str) -> TestMetadata:
        """Build the metadata of a finished record."""
        if record.end_reason is None:
            raise ValueError("record not finished")
        s = record.settings
        return cls(
            start_date=record.started.strftime("%Y-%m-%d"),
            start_time=record.started.strftime("%H:%M:%S"),
            marker_count=s.marker_count,
            resolution_mm_per_px=s.resolution_mm_per_px,
            k_limit=s.k_limit,
            test_step_mm=s.test_step_mm,
            settle_s=s.settle_s,
            fit_subset=s.fit_subset.value,
            k_ref=record.tare.k_ref,
            f_ref=record.tare.f_ref,
            tare_overridden=record.tare.overridden,
            end_reason=record.end_reason.value,
            sample_count=len(record.samples),
            start_position_mm=record.start_position_mm,
            software_version=software_version,
        )


# --- Errors: every device failure names its device (FR-002, FR-103) --------------


class RigError(Exception):
    """Base for all application errors."""


class DeviceError(RigError):
    """A device failed. str(e) == f"{device}: {message}"."""

    device: str  # "stage", "load cell" or "camera" (exact strings shown to the operator)

    def __init__(self, device: str, message: str) -> None:
        super().__init__(f"{device}: {message}")
        self.device = device


class StageError(DeviceError):
    """Stage failure; device == "stage"."""

    def __init__(self, message: str) -> None:
        super().__init__("stage", message)


class MotionInterrupted(StageError):
    """A blocking move ended early because stop() was called."""


class LoadCellError(DeviceError):
    """Load-cell failure; device == "load cell"."""

    def __init__(self, message: str) -> None:
        super().__init__("load cell", message)


class CameraError(DeviceError):
    """Camera failure; device == "camera"."""

    def __init__(self, message: str) -> None:
        super().__init__("camera", message)


class CameraTimeout(CameraError):
    """No frame arrived within the timeout."""


class SaveError(RigError):
    """A file could not be written."""

    path: str

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
