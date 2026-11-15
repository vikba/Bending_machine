"""Unit tests for models, vision and sequences; case IDs follow docs/archive/03_spec/core.md."""

import math
import time
from dataclasses import replace
from datetime import datetime
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import pytest
from pydantic import ValidationError

# models and sequences are used through their modules: pytest would try to collect
# their Test* classes if they were imported by name.
from bending_machine import models, sequences, vision
from bending_machine.models import EndReason, FitSubset, Measurement, PrecondPhase, Settings
from bending_machine.sequences import Finish, Hold, MoveTo, Step
from bending_machine.vision import (
    apply_tare,
    compute_curvature,
    evaluation_x,
    is_overload,
    tare_reference,
    to_display,
)

# --- core.models -------------------------------------------------------------------


def test_m1_settings_defaults() -> None:
    assert Settings().model_dump() == {
        "stage_port": "",
        "camera_id": "",
        "roi": None,
        "threshold_lower": 2,
        "threshold_upper": 60,
        "invert": False,
        "min_area_px": 200,
        "max_area_px": 15000,
        "marker_count": 6,
        "tracking": False,
        "resolution_mm_per_px": 0.0111,
        "fit_subset": FitSubset.ALL,
        "jog_step_mm": 0.0612244898,
        "test_step_mm": 0.0477,
        "settle_s": 0.4,
        "k_limit": 0.12,
        "pc_amplitude": 0.0,
        "pc_cycles": 1,
        "pc_dwell_s": 3.0,
    }


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"marker_count": 2}, id="m2"),
        pytest.param({"threshold_lower": 100, "threshold_upper": 50}, id="m4"),
        pytest.param({"fit_subset": FitSubset.EXCLUDE_OUTER, "marker_count": 4}, id="m5"),
        pytest.param({"k_limit": 0}, id="m6"),
        pytest.param({"settle_s": 0.01}, id="m7"),
        pytest.param({"resolution_mm_per_px": 0}, id="m9"),
    ],
)
def test_m2_m4_m5_m6_m7_m9_invalid_settings(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**fields)
    with pytest.raises(ValidationError):
        Settings().model_copy(update=fields)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"marker_count": 50}, id="m3"),
        pytest.param({"settle_s": 0.4}, id="m7"),
        pytest.param({"pc_dwell_s": 1.0}, id="m8"),
    ],
)
def test_m3_m7_m8_valid_settings(fields: dict[str, Any]) -> None:
    assert Settings(**fields).model_dump(include=set(fields)) == fields


def test_m10_rig_constants() -> None:
    assert models.OVERLOAD_LIMIT_G == 25.0
    assert models.FORCE_SIGN == models.STAGE_UP_SIGN == models.LOADING_STAGE_SIGN == -1
    assert models.CURVATURE_SIGN == -1


def marker_set(count: int, expected: int = 6) -> models.MarkerSet:
    return models.MarkerSet(
        tuple(models.Marker(100.0 * i, 50.0, 400) for i in range(count)), expected, 0.0
    )


def test_m11_marker_set_validity() -> None:
    assert marker_set(5).valid is False
    assert marker_set(6).valid is True
    assert marker_set(2).xs == (0.0, 100.0)
    assert marker_set(2).ys == (50.0, 50.0)


def test_m12_metadata_from_record() -> None:
    record = models.TestRecord(
        samples=[],
        started=datetime(2026, 11, 14, 9, 5, 7),
        settings=Settings(resolution_mm_per_px=0.0111),
        tare=models.TareState(k_ref=0.01, f_ref=None, overridden=True),
        start_position_mm=12.5,
        end_reason=EndReason.STOPPED,
    )
    meta = models.TestMetadata.from_record(record, "1.2.3")
    assert meta.resolution_mm_per_px == 0.0111
    assert (meta.start_date, meta.start_time) == ("2026-11-14", "09:05:07")
    assert (meta.k_ref, meta.f_ref, meta.tare_overridden) == (0.01, None, True)
    assert (meta.end_reason, meta.sample_count, meta.fit_subset) == ("stopped", 0, "all")
    assert (meta.start_position_mm, meta.software_version) == (12.5, "1.2.3")


def meas(dk: float | None, markers: models.MarkerSet | None = None) -> Measurement:
    return Measurement(markers or marker_set(6), 0.0, dk, 0.0, 0.0, 0.0, 0.0)


