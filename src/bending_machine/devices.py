"""Device interfaces, the real drivers and device discovery.

Stage, LoadCell and Camera are the interfaces machine.Machine works against; sim.py
holds the simulated implementations. Vendor libraries (zaber_motion, clr/pythonnet,
vmbpy) are imported inside connect/discovery, never at module import, so simulated
mode works without them. Design: docs/02_design.md §6.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from bending_machine.models import (
    FUTEK_DLL,
    ZABER_AXIS,
    ZABER_BAUD,
    ZABER_DEVICE_ADDRESS,
    CameraError,
    CameraTimeout,
    Frame,
    LoadCellError,
    MotionInterrupted,
    StageError,
)

_log = logging.getLogger(__name__)


class Stage(ABC):
    """Motorised stage. Positions and distances in mm."""

    @abstractmethod
    def connect(self, port: str) -> None:
        """Open the port and verify one stage answers.

        Idempotent if already connected to the same port.
        Raises StageError (port missing, no device, wrong baud).
        """
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Release the port. Never raises; safe if not connected."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the device is connected."""
        raise NotImplementedError

    @abstractmethod
    def position_mm(self) -> float:
        """Current position in mm. Raises StageError."""
        raise NotImplementedError

    @abstractmethod
    def move_relative(self, delta_mm: float) -> None:
        """Move by delta_mm and return when the stage is idle.

        Raises MotionInterrupted if stop() ended the move; StageError otherwise
        (not connected, out of travel, communication failure).
        """
        raise NotImplementedError

    @abstractmethod
    def move_absolute(self, target_mm: float) -> None:
        """Move to target_mm and return when idle. Same errors as move_relative."""
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        """Stop motion now.

        Callable from ANY thread, including while another thread is blocked in a
        move. Returns within 0.2 s. Never raises; logs failures.
        """
        raise NotImplementedError


class LoadCell(ABC):
    """Load cell. Readings in device units."""

    @abstractmethod
    def connect(self) -> None:
        """Attach to the first detected device. Raises LoadCellError if none."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Release the device. Never raises."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the device is connected."""
        raise NotImplementedError

    @abstractmethod
    def read_raw(self) -> float:
        """Channel 0 reading in device units, untouched (no sign change, no tare).

        Returns within 0.1 s. Raises LoadCellError.
        """
        raise NotImplementedError


