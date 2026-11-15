"""Workflows of Machine against the simulated rig (docs/02_design.md §9, §12)."""

import json
import time
from collections.abc import Callable, Iterator
from itertools import pairwise
from math import isfinite, nan
from pathlib import Path

import pytest

from bending_machine.devices import LoadCell
from bending_machine.machine import Machine, Message, Status
from bending_machine.models import Mode, Settings
from bending_machine.sim import SimCamera, SimLoadCell, SimRig, SimStage

# Small, fast workflows: the limit is 3 steps away, the amplitude 2 steps.
FAST = {
    "tracking": True,
    "test_step_mm": 0.05,
    "jog_step_mm": 0.05,
    "settle_s": 0.05,
    "k_limit": 0.012,
    "pc_amplitude": 0.006,
    "pc_cycles": 3,
    "pc_dwell_s": 0.05,
}
LONG = {"k_limit": 0.024}  # 6 steps to the limit: time to interfere mid-test


class FakeLoadCell(LoadCell):
    """Load cell returning a value the test sets, optionally slowly."""

    def __init__(self) -> None:
        self.value = 0.0
        self.delay = 0.0

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    @property
    def is_connected(self) -> bool:
        return True

    def read_raw(self) -> float:
        time.sleep(self.delay)
        return self.value


class Bench:
    """A connected machine on a simulated rig, with what it reported so far."""

    def __init__(
        self, folder: Path, loadcell: LoadCell | None, settings: dict[str, object]
    ) -> None:
        self.rig = SimRig()
        self.machine = Machine(
            SimStage(self.rig),
            loadcell or SimLoadCell(self.rig),
            SimCamera(self.rig),
            Settings.model_validate({**FAST, **settings}),
            folder,
        )
        self.seen: list[Message] = []
        self.positions: list[float] = []  # each distinct position the stage rested at
        self.phases: list[str] = []
        self.connect()

    def wait(self, done: Callable[[Status], object], timeout: float = 10.0) -> Status:
        end = time.monotonic() + timeout
        while True:
            self.seen += self.machine.messages()
            status = self.machine.status()
            position = status.position_mm
            if position is not None and (not self.positions or position != self.positions[-1]):
                self.positions.append(position)
            if status.phase and (not self.phases or status.phase != self.phases[-1]):
                self.phases.append(status.phase)
            if done(status):
                return status
            assert time.monotonic() < end, f"timed out in {status.mode}; messages: {self.seen}"
            time.sleep(0.005)

    def said(self, kind: str) -> list[str]:
        return [m.text for m in self.seen if m.kind == kind]

    def connect(self) -> None:
        self.machine.connect("SIM", "SIM-CAM")
        self.wait(lambda s: s.mode is Mode.IDLE and tracked(s))

    def tare(self) -> None:
        self.machine.tare_curvature()
        self.machine.tare_force()
        self.wait(lambda s: s.tare.k_set and s.tare.f_set)

    def start_test(self, samples: int = 0) -> None:
        """Tare, start a test and wait until it has recorded this many samples."""
        self.tare()
        self.machine.start_test()
        self.wait(lambda s: s.mode is Mode.TESTING and s.samples >= samples)


def tracked(status: Status) -> bool:
    m = status.measurement
    return m is not None and m.markers is not None and m.markers.valid and isfinite(m.force)


def mode_is(mode: Mode) -> Callable[[Status], bool]:
    return lambda status: status.mode is mode


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Callable[..., Bench]]:
    benches: list[Bench] = []

    def make(loadcell: LoadCell | None = None, **settings: object) -> Bench:
        benches.append(Bench(tmp_path, loadcell, settings))
        return benches[-1]

    yield make
    for bench in benches:
        bench.machine.shutdown()


def test_full_run_then_failed_save_then_save(make: Callable[..., Bench], tmp_path: Path) -> None:
    b = make()
    b.start_test()
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert b.phases[-3:] == ["loading", "unloading", "completed"]
    assert status.position_mm == pytest.approx(10.0, abs=1e-9)

    record = b.machine._record  # the pending result, as it will be saved
    assert record is not None and len(record.samples) == status.samples
    dks = [s.dk for s in record.samples]
    assert abs(dks[0]) < 0.001 and max(dks) >= 0.012 and dks[-1] <= 0
    assert record.samples[dks.index(max(dks))].force > 0  # same sign as dK when loaded
    steps = [b.stage_mm - a.stage_mm for a, b in pairwise(record.samples)]
    assert all(abs(step) == pytest.approx(0.05, abs=1e-9) for step in steps)
    assert min(s.stage_mm for s in record.samples) == pytest.approx(9.85, abs=1e-9)  # up = loading
    # Fresh frames: every sample is from after the settle time of the step before it.
    assert all(b.t - a.t >= 0.05 for a, b in pairwise(record.samples))
    assert all(s.force > 0 for s in record.samples[1:-1])  # the sample pushes the probe

    b.machine.start_test()  # nothing new can start while a result is pending
    b.machine.save_result(tmp_path / "missing" / "t.lvm", False)
    b.wait(lambda s: b.said("save_failed"))
    assert b.machine.status().mode is Mode.RESULT_PENDING
    assert "a result is waiting" in b.said("rejected")[-1]

    b.machine.save_result(tmp_path / "t.lvm", False)
    b.wait(mode_is(Mode.IDLE))
    assert b.said("saved") == [str(tmp_path / "t.lvm")]
    rows = (tmp_path / "t.lvm").read_bytes().split(b"\r\n")
    assert rows.pop() == b"" and len(rows) == status.samples
    assert all(row.startswith(b"\t") and row.count(b"\t") == 14 for row in rows)
    meta = json.loads((tmp_path / "t.meta.json").read_text())
    assert (meta["end_reason"], meta["sample_count"]) == ("completed", status.samples)


