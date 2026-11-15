"""Marker detection, curvature and force arithmetic.

Pure functions on numpy arrays using OpenCV. Design: docs/02_design.md §4.
"""

import itertools
import math
from collections.abc import Sequence

import cv2
import numpy as np
import numpy.typing as npt

from bending_machine.models import FitSubset, Marker, MarkerSet, Roi


def prepare(image: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """Check that a camera frame can be processed and return it unchanged.

    image: uint8, shape (H, W). There is no rotation (FR-021 dropped).
    Raises ValueError if dtype is not uint8 or ndim != 2.
    """
    if image.dtype != np.uint8 or image.ndim != 2:
        raise ValueError(f"expected a 2-D uint8 image, got {image.dtype} with shape {image.shape}")
    return image


def binarise(
    image: npt.NDArray[np.uint8], lower: int, upper: int, invert: bool
) -> npt.NDArray[np.uint8]:
    """Return uint8 image, 255 where lower <= pixel <= upper, else 0.

    If invert, swap 0 and 255 afterwards.
    Raises ValueError unless 0 <= lower <= upper <= 255.
    """
    if not 0 <= lower <= upper <= 255:
        raise ValueError(f"invalid threshold bounds {lower}..{upper}")
    inside = (image >= lower) & (image <= upper)
    return np.where(inside != invert, 255, 0).astype(np.uint8)


def find_markers(
    binary: npt.NDArray[np.uint8],
    roi: Roi | None,
    min_area: int,
    max_area: int,
    expected: int,
    timestamp: float,
) -> MarkerSet:
    """Detect marker blobs (pixels == 255) and return their centroids sorted by x."""
    if min_area > max_area or expected < 1:
        raise ValueError("min_area must not exceed max_area and expected must be >= 1")
    left = top = 0
    if roi is not None:
        binary = binary[roi.top : roi.bottom, roi.left : roi.right]  # slicing clips to the image
        left, top = roi.left, roi.top
        if binary.shape[0] < 20 or binary.shape[1] < 20:
            return MarkerSet((), expected, timestamp)
    height, width = binary.shape

    # Fill holes: flood the background from a 1 px margin; background the flood does
    # not reach is enclosed by a blob and becomes part of it.
    padded = cv2.copyMakeBorder(binary, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(padded, None, (0, 0), 128)
    filled = (padded[1:-1, 1:-1] != 128).astype(np.uint8)

    count, _, stats, centroids = cv2.connectedComponentsWithStats(filled, connectivity=8)
    markers = []
    for i in range(1, count):  # label 0 is the background
        x, y, w, h, area = (int(v) for v in stats[i])
        touches_border = x == 0 or y == 0 or x + w == width or y + h == height
        if not touches_border and min_area <= area <= max_area:
            cx, cy = centroids[i]
            markers.append(Marker(float(cx) + left, float(cy) + top, area))
    markers.sort(key=lambda m: (m.x, m.y))
    return MarkerSet(tuple(markers), expected, timestamp)


# --- curvature -----------------------------------------------------------------


def compute_curvature(
    xs: Sequence[float],
    ys: Sequence[float],
    resolution_mm_per_px: float,
    fit_subset: FitSubset = FitSubset.ALL,
) -> float:
    """Absolute curvature in 1/mm at the evaluation point.

    xs, ys: marker centres in px, xs ascending, equal length n.
    Raises ValueError if lengths differ, n < 3 (ALL) or n < 5 (EXCLUDE_OUTER),
    xs not strictly ascending, or resolution_mm_per_px <= 0.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys differ in length")
    if fit_subset is FitSubset.EXCLUDE_OUTER:
        if len(xs) < 5:
            raise ValueError("excluding the outer markers needs at least 5 points")
        xs, ys = xs[1:-1], ys[1:-1]
    if len(xs) < 3:
        raise ValueError("at least 3 points are needed")
    if any(b <= a for a, b in itertools.pairwise(xs)):
        raise ValueError("xs must be strictly ascending")
    if resolution_mm_per_px <= 0:
        raise ValueError("resolution must be positive")

    a, b, _ = np.polyfit(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64), 2)
    slope = 2 * a * evaluation_x(xs) + b
    curvature = float(2 * a / ((1 + slope**2) ** 1.5 * resolution_mm_per_px))
    if not math.isfinite(curvature):
        raise ValueError("fit failed")
    return curvature


def evaluation_x(xs: Sequence[float]) -> float:
    """Evaluation point (FR-041): xs[m/2] for even m; for odd m the mean of the two around it."""
    m = len(xs)
    if m % 2 == 0:
        return xs[m // 2]
    return (xs[(m - 1) // 2] + xs[(m + 1) // 2]) / 2


# --- force ---------------------------------------------------------------------


def to_display(raw: float, force_sign: int) -> float:
    """force_sign * raw. force_sign is -1 or +1 (models.FORCE_SIGN)."""
    if force_sign not in (-1, 1):
        raise ValueError("force_sign must be -1 or +1")
    return force_sign * raw


def apply_tare(raw: float, force_sign: int, f_ref: float | None) -> float:
    """to_display(raw, force_sign) - (f_ref or 0.0)."""
    return to_display(raw, force_sign) - (f_ref or 0.0)


def tare_reference(raw: float, force_sign: int) -> float:
    """Value to store as TareState.f_ref: to_display(raw, force_sign)."""
    return to_display(raw, force_sign)


def is_overload(raw: float, limit_raw: float) -> bool:
    """abs(raw) > limit_raw on the untared reading; NaN is handled as a read error."""
    if limit_raw <= 0:
        raise ValueError("limit_raw must be positive")
    return abs(raw) > limit_raw
