"""Unit tests for storage: settings file, results and metadata files, session log."""

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from bending_machine import models, storage
from bending_machine.models import EndReason, SaveError, Settings

LEGACY = sorted((Path(__file__).parents[1] / "Antonino").rglob("*.lvm"))


def record(samples: int = 2) -> models.TestRecord:
    rows = [
        models.Sample(
            (219.797256, 309.985138), (634.939148, 629.35498), 0.000105, 6.283017e-5, 0, 10
        ),
        models.Sample(
            (219.818619, 310.029297), (612.901733, 607.659851), -0.000491, -0.006024, 1, 9
        ),
    ]
    return models.TestRecord(
        samples=rows[:samples],
        started=datetime(2026, 7, 16, 16, 28, 5),
        settings=Settings(),
        tare=models.TareState(k_ref=-0.0031, f_ref=None),
        start_position_mm=10.0,
        end_reason=EndReason.COMPLETED,
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0.000000"),
        (219.797256, "219.797256"),
        (-0.000491, "-0.000491"),
        (1e-4, "0.000100"),
        (9.9999e-5, "9.999900E-5"),
        (6.283017e-5, "6.283017E-5"),
        (-3.1e-6, "-3.100000E-6"),
        (1.413679e-6, "1.413679E-6"),
    ],
)
def test_format_value(value: float, text: str) -> None:
    assert storage.format_value(value) == text


def test_write_results_bytes_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "Test 1.lvm"
    storage.write_results(record(), path, overwrite=False)
    assert path.read_bytes() == (
        b"\t219.797256\t309.985138\t634.939148\t629.354980\t0.000105\t6.283017E-5\r\n"
        b"\t219.818619\t310.029297\t612.901733\t607.659851\t-0.000491\t-0.006024\r\n"
    )
    meta_text = (tmp_path / "Test 1.meta.json").read_text(encoding="utf-8")
    assert "\r" not in meta_text
    meta = json.loads(meta_text)
    assert list(meta) == [
        "schema", "results_file", "start_date", "start_time", "marker_count",
        "resolution_mm_per_px", "k_limit", "test_step_mm", "settle_s", "fit_subset", "k_ref",
        "f_ref", "tare_overridden", "end_reason", "sample_count", "start_position_mm",
        "software_version",
    ]  # fmt: skip
    assert meta["schema"] == 1
    assert meta["results_file"] == "Test 1.lvm"
    assert (meta["start_date"], meta["start_time"]) == ("2026-07-16", "16:28:05")
    assert meta["resolution_mm_per_px"] == 0.0111
    assert (meta["k_ref"], meta["f_ref"]) == (-0.0031, None)
    assert (meta["end_reason"], meta["sample_count"]) == ("completed", 2)
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.skipif(not LEGACY, reason="legacy data is not in the repository")
@pytest.mark.parametrize("legacy", LEGACY[:5], ids=lambda p: p.name)
def test_legacy_rows_reformat_identically(legacy: Path) -> None:
    """Parsing a legacy file and formatting its numbers again gives the same text."""
    for line in legacy.read_text(encoding="ascii").splitlines()[:200]:
        fields = line.split("\t")[1:]
        assert [storage.format_value(float(f)) for f in fields] == fields


def test_existing_target_needs_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "a.lvm"
    storage.write_results(record(), path, overwrite=False)
    assert storage.results_exist(path)
    with pytest.raises(SaveError, match="file exists"):
        storage.write_results(record(1), path, overwrite=False)
    assert len(path.read_bytes().splitlines()) == 2  # untouched
    path.unlink()  # the metadata file alone also counts as existing
    with pytest.raises(SaveError):
        storage.write_results(record(), path, overwrite=False)
    storage.write_results(record(1), path, overwrite=True)
    assert len(path.read_bytes().splitlines()) == 1


def test_empty_record_refused(tmp_path: Path) -> None:
    with pytest.raises(SaveError, match="no samples"):
        storage.write_results(record(0), tmp_path / "a.lvm", overwrite=False)
    assert list(tmp_path.iterdir()) == []


def test_unwritable_folder_leaves_nothing(tmp_path: Path) -> None:
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(SaveError):
            storage.write_results(record(), tmp_path / "a.lvm", overwrite=False)
        with pytest.raises(SaveError):
            storage.save_settings(tmp_path, Settings())
        assert list(tmp_path.iterdir()) == []
    finally:
        tmp_path.chmod(0o700)


def test_settings_round_trip(tmp_path: Path) -> None:
    folder = tmp_path / "new"  # created on save
    assert storage.load_settings(folder) == Settings()
    changed = Settings(
        stage_port="COM3", roi=models.Roi(10, 20, 300, 200), k_limit=-0.05, tracking=True,
        fit_subset=models.FitSubset.EXCLUDE_OUTER,
    )  # fmt: skip
    storage.save_settings(folder, changed)
    assert storage.load_settings(folder) == changed.model_copy(update={"tracking": False})
    assert list(folder.glob("*.tmp")) == []


@pytest.mark.parametrize("content", ["not json", '{"marker_count": 1}', '{"k_limit": 0}'])
def test_bad_settings_file_gives_defaults(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "settings.json").write_text(content)
    assert storage.load_settings(tmp_path) == Settings()
    assert "using defaults" in caplog.text


def test_default_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", "/data")
    assert storage.default_folder() == Path("/data/bending_machine")
    monkeypatch.delenv("APPDATA")
    assert storage.default_folder() == Path.home() / ".bending_machine"


def test_session_log_writes_and_prunes(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for i in range(55):
        (logs / f"bending_20200101_0000{i:02d}.log").touch()
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        path = storage.start_session_log(tmp_path)
        logging.getLogger("bending_machine.test").info("hello %d", 7)
    finally:
        for handler in root.handlers[:]:
            handler.close()
        root.handlers[:] = saved
        root.setLevel(level)
    assert path.parent == logs and path.name.startswith("bending_")
    assert " INFO " in path.read_text() and "bending_machine.test: hello 7" in path.read_text()
    names = sorted(p.name for p in logs.glob("*.log"))
    assert len(names) == 50
    assert names[0] == "bending_20200101_000006.log" and path.name in names