class Camera(ABC):
    """Camera delivering 8-bit monochrome frames."""

    @abstractmethod
    def connect(self, camera_id: str) -> None:
        """Open the camera and start continuous acquisition. Raises CameraError."""
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        """Stop acquisition and release the camera. Never raises."""
        raise NotImplementedError

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the device is connected."""
        raise NotImplementedError

    @abstractmethod
    def grab_latest(self, timeout_s: float = 1.0) -> Frame:
        """Newest complete frame as uint8 (H, W) as delivered by the camera.

        Frame.timestamp = time.perf_counter() at reception. Never returns the same
        frame twice: blocks until a frame newer than the last returned one exists.
        Raises CameraTimeout after timeout_s; CameraError on other failures.
        """
        raise NotImplementedError


# --- Real drivers --------------------------------------------------------------


class ZaberStage(Stage):
    """Stage over zaber-motion, ASCII protocol."""

    def __init__(self) -> None:
        self._connection: Any = None
        self._axis: Any = None
        self._mm: Any = None  # zaber_motion.Units.LENGTH_MILLIMETRES

    def connect(self, port: str) -> None:
        if self._axis is not None:
            return
        try:
            from zaber_motion import Units
            from zaber_motion.ascii import Connection

            connection = Connection.open_serial_port(port, baud_rate=ZABER_BAUD)
            try:
                connection.detect_devices()
                axis = connection.get_device(ZABER_DEVICE_ADDRESS).get_axis(ZABER_AXIS)
                axis.get_position(Units.LENGTH_MILLIMETRES)
            except Exception:
                connection.close()
                raise
        except Exception as error:  # a missing library included
            raise StageError(f"no Zaber stage on {port!r}: {error}") from error
        self._connection, self._axis, self._mm = connection, axis, Units.LENGTH_MILLIMETRES

    def disconnect(self) -> None:
        connection, self._connection, self._axis = self._connection, None, None
        try:
            if connection is not None:
                connection.close()
        except Exception:
            _log.warning("closing the stage port failed", exc_info=True)

    @property
    def is_connected(self) -> bool:
        return self._axis is not None

    def _call(self, method: str, *args: Any) -> Any:
        """One axis call, with the library's exceptions turned into ours."""
        axis = self._axis
        if axis is None:
            raise StageError("not connected")
        try:
            return getattr(axis, method)(*args)
        except Exception as error:
            name = type(error).__name__
            if name == "MovementInterruptedException":  # stop() ended a blocking move
                raise MotionInterrupted(str(error)) from error
            if name in ("ConnectionClosedException", "ConnectionFailedException"):
                self.disconnect()  # the stage is gone; a refused move leaves it connected
            raise StageError(str(error)) from error

    def position_mm(self) -> float:
        return float(self._call("get_position", self._mm))

    def move_relative(self, delta_mm: float) -> None:
        self._call("move_relative", delta_mm, self._mm, True)  # True: wait until idle

    def move_absolute(self, target_mm: float) -> None:
        self._call("move_absolute", target_mm, self._mm, True)

    def stop(self) -> None:
        try:
            if self._axis is not None:
                self._axis.stop(wait_until_idle=False)
        except Exception:
            _log.warning("stopping the stage failed", exc_info=True)


class FutekLoadCell(LoadCell):
    """Load cell over FUTEK.Devices.dll."""

    def __init__(self) -> None:
        self._repository: Any = None
        self._device: Any = None

    def connect(self) -> None:
        if self._device is not None:
            return
        dll = Path(__file__).parent / FUTEK_DLL  # an absolute FUTEK_DLL replaces the folder
        try:
            from pythonnet import load

            load("netfx")  # .NET Framework, which the net48 assembly needs; once per process
            import clr

            clr.AddReference(str(dll))
            from FUTEK.Devices import DeviceRepository, DeviceUSB225
        except Exception as error:
            raise LoadCellError(f"cannot load {dll}: {error}") from error
        try:
            repository = DeviceRepository()
            found = repository.DetectDevices()
            if found.Count == 0:
                raise LoadCellError("no FUTEK device detected")
            if not isinstance(found[0], DeviceUSB225):
                raise LoadCellError(f"unexpected device type {type(found[0]).__name__}")
        except LoadCellError:
            raise
        except Exception as error:
            raise LoadCellError(str(error)) from error
        self._repository, self._device = repository, found[0]

    def disconnect(self) -> None:
        repository, self._repository, self._device = self._repository, None, None
        try:
            if repository is not None:
                repository.DisconnectAllDevices()
        except Exception:
            _log.warning("disconnecting the load cell failed", exc_info=True)

    @property
    def is_connected(self) -> bool:
        return self._device is not None

    def read_raw(self) -> float:
        if self._device is None:
            raise LoadCellError("not connected")
        try:
            return float(self._device.GetChannelXReading(0))
        except Exception as error:  # typically: unplugged
            self._device = None
            raise LoadCellError(str(error)) from error


MONO_SHIFT = {"Mono8": 0, "Mono10": 2, "Mono12": 4, "Mono16": 8}  # bits to drop for 8-bit


def to_uint8(pixels: npt.NDArray[Any], shift: int) -> npt.NDArray[np.uint8]:
    """A copy of a (H, W) or (H, W, 1) mono frame as 2-D uint8, dropping `shift` low bits."""
    return (pixels.reshape(pixels.shape[:2]) >> shift).astype(np.uint8)


