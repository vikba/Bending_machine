"""Pure state machines for the test run and preconditioning.

No timing, threads or device calls; machine.Machine drives them.
Design: docs/02_design.md §5.
"""

from dataclasses import dataclass

from bending_machine.models import EndReason, Measurement, PrecondPhase, TestPhase

# --- actions shared by both sequences -------------------------------------------


@dataclass(frozen=True, slots=True)
class Step:
    """Relative move to command."""

    delta_mm: float


@dataclass(frozen=True, slots=True)
class MoveTo:
    """Absolute move; the controller dwells before it. Preconditioning only."""

    target_mm: float


@dataclass(frozen=True, slots=True)
class Hold:
    """Do nothing; ask again with a newer measurement."""


@dataclass(frozen=True, slots=True)
class Finish:
    """The run is over. Always EndReason.COMPLETED from this module."""

    reason: EndReason


TestRunAction = Step | Hold | Finish
PrecondAction = Step | MoveTo | Hold | Finish


# --- test run --------------------------------------------------------------------


class TestRunSequence:
    """Two-phase test: load to the curvature limit, unload back to zero."""

    def __init__(self, k_limit: float, step_mm: float, loading_stage_sign: int) -> None:
        """k_limit: target dK in 1/mm, non-zero. step_mm > 0.

        loading_stage_sign: -1 or +1, sign of delta_mm that makes dK more negative.
        Raises ValueError on invalid arguments.
        """
        if k_limit == 0 or step_mm <= 0 or loading_stage_sign not in (-1, 1):
            raise ValueError("k_limit must be non-zero, step_mm > 0 and the sign -1 or +1")
        self._k_limit = k_limit
        self._sign = 1 if k_limit > 0 else -1  # side of zero on which the limit lies
        self._load_mm = -self._sign * loading_stage_sign * step_mm
        self._loading = True
        self._paused = False
        self._finished = False
        self._steps = 0

    @property
    def phase(self) -> TestPhase:
        """Current phase; LOADING initially."""
        if self._paused:
            return TestPhase.PAUSED
        return TestPhase.LOADING if self._loading else TestPhase.UNLOADING

    @property
    def steps_commanded(self) -> int:
        """Number of Step actions returned so far."""
        return self._steps

    def next(self, m: Measurement) -> TestRunAction:
        """Advance on one measurement.

        After Finish has been returned, further calls raise RuntimeError.
        """
        if self._finished:
            raise RuntimeError("the test run has finished")
        dk = m.dk
        self._paused = not m.usable or dk is None
        if dk is None or self._paused:
            return Hold()
        if self._loading:
            if self._sign * dk >= self._sign * self._k_limit:
                self._loading = False
        elif self._sign * dk <= 0:
            self._finished = True
            return Finish(EndReason.COMPLETED)
        self._steps += 1
        return Step(self._load_mm if self._loading else -self._load_mm)


# --- preconditioning ------------------------------------------------------------


class PreconditionSequence:
    """Seek both curvature limits, then cycle between the recorded positions."""

    def __init__(
        self, amplitude: float, cycles: int, step_mm: float, loading_stage_sign: int
    ) -> None:
        """amplitude > 0 (1/mm); cycles >= 1; step_mm > 0; sign -1 or +1.

        Raises ValueError on invalid arguments.
        """
        if amplitude <= 0 or cycles < 1 or step_mm <= 0 or loading_stage_sign not in (-1, 1):
            raise ValueError("amplitude and step_mm must be > 0, cycles >= 1, the sign -1 or +1")
        self._amplitude = amplitude
        self._cycles = cycles
        self._neg_mm = loading_stage_sign * step_mm  # step that makes dK more negative
        self._phase = PrecondPhase.SEEK_FIRST_LIMIT
        self._cycles_done = 0
        self._limit_a: float | None = None
        self._limit_b: float | None = None
        self._last_target_a = False
        self._finished = False

    @property
    def phase(self) -> PrecondPhase:
        """Current phase; SEEK_FIRST_LIMIT initially."""
        return self._phase

    @property
    def cycles_done(self) -> int:
        """Completed cycles; 0 initially."""
        return self._cycles_done

    @property
    def limit_a_mm(self) -> float | None:
        """Stage position at dK <= -amplitude, once found."""
        return self._limit_a

    @property
    def limit_b_mm(self) -> float | None:
        """Stage position at dK >= +amplitude, once found."""
        return self._limit_b

    def next(self, m: Measurement, position_mm: float) -> PrecondAction:
        """m: latest measurement; position_mm: current stage position.

        Raises RuntimeError if called after Finish.
        """
        if self._finished:
            raise RuntimeError("preconditioning has finished")
        dk = m.dk
        if self._phase is PrecondPhase.SEEK_FIRST_LIMIT:
            if dk is None or not m.usable:
                return Hold()
            if dk > -self._amplitude:
                return Step(self._neg_mm)
            self._limit_a = position_mm
            self._phase = PrecondPhase.SEEK_SECOND_LIMIT
            return Step(-self._neg_mm)
        elif self._phase is PrecondPhase.SEEK_SECOND_LIMIT:
            if dk is None or not m.usable:
                return Hold()
            if dk < self._amplitude:
                return Step(-self._neg_mm)
            self._limit_b = position_mm
        elif self._last_target_a:
            assert self._limit_b is not None
            self._last_target_a = False
            return MoveTo(self._limit_b)

        # Limit B has just been reached, by seeking or by a move: one cycle is complete.
        self._cycles_done += 1
        if self._cycles_done == self._cycles:
            self._finished = True
            return Finish(EndReason.COMPLETED)
        assert self._limit_a is not None
        self._phase = PrecondPhase.CYCLING
        self._last_target_a = True
        return MoveTo(self._limit_a)
