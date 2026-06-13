#!/usr/bin/env python3
"""Deterministic scenario grader (RUBRIC section A) — the verifier's tool.

Grades a RouteCause report for ONE injected scenario against RUBRIC A1-A4, by
*independently re-executing* the report's evidence locators and the fix dry-run
against the live cluster, and comparing fault_category to ground truth.

Ground truth is supplied at runtime, NEVER hard-coded — so the repository stays
grep-clean of the ground-truth file path (the diagnosis agent must never be able
to read it). Provide it one of two ways:

    --expected s1|s2|s3                  # the operator injected it, so they know it
    --ground-truth-env ENVVAR            # read the path from $ENVVAR, then parse {"fault": "..."}

The builder never calls this on itself; a fresh-context verifier subagent runs it.

Usage:
    python workflow/grade_scenario.py --report reports/latest.json --expected s1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routecause.kube import apply_dry_run_server  # noqa: E402
from routecause.prom import PrometheusClient, PrometheusError  # noqa: E402
from routecause.schema import SCENARIO_TO_CATEGORY, Report  # noqa: E402

MAX_RUN_SECONDS = 600  # A1: <= 10 minutes


def _ground_truth_label(args) -> str:
    if args.expected:
        return args.expected.lower()
    if args.ground_truth_env:
        path = os.environ.get(args.ground_truth_env)
        if not path or not Path(path).exists():
            raise SystemExit(f"ground-truth env {args.ground_truth_env} unset or path missing")
        data = json.loads(Path(path).read_text())
        return str(data["fault"]).lower()
    raise SystemExit("provide --expected sN or --ground-truth-env ENVVAR")


def _elapsed_seconds(r: Report) -> float:
    from datetime import datetime

    a = datetime.fromisoformat(r.started_at)
    b = datetime.fromisoformat(r.completed_at)
    return (b - a).total_seconds()


def grade(report: Report, ground_label: str) -> dict:
    checks: list[dict] = []

    def add(cid, level, ok, detail):
        checks.append({"id": cid, "level": level, "pass": bool(ok), "detail": detail})

    # A1 — completed end-to-end, no crash, <= 10 min
    elapsed = _elapsed_seconds(report)
    add("A1", "MUST", report.fault_category is not None and elapsed <= MAX_RUN_SECONDS,
        f"report produced; elapsed {elapsed:.1f}s (limit {MAX_RUN_SECONDS}s)")

    # A2 — fault_category equals ground truth
    expected_cat = SCENARIO_TO_CATEGORY.get(ground_label)
    add("A2", "MUST", expected_cat is not None and report.fault_category == expected_cat,
        f"ground-truth {ground_label} -> {expected_cat.value if expected_cat else '?'}; "
        f"report says {report.fault_category.value}")

    # A3 — >= 2 distinct sources AND each citation re-executable / true on the live cluster
    sources = {e.source.value for e in report.evidence}
    prom = PrometheusClient()
    reexec_ok, reexec_detail = True, []
    for e in report.evidence:
        if e.source.value == "prometheus":
            try:
                got = prom.query(e.locator)
                ok = len(got) > 0
            except PrometheusError:
                ok = False
            reexec_ok &= ok
            reexec_detail.append(f"prom[{'ok' if ok else 'NO DATA'}] {e.locator[:40]}")
    add("A3", "MUST", len(sources) >= 2 and reexec_ok,
        f"{len(report.evidence)} citations across {sorted(sources)}; "
        f"re-exec: {'; '.join(reexec_detail) or 'no prometheus locators'}")

    # A4 — fix passes kubectl apply --dry-run=server (re-run the report's own fix)
    a4_ok, a4_detail = False, "no manifest found in fix.command"
    if report.fix.command:
        m = re.search(r"<<'EOF'\n(.*?)\nEOF", report.fix.command, re.DOTALL)
        if m:
            ok, msg = apply_dry_run_server(m.group(1))
            a4_ok, a4_detail = ok, (msg.splitlines()[-1] if msg else "applied (dry-run)")
        else:
            a4_detail = "fix.command present but no heredoc manifest"
    elif report.fix.diff:
        a4_detail = "fix is a manifest-diff without an apply command"
    add("A4", "MUST", a4_ok and report.fix.dry_run_validated, f"dry-run: {a4_detail}")

    musts = [c for c in checks if c["level"] == "MUST"]
    return {
        "scenario": ground_label,
        "fault_category": report.fault_category.value,
        "all_musts_pass": all(c["pass"] for c in musts),
        "checks": checks,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    p.add_argument("--expected", choices=["s1", "s2", "s3"])
    p.add_argument("--ground-truth-env")
    args = p.parse_args(argv)

    report = Report.model_validate_json(Path(args.report).read_text())
    result = grade(report, _ground_truth_label(args))
    print(json.dumps(result, indent=2))
    return 0 if result["all_musts_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
