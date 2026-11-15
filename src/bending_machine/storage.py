"""Files: operator settings, test results with metadata, and the session log.

Design: docs/02_design.md §8.
"""

import contextlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from bending_machine import __version__
from bending_machine.models import SaveError, Settings, TestMetadata, TestRecord

_log = logging.getLogger(__name__)


def default_folder() -> Path:
    """Folder for settings.json and logs/: %APPDATA%/bending_machine, else ~/.bending_machine."""
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "bending_machine" if appdata else Path.home() / ".bending_machine"


def load_settings(folder: Path) -> Settings:
    """Stored settings, tracking off; defaults if the file is absent or invalid. Never raises."""
    path = folder / "settings.json"
    try:
        stored = Settings.model_validate_json(path.read_text(encoding="utf-8"))
        return stored.model_copy(update={"tracking": False})
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as error:
        _log.warning("settings file %s ignored, using defaults: %s", path, error)
        return Settings()


def save_settings(folder: Path, settings: Settings) -> None:
    """Write settings.json through a temporary file. Raises SaveError."""
    path = folder / "settings.json"
    tmp = folder / "settings.json.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise SaveError(str(path), str(error)) from error


def format_value(value: float) -> str:
    """One number in the legacy .lvm format (six decimals; exponent form below 1e-4)."""
    if value == 0 or abs(value) >= 1e-4:
        return f"{value:.6f}"
    mantissa, exponent = f"{value:.6E}".split("E")
    return f"{mantissa}E{int(exponent)}"  # int() drops the sign padding: E-05 -> E-5


def results_exist(results_path: Path) -> bool:
    """True if the .lvm file or its .meta.json already exists."""
    return results_path.exists() or results_path.with_suffix(".meta.json").exists()


def write_results(record: TestRecord, results_path: Path, overwrite: bool) -> None:
    """Write name.lvm and name.meta.json through temporary files.

    Raises SaveError if a target exists and overwrite is False, the record has no
    samples, or writing fails. The caller keeps the record until this succeeds.
    """
    if not record.samples:
        raise SaveError(str(results_path), "no samples")
    if not overwrite and results_exist(results_path):
        raise SaveError(str(results_path), "file exists")

    rows = "".join(
        "\t" + "\t".join(format_value(v) for v in (*s.xs, *s.ys, s.dk, s.force)) + "\r\n"
        for s in record.samples
    )
    metadata = {
        "schema": 1,
        "results_file": results_path.name,
        **TestMetadata.from_record(record, __version__).model_dump(),
    }
    files = [
        (results_path, rows, "ascii"),
        (results_path.with_suffix(".meta.json"), json.dumps(metadata, indent=2) + "\n", "utf-8"),
    ]
    try:
        for path, text, encoding in files:
            # newline="" writes the text as is: CR LF in results, LF in metadata.
            path.with_name(path.name + ".tmp").write_text(text, encoding=encoding, newline="")
        for path, _, _ in files:
            os.replace(path.with_name(path.name + ".tmp"), path)
    except OSError as error:
        raise SaveError(str(results_path), str(error)) from error
    finally:
        for path, _, _ in files:
            with contextlib.suppress(OSError):
                path.with_name(path.name + ".tmp").unlink(missing_ok=True)


def start_session_log(folder: Path) -> Path:
    """Log to folder/logs/bending_YYYYMMDD_HHMMSS.log and the console; keep the newest 50 files."""
    logs = folder / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"bending_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(threadName)-10s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    for old in sorted(logs.glob("bending_*.log"))[:-50]:  # names sort by date
        old.unlink()
    return path