def bad() -> Measurement:
    return meas(0.0, marker_set(5))


def test_m13_measurement_usable() -> None:
    assert meas(None).usable is False
    assert bad().usable is False
    assert meas(0.0).usable is True


@pytest.mark.parametrize(
    ("force", "skew", "usable"),
    [
        (1.0, 0.1, True),
        (1.0, -0.1, True),
        (1.0, 0.100001, False),
        (1.0, -0.100001, False),
        (math.nan, 0.0, False),
        (math.inf, 0.0, False),
        (-math.inf, 0.0, False),
    ],
)
def test_measurement_requires_finite_synchronized_force(
    force: float, skew: float, usable: bool
) -> None:
    assert replace(meas(0.0), force=force, frame_time=skew).usable is usable


def test_roi_validation() -> None:
    assert models.Roi(0, 0, 20, 20).right == 20
    for box in [(-1, 0, 30, 30), (0, 0, 19, 30), (0, 10, 30, 29)]:
        with pytest.raises(ValueError):
            models.Roi(*box)


# --- core.errors -------------------------------------------------------------------


def test_e1_device_named_in_message() -> None:
    assert str(models.LoadCellError("not found")) == "load cell: not found"


def test_e2_motion_interrupted_is_stage_error() -> None:
    error = models.MotionInterrupted("x")
    assert isinstance(error, models.StageError)
    assert error.device == "stage"


def test_e3_camera_timeout_is_camera_error() -> None:
    error = models.CameraTimeout("1.0 s")
    assert isinstance(error, models.CameraError)
    assert error.device == "camera"


def test_e4_save_error_path() -> None:
    assert models.SaveError("C:/a.lvm", "denied").path == "C:/a.lvm"


# --- core.vision -------------------------------------------------------------------

SIX = [(100, 200), (180, 210), (260, 215), (340, 215), (420, 210), (500, 200)]


def scene(
    discs: list[tuple[int, int]], radius: int = 12, shape: tuple[int, int] = (480, 640)
) -> npt.NDArray[np.uint8]:
    """Binary image of dark discs (grey 20) on a background of 200."""
    image = np.full(shape, 200, np.uint8)
    for centre in discs:
        cv2.circle(image, centre, radius, 20, -1)
    return vision.binarise(image, 2, 60, False)


def find(
    binary: npt.NDArray[np.uint8], roi: models.Roi | None = None, expected: int = 6
) -> models.MarkerSet:
    return vision.find_markers(binary, roi, 200, 15000, expected, 1.5)


def test_v1_prepare_returns_image_unchanged() -> None:
    image = np.arange(24, dtype=np.uint8).reshape(4, 6)
    assert vision.prepare(image) is image


@pytest.mark.parametrize("image", [np.zeros((4, 6), np.float32), np.zeros((4, 6, 3), np.uint8)])
def test_v2_prepare_rejects_wrong_dtype_or_shape(image: Any) -> None:
    with pytest.raises(ValueError):
        vision.prepare(image)


@pytest.mark.parametrize(
    ("invert", "expected"),
    [
        pytest.param(False, [0, 255, 255, 0], id="v3"),
        pytest.param(True, [255, 0, 0, 255], id="v4"),
    ],
)
def test_v3_v4_binarise(invert: bool, expected: list[int]) -> None:
    image = np.array([[1, 2, 60, 61]], np.uint8)
    assert vision.binarise(image, 2, 60, invert).tolist() == [expected]


def test_binarise_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        vision.binarise(np.zeros((4, 6), np.uint8), 61, 60, False)


def test_v5_six_discs() -> None:
    found = find(scene(SIX))
    assert found.valid is True
    assert found.timestamp == 1.5
    assert [(round(m.x), round(m.y)) for m in found.markers] == SIX
    for marker, (x, y) in zip(found.markers, SIX, strict=True):
        assert math.hypot(marker.x - x, marker.y - y) <= 1.0


def test_v6_disc_outside_roi_is_ignored() -> None:
    found = find(scene([*SIX, (300, 400)]), models.Roi(50, 150, 560, 280))
    assert found.valid is True
    assert [(round(m.x), round(m.y)) for m in found.markers] == SIX


def test_v7_disc_touching_roi_edge_is_rejected() -> None:
    binary = scene([(100, 200), (300, 200)])
    assert len(find(binary, models.Roi(95, 100, 400, 300)).markers) == 1
    assert len(find(scene([(5, 200)])).markers) == 0  # touching the image edge


