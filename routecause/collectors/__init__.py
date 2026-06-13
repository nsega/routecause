"""Deterministic, isolated evidence collectors (E1/E2/E3).

Each reads exactly ONE source and forms its own candidate signals — no collector
sees another's evidence (avoids a single context owning all evidence). They are
LLM-free and individually unit-testable; the LLM hypothesis/refuter layer and the
orchestrator sit on top.
"""

from .base import CollectorResult, Signal
from .clusterstate import collect_clusterstate
from .metrics import collect_metrics
from .schedconfig import collect_schedconfig

__all__ = [
    "CollectorResult",
    "Signal",
    "collect_metrics",
    "collect_schedconfig",
    "collect_clusterstate",
    "run_all_collectors",
]


def run_all_collectors(pool: str, prom=None) -> dict[str, CollectorResult]:
    """Run E1/E2/E3. Each collector is isolated and degrades to ``errors`` on failure."""
    return {
        "E1": collect_metrics(pool, prom=prom),
        "E2": collect_schedconfig(pool),
        "E3": collect_clusterstate(pool),
    }
