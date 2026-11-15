"""C7 (NFR-007): compute_curvature reproduces the K column of legacy results files.

Saved K is dK against an unknown tare reference, and the resolution of each session
was not recorded, so the test compares differences from row 1 after fitting one scale
factor per file (docs/archive/03_spec/core.md, core.measurement: curvature, notes on C7).
"""

from pathlib import Path

import numpy as np
import pytest

from bending_machine.vision import compute_curvature

TESTS = Path(__file__).parents[1]
MARKERS = 6
FILES = sorted(
    [
        *(TESTS / "Cristian/5.Testing load cell/Test 16-07/PCL 100%/1").glob("Test [1-5].lvm"),
        *(TESTS / "Antonino").rglob("*.lvm"),
    ]
)


@pytest.mark.skipif(not FILES, reason="legacy data is not in the repository")
@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(TESTS)))
def test_c7_curvature_matches_legacy(path: Path) -> None:
    # Row layout: X1..X6, Y1..Y6, K, F, tab-separated with no header.
    rows = np.loadtxt(path, ndmin=2)
    assert rows.shape[1] == 2 * MARKERS + 2
    computed = np.array(
        [compute_curvature(list(r[:MARKERS]), list(r[MARKERS : 2 * MARKERS]), 1.0) for r in rows]
    )
    computed -= computed[0]
    saved = rows[:, -2] - rows[0, -2]

    scale = (computed @ saved) / (computed @ computed)
    print(f"fitted resolution {1 / scale:.5f} mm/px")
    assert np.abs(saved - scale * computed).max() <= 1e-6
