"""Unit tests for the simulated rig and its drivers (docs/02_design.md §7)."""

import threading
import time

import pytest

from bending_machine import vision
from bending_machine.models import (
    FORCE_SIGN,
    CameraError,
    CameraTimeout,
    LoadCellError,
    MotionInterrupted,
    Settings,
    StageError,
)
from bending_machine.sim import SimCamera, SimLoadCell, SimRig, SimRigConfig, SimStage


def connected_stage(rig: SimRig) -> SimStage:
    stage = SimStage(rig)
    stage.connect("SIM")
    return stage


def measure(rig: SimRig) -> tuple[int, float]:
    """Marker count and curvature of one rendered frame, with the default settings."""
    s = Settings()
    binary = vision.binarise(
        vision.prepare(rig.render_frame()), s.threshold_lower, s.threshold_upper, s.invert
    )
    found = vision.find_markers(binary, None, s.min_area_px, s.max_area_px, s.marker_count, 0.0)
    xs = [m.x for m in found.markers]
    ys = [m.y for m in found.markers]
    return len(xs), vision.compute_curvature(xs, ys, s.resolution_mm_per_px) if len(xs) > 2 else 0


def test_frame_gives_six_markers_and_the_model_curvature() -> None:
    rig = SimRig()
    count, curvature = measure(rig)
    assert count == 6
    assert curvature == pytest.approx(0.0, abs=5e-4)
    rig.set_position_mm(10.0 - 1.5)  # 1.5 mm up
    count, curvature = measure(rig)
    assert count == 6
    assert curvature == pytest.approx(-0.12, abs=3e-3)
    assert rig.curvature() == pytest.approx(0.12)  # the image fit has the opposite sign


def test_hidden_marker_is_not_drawn() -> None:
    rig = SimRig()
    rig.hide_marker(2)
    assert measure(rig)[0] == 5
    rig.hide_marker(None)
    assert measure(rig)[0] == 6
    with pytest.raises(IndexError):
        rig.hide_marker(6)


def test_move_blocks_until_the_target_is_reached() -> None:
    stage = connected_stage(SimRig())  # 1 mm/s
    start = time.perf_counter()
    stage.move_relative(-0.1)
    assert time.perf_counter() - start >= 0.09
    assert stage.position_mm() == pytest.approx(9.9)
    stage.move_absolute(10.0)
    assert stage.position_mm() == 10.0


def test_stop_from_another_thread_interrupts_a_move() -> None:
    stage = connected_stage(SimRig())
    threading.Timer(0.1, stage.stop).start()
    start = time.perf_counter()
    with pytest.raises(MotionInterrupted):
        stage.move_absolute(0.0)  # 10 s if not stopped
    assert time.perf_counter() - start < 0.3
    assert 9.8 < stage.position_mm() < 10.0
    stage.move_relative(0.02)  # an earlier stop does not affect the next move


def test_target_out_of_travel_is_refused_without_moving() -> None:
    stage = connected_stage(SimRig())
    for target in (-0.1, 25.1):
        with pytest.raises(StageError, match="target out of travel"):
            stage.move_absolute(target)
    assert stage.position_mm() == 10.0


def test_force_is_positive_on_display_when_loaded_and_follows_the_offset() -> None:
    rig = SimRig(SimRigConfig(force_noise=0.0))
    cell = SimLoadCell(rig)
    cell.connect()
    assert cell.read_raw() == 0.0
    rig.set_position_mm(8.5)  # up: dK = 0.12
    assert vision.to_display(cell.read_raw(), FORCE_SIGN) == pytest.approx(0.42)
    rig.set_force_offset(-30.0)
    assert cell.read_raw() == pytest.approx(-30.42)


def test_camera_never_returns_the_same_frame_twice() -> None:
    rig = SimRig()
    camera = SimCamera(rig)
    camera.connect("SIM-CAM")
    first = camera.grab_latest()
    rig.set_position_mm(8.5)
    second = camera.grab_latest()
    assert first.image.shape == (960, 1280)
    assert second.timestamp - first.timestamp >= 0.05  # one frame period at 17.78 fps
    assert (first.image != second.image).any()  # rendered at grab time, not queued
    with pytest.raises(CameraTimeout):
        camera.grab_latest(timeout_s=0.001)


def test_wrong_ids_and_unconnected_devices_raise() -> None:
    rig = SimRig()
    with pytest.raises(StageError):
        SimStage(rig).connect("COM3")
    with pytest.raises(CameraError):
        SimCamera(rig).connect("DEV_1")
    with pytest.raises(StageError):
        SimStage(rig).position_mm()
    with pytest.raises(LoadCellError):
        SimLoadCell(rig).read_raw()
    with pytest.raises(CameraError):
        SimCamera(rig).grab_latest()


def test_failed_device_raises_its_error_until_cleared() -> None:
    rig = SimRig()
    stage, cell, camera = connected_stage(rig), SimLoadCell(rig), SimCamera(rig)
    cell.connect()
    camera.connect("SIM-CAM")

    rig.fail("load cell")
    with pytest.raises(LoadCellError):
        cell.read_raw()
    assert not cell.is_connected
    camera.grab_latest()  # the other devices keep working

    rig.fail("camera")
    with pytest.raises(CameraError):
        camera.grab_latest()

    threading.Timer(0.05, rig.fail, ["stage"]).start()
    with pytest.raises(StageError) as lost:
        stage.move_absolute(0.0)  # lost in the middle of a move
    assert not isinstance(lost.value, MotionInterrupted)
    assert str(lost.value).startswith("stage: ")

    rig.fail(None)
    stage.connect("SIM")
    stage.move_relative(0.01)