def test_v8_area_limits() -> None:
    found = find(scene([(100, 100)], 6) | scene([(250, 100)], 18) | scene([(450, 250)], 80))
    assert len(found.markers) == 1
    assert found.markers[0].area == pytest.approx(1000, rel=0.05)


def test_v9_ring_is_filled() -> None:
    ring = scene([(300, 200)], 20)
    cv2.circle(ring, (300, 200), 8, 0, -1)
    (marker,) = find(ring).markers
    assert marker.area == np.count_nonzero(scene([(300, 200)], 20)) == pytest.approx(1200, rel=0.1)
    assert math.hypot(marker.x - 300, marker.y - 200) <= 0.5


@pytest.mark.parametrize(
    "discs", [pytest.param(SIX[:5], id="v10"), pytest.param([*SIX, (580, 200)], id="v11")]
)
def test_v10_v11_wrong_marker_count(discs: list[tuple[int, int]]) -> None:
    found = find(scene(discs))
    assert len(found.markers) == len(discs)
    assert found.valid is False


def test_v12_output_sorted_by_x() -> None:
    xs = find(scene(SIX[::-1])).xs
    assert list(xs) == sorted(xs)


def test_v13_processing_time() -> None:
    image = np.full((960, 1280), 200, np.uint8)
    for x in range(200, 1200, 180):
        cv2.circle(image, (x, 480), 12, 20, -1)
    start = time.perf_counter()
    for _ in range(100):
        binary = vision.binarise(vision.prepare(image), 2, 60, False)
        assert find(binary).valid
    assert (time.perf_counter() - start) / 100 <= 0.020


def test_find_markers_error_and_empty_cases() -> None:
    blank = scene([])
    assert find(blank).markers == ()
    assert find(blank).valid is False
    assert find(scene(SIX), models.Roi(630, 0, 700, 480)).markers == ()  # clipped below 20 px
    with pytest.raises(ValueError):
        vision.find_markers(blank, None, 300, 200, 6, 0.0)
    with pytest.raises(ValueError):
        vision.find_markers(blank, None, 200, 300, 0, 0.0)


# --- core.measurement: curvature ---------------------------------------------------

XS = [200.0, 380.0, 560.0, 740.0, 920.0, 1100.0]


def parabola(xs: list[float]) -> list[float]:
    return [1e-3 * x**2 + 600 for x in xs]


def test_c1_parabola() -> None:
    expected = 2e-3 / ((1 + (2e-3 * XS[3]) ** 2) ** 1.5 * 0.01)
    assert compute_curvature(XS, parabola(XS), 0.01) == pytest.approx(expected, rel=1e-9)


def test_c2_circle() -> None:
    xs = [-100.0, -60.0, -20.0, 20.0, 60.0, 100.0]
    ys = [math.sqrt(500**2 - x**2) for x in xs]  # lowest point (largest y) at x = 0
    assert abs(compute_curvature(xs, ys, 0.01)) == pytest.approx(0.2, rel=0.01)


@pytest.mark.parametrize(
    ("xs", "expected"),
    [
        pytest.param([10, 20, 30, 40, 50, 60], 40, id="c3"),
        pytest.param([10, 20, 30, 40, 50], 35, id="c4"),
    ],
)
def test_c3_c4_evaluation_x(xs: list[float], expected: float) -> None:
    assert evaluation_x(xs) == expected


def test_c5_exclude_outer() -> None:
    ys = parabola(XS)
    inner = compute_curvature(XS[1:5], ys[1:5], 0.01)
    displaced = [ys[0] + 30, *ys[1:5], ys[5] + 30]
    assert compute_curvature(XS, displaced, 0.01, FitSubset.EXCLUDE_OUTER) == inner
    assert abs(compute_curvature(XS, displaced, 0.01) / inner - 1) > 0.05


def test_c6_resolution_scales_result() -> None:
    ys = parabola(XS)
    assert compute_curvature(XS, ys, 0.02) == pytest.approx(
        compute_curvature(XS, ys, 0.01) / 2, rel=1e-12
    )