def test_stop_during_a_test(make: Callable[..., Bench]) -> None:
    b = make(**LONG)
    b.start_test(samples=3)
    stopped = time.monotonic()
    b.machine.stop()
    status = b.wait(lambda s: s.phase == "stopped")
    assert time.monotonic() - stopped < 1.0  # one step at most, 0.4 s, and the way back
    assert status.position_mm == pytest.approx(10.0, abs=1e-9)
    assert b.wait(mode_is(Mode.RESULT_PENDING)).samples >= 3


def test_stop_during_a_long_move(make: Callable[..., Bench]) -> None:
    b = make()
    b.machine.move_absolute(20.0)  # 10 s at the simulated 1 mm/s
    b.wait(mode_is(Mode.MOVING))
    time.sleep(0.2)
    stopped = time.monotonic()
    b.machine.stop()
    status = b.wait(mode_is(Mode.IDLE))
    assert time.monotonic() - stopped < 0.2
    assert status.position_mm is not None and 10.1 < status.position_mm < 10.6


def test_marker_hidden_mid_test(make: Callable[..., Bench]) -> None:
    b = make(**LONG)
    b.start_test(samples=2)
    b.rig.hide_marker(2)
    held = b.wait(lambda s: s.marker_warning)
    assert held.phase == "paused"
    time.sleep(0.5)
    later = b.machine.status()
    assert (later.samples, later.position_mm) == (held.samples, held.position_mm)
    b.rig.hide_marker(None)
    assert b.wait(mode_is(Mode.RESULT_PENDING)).phase == "completed"


def test_overload_mid_test(make: Callable[..., Bench]) -> None:
    b = make(**LONG)
    b.start_test(samples=3)
    tripped = time.monotonic()
    b.rig.set_force_offset(30.0)
    b.wait(lambda s: b.said("overload"))
    assert time.monotonic() - tripped < 0.25
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert status.phase == "overload" and status.samples >= 3
    time.sleep(0.6)  # longer than the pause before a return move
    position = b.machine.status().position_mm
    assert position is not None and position < 9.95  # where it stopped, not back at 10

    b.machine.discard_result()
    b.wait(mode_is(Mode.OVERLOAD))
    b.machine.jog(+1)
    b.machine.acknowledge_overload()  # refused: the force is still too high
    b.wait(lambda s: len(b.said("rejected")) == 2)
    assert "force limit is exceeded" in b.said("rejected")[0]
    assert b.machine.status().position_mm == position

    b.rig.set_force_offset(0.0)
    time.sleep(0.1)
    b.machine.acknowledge_overload()
    b.wait(mode_is(Mode.IDLE))
    b.machine.jog(+1)
    b.wait(lambda s: s.position_mm == pytest.approx(position - 0.05, abs=1e-9))


@pytest.mark.parametrize("device", ["camera", "stage", "load cell"])
def test_device_lost_mid_test(make: Callable[..., Bench], device: str) -> None:
    b = make(**LONG)
    b.start_test(samples=2)
    b.rig.fail(device)
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert status.phase == "fault" and status.samples >= 2
    assert b.said("fault")[0].startswith(device)
    assert any(error.startswith(device) for error in status.device_errors)
    time.sleep(0.6)
    assert b.rig.position_mm() < 9.99  # no return move

    b.machine.discard_result()
    b.wait(mode_is(Mode.DISCONNECTED))
    b.rig.fail(None)
    b.connect()  # a new Connect brings everything back
    assert b.machine.status().device_errors == ()


def test_stale_force_faults_a_test(make: Callable[..., Bench]) -> None:
    cell = FakeLoadCell()
    b = make(cell, **LONG)
    cell.value = -1.0  # a real load; the simulated curvature still follows the stage
    b.start_test(samples=2)
    cell.delay = 0.5  # readings now arrive too late to pair with a frame
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert status.phase == "fault"
    assert b.said("fault") == ["load cell: no fresh force reading"]
    record = b.machine._record
    assert record is not None and all(isfinite(s.force) for s in record.samples)


