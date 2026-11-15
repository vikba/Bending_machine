"""Bench tests: need the real rig. Run with `uv run pytest -m bench` and no sample mounted.

Set BENCH_STAGE_PORT (for example COM3) and BENCH_CAMERA_ID before running.
"""

import os
import threading
import time
from collections.abc import Iterator
from itertools import pairwise

import pytest

from bending_machine.devices import FutekLoadCell, VimbaCamera, ZaberStage
from bending_machine.models import FORCE_PERIOD_S, MotionInterrupted

pytestmark = pytest.mark.bench


@pytest.fixture
def stage() -> Iterator[ZaberStage]:
    made = ZaberStage()
    made.connect(os.environ["BENCH_STAGE_PORT"])
    yield made
    made.disconnect()


def test_stage_relative_moves(stage: ZaberStage) -> None:
    start = stage.position_mm()
    for _ in range(10):
        stage.move_relative(0.05)  # away from home: down
    arrived = stage.position_mm()
    assert arrived == pytest.approx(start + 0.5, abs=0.002)
    time.sleep(0.05)
    assert stage.position_mm() == arrived  # the move had really finished
    stage.move_absolute(start)
    assert stage.position_mm() == pytest.approx(start, abs=0.002)


def test_stage_stop_from_a_second_thread(stage: ZaberStage) -> None:
    start = stage.position_mm()
    threading.Timer(0.5, stage.stop).start()
    began = time.perf_counter()
    with pytest.raises(MotionInterrupted):
        stage.move_relative(20.0)
    assert time.perf_counter() - began < 1.0
    assert stage.position_mm() < start + 20.0
    stage.move_absolute(start)


def test_loadcell_keeps_the_20_ms_period() -> None:
    cell = FutekLoadCell()
    cell.connect()
    try:
        began = time.perf_counter()
        readings = [cell.read_raw() for _ in range(200)]
        per_read = (time.perf_counter() - began) / len(readings)
    finally:
        cell.disconnect()
    assert per_read < FORCE_PERIOD_S
    assert all(abs(raw) < 25 for raw in readings)  # nothing on the probe


def test_camera_rate_and_newest_frame() -> None:
    camera = VimbaCamera()
    camera.connect(os.environ["BENCH_CAMERA_ID"])
    try:
        began = time.perf_counter()
        frames = [camera.grab_latest() for _ in range(100)]
        rate = len(frames) / (time.perf_counter() - began)
        assert rate >= 15
        assert len({frame.image.shape for frame in frames}) == 1 and frames[0].image.ndim == 2
        assert all(b.timestamp > a.timestamp for a, b in pairwise(frames))
        time.sleep(1.0)  # frames keep arriving; the next grab must return the newest one
        assert time.perf_counter() - camera.grab_latest().timestamp < 0.2
    finally:
        camera.disconnect()
