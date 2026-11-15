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
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and not data.get("loading_positive") and "k_limit" in data:
            # Written when loading gave negative dK: the limit keeps its meaning, not its sign.
            data["k_limit"] = -data["k_limit"]
        stored = Settings.model_validate(data)
        return stored.model_copy(update={"tracking": False})
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError, TypeError) as error:
        _log.warning("settings file %s ignored, using defaults: %s", path, error)
        return Settings()


def save_settings(folder: Path, settings: Settings) -> None:
    """Write settings.json through a temporary file. Raises SaveError."""
    path = folder / "settings.json"
    tmp = folder / "settings.json.tmp"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        data = {**settings.model_dump(mode="json"), "loading_positive": True}
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as error:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise SaveError(str(path), str(error)) from error


def results_exist(results_path: Path) -> bool:
    """True if the .csv file or its .meta.json already exists."""
    return results_path.exists() or results_path.with_suffix(".meta.json").exists()


def write_results(record: TestRecord, results_path: Path, overwrite: bool) -> None:
    """Write name.csv and name.meta.json through temporary files.

    Raises SaveError if a target exists and overwrite is False, the record has no
    samples, or writing fails. The caller keeps the record until this succeeds.
    """
    if not record.samples:
        raise SaveError(str(results_path), "no samples")
    if not overwrite and results_exist(results_path):
        raise SaveError(str(results_path), "file exists")

    markers = range(1, len(record.samples[0].xs) + 1)
    header = ["t_s", "stage_mm", "dk_per_mm", "force_g"]
    header += [f"x{i}_px" for i in markers] + [f"y{i}_px" for i in markers]
    rows = ",".join(header) + "\r\n"
    for s in record.samples:
        # Decimals well below each quantity's noise; 4 for markers keeps replayed dK within 1e-7.
        values = [(s.t, 3), (s.stage_mm, 4), (s.dk, 6), (s.force, 4)]
        values += [(v, 4) for v in (*s.xs, *s.ys)]
        rows += ",".join(f"{round(v, d) + 0.0:.{d}f}" for v, d in values) + "\r\n"  # no -0.0
    metadata = {
        "schema": 2,
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
