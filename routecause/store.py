"""Report persistence: keep the latest report in memory and on disk."""

from __future__ import annotations

import threading
from pathlib import Path

from .schema import Report

REPORTS_DIR = Path("reports")
_lock = threading.Lock()
_latest: Report | None = None


def save(report: Report) -> Report:
    global _latest
    with _lock:
        _latest = report
        REPORTS_DIR.mkdir(exist_ok=True)
        (REPORTS_DIR / f"{report.report_id}.json").write_text(report.model_dump_json(indent=2))
        (REPORTS_DIR / "latest.json").write_text(report.model_dump_json(indent=2))
    return report


def latest() -> Report | None:
    with _lock:
        if _latest is not None:
            return _latest
    p = REPORTS_DIR / "latest.json"
    if p.exists():
        return Report.model_validate_json(p.read_text())
    return None


def get(report_id: str) -> Report | None:
    p = REPORTS_DIR / f"{report_id}.json"
    return Report.model_validate_json(p.read_text()) if p.exists() else None