class VimbaCamera(Camera):
    """Camera over vmbpy and the Vimba X GigE transport layer."""

    def __init__(self) -> None:
        self._system: Any = None
        self._camera: Any = None
        self._complete: Any = None  # vmbpy.FrameStatus.Complete
        self._shift = 0
        self._ready = threading.Condition()  # guards _latest, written by the vmbpy thread
        self._latest: Frame | None = None
        self._returned = 0.0  # timestamp of the last frame handed out

    def connect(self, camera_id: str) -> None:
        if self._camera is not None:
            return
        try:
            import vmbpy
        except ImportError as error:
            raise CameraError(f"Vimba X / vmbpy not available: {error}") from error
        try:
            self._system = vmbpy.VmbSystem.get_instance()
            self._system.__enter__()
            camera = self._system.get_camera_by_id(camera_id)
            camera.__enter__()
            self._camera = camera
            pixel_format = camera.get_pixel_format().name
            if pixel_format not in MONO_SHIFT:
                raise CameraError(f"unsupported pixel format {pixel_format}")
            self._shift, self._complete = MONO_SHIFT[pixel_format], vmbpy.FrameStatus.Complete
            try:  # GigE only: fit the packet size to the network link
                adjust = camera.get_streams()[0].GVSPAdjustPacketSize
                adjust.run()
                while not adjust.is_done():
                    time.sleep(0.01)
            except (AttributeError, vmbpy.VmbFeatureError):
                pass
            with self._ready:
                self._latest = None
            camera.start_streaming(self._on_frame, buffer_count=3)
        except Exception as error:
            self.disconnect()
            if isinstance(error, CameraError):
                raise
            raise CameraError(f"cannot open camera {camera_id!r}: {error}") from error

    def _on_frame(self, camera: Any, stream: Any, frame: Any) -> None:
        """Runs on a vmbpy thread: keep only the newest complete frame."""
        if frame.get_status() == self._complete:
            image = to_uint8(frame.as_numpy_ndarray(), self._shift)
            with self._ready:
                self._latest = Frame(image, time.perf_counter())
                self._ready.notify_all()
        camera.queue_frame(frame)

    def disconnect(self) -> None:
        camera, system, self._camera, self._system = self._camera, self._system, None, None
        nothing = (None, None, None)
        for target, method, args in (
            (camera, "stop_streaming", ()),
            (camera, "__exit__", nothing),
            (system, "__exit__", nothing),
        ):
            try:
                if target is not None:
                    getattr(target, method)(*args)
            except Exception:
                _log.warning("closing the camera failed", exc_info=True)

    @property
    def is_connected(self) -> bool:
        return self._camera is not None

    def grab_latest(self, timeout_s: float = 1.0) -> Frame:
        if self._camera is None:
            raise CameraError("not connected")
        with self._ready:
            self._ready.wait_for(
                lambda: self._latest is not None and self._latest.timestamp > self._returned,
                timeout_s,
            )
            frame = self._latest
        if frame is None or frame.timestamp <= self._returned:
            raise CameraTimeout(f"no frame within {timeout_s:g} s")
        self._returned = frame.timestamp
        return frame


# --- Discovery -----------------------------------------------------------------


def list_stage_ports(simulated: bool) -> list[str]:
    """Serial port names, sorted; ["SIM"] when simulated. Never raises ([] on failure)."""
    if simulated:
        return ["SIM"]
    try:
        from zaber_motion import Tools

        return sorted(Tools.list_serial_ports())
    except Exception as error:
        _log.warning("listing serial ports failed: %s", error)
        return []


def list_cameras(simulated: bool) -> list[str]:
    """Camera ids, sorted; ["SIM-CAM"] when simulated. Never raises ([] on failure)."""
    if simulated:
        return ["SIM-CAM"]
    try:
        import vmbpy

        with vmbpy.VmbSystem.get_instance() as system:
            return sorted(camera.get_id() for camera in system.get_all_cameras())
    except Exception as error:
        _log.warning("listing cameras failed: %s", error)
        return []