def test_preconditioning_three_cycles(make: Callable[..., Bench]) -> None:
    b = make()
    b.tare()
    b.machine.start_precondition()
    b.wait(mode_is(Mode.PRECONDITIONING))
    status = b.wait(mode_is(Mode.IDLE))
    assert b.phases[-4:] == ["seek_first", "seek_second", "cycling", "completed"]
    assert status.cycles_done == 3
    assert b.said("info")[-1] == "Preconditioning ended: completed."
    # Up to the first limit (dK >= 0.006 at 9.9), down to the second (10.1), then
    # straight between exactly those two positions, and back to the start.
    expected = [10.0, 9.95, 9.9, 9.95, 10.0, 10.05, 10.1, 9.9, 10.1, 9.9, 10.1, 10.0]
    assert b.positions == pytest.approx(expected, abs=1e-9)


def test_start_refusals_and_override(make: Callable[..., Bench]) -> None:
    b = make()
    tracking = b.machine.settings

    b.machine.apply_settings(tracking.model_copy(update={"tracking": False}))
    b.machine.start_test()
    b.machine.start_precondition()
    b.wait(lambda s: len(b.said("not_tracking")) == 2)
    assert "To run a test, switch on" in b.said("not_tracking")[0]
    assert "To precondition the sample, switch on" in b.said("not_tracking")[1]

    b.machine.apply_settings(tracking)
    b.rig.hide_marker(0)
    b.wait(lambda s: s.measurement is not None and not tracked(s) and s.measurement.markers)
    b.machine.start_test()
    b.wait(lambda s: b.said("rejected"))
    assert b.said("rejected") == ["Not all markers are detected (found 5 of 6)."]
    b.machine.tare_curvature()
    b.wait(lambda s: len(b.said("rejected")) == 2)

    b.rig.hide_marker(None)
    b.wait(tracked)
    b.machine.start_test()
    b.machine.start_precondition()
    b.wait(lambda s: len(b.said("not_tared")) == 2)
    assert b.said("not_tared") == [
        "Curvature and force must be tared before a test.",
        "Tare curvature before preconditioning.",
    ]
    assert b.positions == [10.0]  # nothing moved so far

    b.machine.start_test(override_tare=True)
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert status.tare.overridden and status.samples > 0


def test_wrong_mode_commands_during_a_test(make: Callable[..., Bench]) -> None:
    b = make()
    b.start_test(samples=1)
    b.machine.jog(+1)
    b.machine.tare_force()
    b.machine.start_precondition()
    b.machine.apply_settings(Settings())
    status = b.wait(mode_is(Mode.RESULT_PENDING))
    assert b.said("rejected") == ["Not available while a test is running."] * 4
    assert status.phase == "completed" and status.position_mm == pytest.approx(10.0, abs=1e-9)
    assert b.machine.settings.tracking


def test_overload_boundaries(make: Callable[..., Bench]) -> None:
    cell = FakeLoadCell()
    b = make(cell)
    for allowed in (25.0, -25.0):  # exactly at the limit is not an overload
        cell.value = allowed
        time.sleep(0.15)
        assert b.machine.status().mode is Mode.IDLE
    for over in (25.01, -25.01):
        cell.value = over
        b.wait(mode_is(Mode.OVERLOAD))
        cell.value = 0.0
        time.sleep(0.1)
        b.machine.acknowledge_overload()
        b.wait(mode_is(Mode.IDLE))
    assert len(b.said("overload")) == 2

    cell.value = nan  # not a reading: three in a row are a load-cell fault
    b.wait(mode_is(Mode.DISCONNECTED))
    assert b.said("fault") == ["load cell: the reading is not a number"]
    assert len(b.said("overload")) == 2


def test_repeated_force_tare_displays_zero(make: Callable[..., Bench]) -> None:
    cell = FakeLoadCell()
    b = make(cell)

    def shows(force: float) -> Callable[[Status], bool]:
        return lambda s: s.measurement is not None and s.measurement.force == pytest.approx(force)

    cell.value = 2.0
    b.wait(shows(-2.0))  # displayed force is the negated reading
    for _ in range(2):  # taring twice at the same load still shows zero
        b.machine.tare_force()
        b.wait(shows(0.0))
        time.sleep(0.1)
    cell.value = 5.0
    b.wait(shows(-3.0))
    b.machine.tare_force()
    b.wait(shows(0.0))


def test_shutdown_during_a_test_is_safe_to_repeat(make: Callable[..., Bench]) -> None:
    b = make(**LONG)
    b.start_test(samples=2)
    b.machine.shutdown()
    b.machine.shutdown()
    assert not b.machine._controller.is_alive()
    position = b.rig.position_mm()
    assert position < 9.99  # stopped where it was: no return move
    time.sleep(0.2)
    assert b.rig.position_mm() == position