def test_c8_collinear_points() -> None:
    ys = [0.5 * x + 3 for x in XS]
    assert compute_curvature(XS, ys, 0.01) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    ("xs", "ys", "resolution", "subset"),
    [
        pytest.param([1, 2], [1, 2], 0.01, FitSubset.ALL, id="two-points"),
        pytest.param([1, 2, 3, 4], [1, 2, 3, 4], 0.01, FitSubset.EXCLUDE_OUTER, id="four-exclude"),
        pytest.param([1, 2, 3], [1, 2], 0.01, FitSubset.ALL, id="unequal-lengths"),
        pytest.param([1, 2, 3], [1, 2, 3], 0, FitSubset.ALL, id="resolution-zero"),
        pytest.param([1, 3, 3], [1, 2, 3], 0.01, FitSubset.ALL, id="not-ascending"),
        pytest.param([1, 2, 3], [1, math.nan, 3], 0.01, FitSubset.ALL, id="fit-failed"),
    ],
)
def test_c9_invalid_arguments(
    xs: list[float], ys: list[float], resolution: float, subset: FitSubset
) -> None:
    with pytest.raises(ValueError):
        compute_curvature(xs, ys, resolution, subset)


# --- core.measurement: force -------------------------------------------------------


@pytest.mark.parametrize(
    ("sign", "expected"), [pytest.param(-1, -3.2, id="f1"), pytest.param(1, 3.2, id="f2")]
)
def test_f1_f2_to_display(sign: int, expected: float) -> None:
    assert to_display(3.2, sign) == expected


def test_f3_tare_gives_zero() -> None:
    for raw in (3.2, 3.2, 5.0, 5.0):
        reference = tare_reference(raw, -1)
        assert apply_tare(raw, -1, reference) == 0.0


@pytest.mark.parametrize(
    ("f_ref", "expected"), [pytest.param(-3.2, -1.8, id="f4"), pytest.param(None, -5.0, id="f5")]
)
def test_f4_f5_apply_tare(f_ref: float | None, expected: float) -> None:
    assert apply_tare(5.0, -1, f_ref) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(25.0, False, id="f6-at-limit"),
        pytest.param(25.001, True, id="f6-above"),
        pytest.param(-25.001, True, id="f6-below"),
        pytest.param(-25.0, False, id="f6-at-negative-limit"),
        pytest.param(0.0, False, id="f6-zero"),
        pytest.param(math.nan, False, id="f7-invalid-reading"),
    ],
)
def test_f6_f7_is_overload(raw: float, expected: bool) -> None:
    assert is_overload(raw, 25.0) is expected


def test_f8_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        to_display(1.0, 0)
    with pytest.raises(ValueError):
        is_overload(1.0, 0.0)
    assert math.isnan(apply_tare(math.nan, -1, None))


# --- core.sequences: test run ------------------------------------------------------

LOADING, UNLOADING, PAUSED = models.TestPhase


def test_run(k_limit: float = 0.12, step: float = 0.05) -> sequences.TestRunSequence:
    return sequences.TestRunSequence(k_limit, step, -1)


test_run.__test__ = False  # type: ignore[attr-defined]  # a helper, not a test


def test_t1_to_t5_load_then_unload() -> None:
    seq = test_run()
    assert seq.phase is LOADING
    for dk in (0.0, 0.05, 0.10):  # T1, T2
        assert seq.next(meas(dk)) == Step(-0.05)
        assert seq.phase is LOADING
    assert seq.next(meas(0.12)) == Step(0.05)  # T3
    assert seq.phase is UNLOADING
    assert seq.next(meas(0.06)) == Step(0.05)  # T4
    assert seq.steps_commanded == 5
    assert seq.next(meas(-0.001)) == Finish(EndReason.COMPLETED)  # T5
    with pytest.raises(RuntimeError):  # T12
        seq.next(meas(0.0))


def test_t6_negative_limit() -> None:
    seq = test_run(k_limit=-0.12)
    assert seq.next(meas(0.0)) == Step(0.05)
    assert seq.next(meas(-0.11)) == Step(0.05)
    assert seq.next(meas(-0.12)) == Step(-0.05)
    assert seq.phase is UNLOADING
    assert seq.next(meas(-0.01)) == Step(-0.05)
    assert seq.next(meas(0.0)) == Finish(EndReason.COMPLETED)


def test_t7_step_size_is_exact() -> None:
    seq = test_run(step=0.0477)
    actions = [seq.next(meas(dk)) for dk in (0.0, 0.06, 0.13, 0.06, 0.0)]
    steps = [a for a in actions if isinstance(a, Step)]
    assert len(steps) == seq.steps_commanded == 4
    assert all(abs(s.delta_mm) == 0.0477 for s in steps)


