"""The real drivers and discovery, as far as they can run without the rig (design §6)."""

import sys

import numpy as np
import pytest

from bending_machine import devices
from bending_machine.devices import FutekLoadCell, VimbaCamera, ZaberStage, to_uint8
from bending_machine.models import CameraError, LoadCellError, StageError


@pytest.fixture
def no_vendor_libraries(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("zaber_motion", "zaber_motion.ascii", "pythonnet", "clr", "vmbpy"):
        monkeypatch.setitem(sys.modules, name, None)  # importing it now raises ImportError


def test_connect_without_the_vendor_library_names_the_device(no_vendor_libraries: None) -> None:
    stage, cell, camera = ZaberStage(), FutekLoadCell(), VimbaCamera()
    with pytest.raises(StageError, match=r"^stage: no Zaber stage on 'COM3'"):
        stage.connect("COM3")
    with pytest.raises(LoadCellError, match=r"^load cell: cannot load .*FUTEK\.Devices\.dll"):
        cell.connect()
    with pytest.raises(CameraError, match=r"^camera: Vimba X / vmbpy not available"):
        camera.connect("DEV_1")
    assert not (stage.is_connected or cell.is_connected or camera.is_connected)


def test_unconnected_drivers_fail_cleanly() -> None:
    stage, cell, camera = ZaberStage(), FutekLoadCell(), VimbaCamera()
    with pytest.raises(StageError, match="not connected"):
        stage.position_mm()
    with pytest.raises(StageError, match="not connected"):
        stage.move_relative(1.0)
    with pytest.raises(LoadCellError, match="not connected"):
        cell.read_raw()
    with pytest.raises(CameraError, match="not connected"):
        camera.grab_latest(0.01)
    for device in (stage, cell, camera):
        device.disconnect()  # never raises
    stage.stop()


def test_stage_on_a_missing_port() -> None:
    with pytest.raises(StageError, match="no Zaber stage on 'NO-SUCH-PORT'"):
        ZaberStage().connect("NO-SUCH-PORT")


def test_discovery_returns_nothing_on_failure(
    no_vendor_libraries: None, caplog: pytest.LogCaptureFixture
) -> None:
    assert devices.list_stage_ports(False) == []
    assert devices.list_cameras(False) == []
    assert "listing serial ports failed" in caplog.text and "listing cameras failed" in caplog.text
    assert devices.list_stage_ports(True) == ["SIM"]
    assert devices.list_cameras(True) == ["SIM-CAM"]


@pytest.mark.parametrize(("name", "top"), [("Mono8", 255), ("Mono10", 1023), ("Mono12", 4095)])
def test_frame_conversion_to_8_bit(name: str, top: int) -> None:
    dtype = np.uint8 if name == "Mono8" else np.uint16
    pixels = np.array([[0, top // 2], [top, 1]], dtype).reshape(2, 2, 1)  # as vmbpy delivers
    image = to_uint8(pixels, devices.MONO_SHIFT[name])
    assert image.dtype == np.uint8 and image.shape == (2, 2)
    assert image.tolist() == [[0, 127], [255, 0 if top > 255 else 1]]
    image[0, 0] = 9
    assert pixels[0, 0, 0] == 0  # a copy: vmbpy reuses its buffer
