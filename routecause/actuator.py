"""Actuator (stretch, A6) — the ONLY component that may mutate the cluster.

Quarantine split: collectors, the LLM subagents, and the refuters are all
read-only (they import ``routecause.kube``, which exposes only read verbs). This
module holds the sole mutating kubectl runner, and it is invoked only behind an
explicit opt-in (``ROUTECAUSE_ALLOW_APPLY=1`` / ``--apply --confirm``). It applies
an already-validated fix, then watches the same metrics until the pool recovers
under SLO, and reports before/after numbers.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

from .collectors.metrics import Q_HIT, Q_P95E2E, Q_QUEUE, Q_RPS, Q_TTFT_BE
from .config import KUBE_CONTEXT, NAMESPACE, SLO_P95_E2E_SECONDS
from .prom import PrometheusClient
from .schema import Fix, Recovery

ALLOW_APPLY_ENV = "ROUTECAUSE_ALLOW_APPLY"


def apply_enabled() -> bool:
    return os.environ.get(ALLOW_APPLY_ENV) == "1"


def _mutate(args: list[str], input_text: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    cmd = ["kubectl", "--context", KUBE_CONTEXT, "-n", NAMESPACE] + args
    return subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)


def apply_fix(fix: Fix) -> tuple[bool, str]:
    """Apply the report's fix for real (the manifest in fix.command), then reload
    the EPP if the fix calls for it. Refuses unless apply is explicitly enabled."""
    if not apply_enabled():
        return False, f"apply disabled; set {ALLOW_APPLY_ENV}=1 to allow mutation"
    if not fix.command:
        return False, "fix has no apply command"
    m = re.search(r"<<'EOF'\n(.*?)\nEOF", fix.command, re.DOTALL)
    if not m:
        return False, "no manifest found in fix.command"
    proc = _mutate(["apply", "-f", "-"], input_text=m.group(1))
    if proc.returncode != 0:
        return False, proc.stderr.strip()
    out = proc.stdout.strip()
    if "rollout restart deploy/epp" in fix.command:
        _mutate(["rollout", "restart", "deploy/epp"])
        _mutate(["rollout", "status", "deploy/epp", "--timeout=120s"])
        out += " (EPP reloaded)"
    return True, out


def _snapshot(prom: PrometheusClient) -> dict:
    queue = prom.by_backend(Q_QUEUE) or {}
    rps = prom.by_backend(Q_RPS) or {}
    ttft = prom.by_backend(Q_TTFT_BE) or {}
    return {
        "p95_e2e_s": round(prom.scalar(Q_P95E2E) or 0.0, 2),
        "prefix_hit_rate": round(prom.scalar(Q_HIT) or 0.0, 3),
        "max_queue": round(max(queue.values(), default=0.0), 1),
        "min_success_rps": round(min(rps.values(), default=0.0), 2),
        "max_ttft_s": round(max(ttft.values(), default=0.0), 2),
    }


def watch_recovery(prom: PrometheusClient, timeout: int = 300, interval: int = 15) -> tuple[dict, bool]:
    """Poll until P95 e2e is back under SLO (and queues drained), or timeout."""
    deadline = time.monotonic() + timeout
    snap = _snapshot(prom)
    while time.monotonic() < deadline:
        snap = _snapshot(prom)
        if snap["p95_e2e_s"] and snap["p95_e2e_s"] < SLO_P95_E2E_SECONDS and snap["max_queue"] < 5:
            return snap, True
        time.sleep(interval)
    return snap, snap["p95_e2e_s"] < SLO_P95_E2E_SECONDS if snap["p95_e2e_s"] else False


def actuate(fix: Fix, prom: PrometheusClient | None = None, watch_timeout: int = 300) -> Recovery:
    """Capture before metrics, apply the fix, watch recovery, report before/after."""
    prom = prom or PrometheusClient()
    before = _snapshot(prom)
    ok, msg = apply_fix(fix)
    if not ok:
        return Recovery(applied=False, before=before, after={"error": msg}, slo_met=None)
    after, slo_met = watch_recovery(prom, timeout=watch_timeout)
    return Recovery(applied=True, before=before, after=after, slo_met=slo_met)