def test_t8_t9_pause_while_loading() -> None:
    seq = test_run()
    seq.next(meas(0.0))
    assert seq.next(bad()) == Hold()
    assert seq.next(meas(None)) == Hold()
    assert seq.phase is PAUSED
    assert seq.steps_commanded == 1
    assert seq.next(meas(0.05)) == Step(-0.05)
    assert seq.phase is LOADING


def test_t10_pause_while_unloading() -> None:
    seq = test_run()
    seq.next(meas(0.12))
    assert seq.next(bad()) == Hold()
    assert seq.phase is PAUSED
    assert seq.next(meas(0.03)) == Step(0.05)
    assert seq.phase is UNLOADING


def test_t11_first_measurement_beyond_limit() -> None:
    seq = test_run()
    assert seq.next(meas(0.20)) == Step(0.05)
    assert seq.phase is UNLOADING


@pytest.mark.parametrize("args", [(0, 0.05, -1), (0.12, 0, -1), (0.12, 0.05, 2)])
def test_t13_invalid_arguments(args: tuple[float, float, int]) -> None:
    with pytest.raises(ValueError):
        sequences.TestRunSequence(*args)


def test_t14_closed_loop() -> None:
    seq = test_run()
    position = 0.0
    dks: list[float] = []
    while isinstance(action := seq.next(meas(-0.8 * position)), Step):
        position += action.delta_mm
        dks.append(-0.8 * position)
    assert action == Finish(EndReason.COMPLETED)
    assert dks[2] >= 0.12
    assert dks[:3] == sorted(dks[:3])
    assert dks[3:] == sorted(dks[3:], reverse=True)
    assert 1 <= len(dks) - 3 <= 4


# --- core.sequences: preconditioning -----------------------------------------------


SEEK_FIRST, SEEK_SECOND, CYCLING = PrecondPhase


def precondition(cycles: int = 3) -> sequences.PreconditionSequence:
    """A sequence taken through P1-P3: limit A found at 9.5, seeking limit B."""
    seq = sequences.PreconditionSequence(0.05, cycles, 0.05, -1)
    assert seq.phase is SEEK_FIRST
    assert seq.next(meas(0.0), 10.0) == Step(-0.05)  # P1
    assert seq.phase is SEEK_FIRST
    assert seq.next(bad(), 9.8) == Hold()  # P8
    assert seq.limit_a_mm is None
    assert seq.next(meas(0.05), 9.5) == Step(0.05)  # P2
    assert seq.limit_a_mm == 9.5
    assert seq.phase is SEEK_SECOND
    assert seq.next(bad(), 9.8) == Hold()
    assert seq.next(meas(-0.02), 10.2) == Step(0.05)  # P3
    assert (seq.limit_b_mm, seq.cycles_done) == (None, 0)
    return seq


def test_p1_to_p6_p8_p9_three_cycles() -> None:
    seq = precondition()
    assert seq.next(meas(-0.05), 10.5) == MoveTo(9.5)  # P4
    assert (seq.limit_b_mm, seq.cycles_done) == (10.5, 1)
    assert seq.phase is CYCLING
    assert seq.next(bad(), 9.5) == MoveTo(10.5)  # P5, P9
    assert seq.next(meas(0.0), 10.5) == MoveTo(9.5)
    assert seq.cycles_done == 2
    assert seq.next(meas(0.0), 9.5) == MoveTo(10.5)  # P6
    assert seq.next(meas(0.0), 10.5) == Finish(EndReason.COMPLETED)
    assert seq.cycles_done == 3
    with pytest.raises(RuntimeError):
        seq.next(meas(0.0), 10.5)


def test_p7_single_cycle() -> None:
    seq = precondition(cycles=1)
    assert seq.next(meas(-0.05), 10.5) == Finish(EndReason.COMPLETED)
    assert seq.cycles_done == 1


@pytest.mark.parametrize("args", [(0, 3, 0.05, -1), (0.05, 0, 0.05, -1), (0.05, 3, -1, -1)])
def test_p10_invalid_arguments(args: tuple[float, int, float, int]) -> None:
    with pytest.raises(ValueError):
        sequences.PreconditionSequence(*args)


def test_p11_positive_sign() -> None:
    seq = sequences.PreconditionSequence(0.05, 3, 0.05, 1)
    assert seq.next(meas(0.0), 10.0) == Step(0.05)
