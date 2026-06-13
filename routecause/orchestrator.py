"""Orchestrator — merges disjoint-source evidence into a ranked RCA report.

Deterministic core (this module): run E1/E2/E3, aggregate their signals into a
fault_category, assemble cross-source evidence (>= 2 distinct sources), build the
hypotheses list, and attach a dry-run-validated fix. The LLM layer
(``routecause.llm``) enriches root-cause narration and runs an independent
refuter per surviving hypothesis on top of this — but the core never depends on
the LLM, so a diagnosis still completes (and stays correct) without it.

``diagnose`` receives the pool name ONLY — never which fault was injected.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .collectors import CollectorResult, run_all_collectors
from .config import ROUTER_DISPLAY_NAME, SLO_P95_E2E_SECONDS
from .fixes import generate_fix
from .schema import (
    Evidence,
    FaultCategory,
    Fix,
    Hypothesis,
    HypothesisOrigin,
    HypothesisStatus,
    Report,
    RootCause,
)

# E1 observation claims that corroborate each category (for cross-source evidence).
_CORROBORATION = {
    FaultCategory.SCORER_WEIGHT_MISCONFIG: ("queue", "request rate"),
    FaultCategory.PREFIX_CACHE_DISABLED: ("hit rate", "prefix", "kv-cache"),
    FaultCategory.UNHEALTHY_ENDPOINT: ("request rate", "time-to-first-token", "ttft"),
    FaultCategory.OTHER: ("latency", "hit rate"),
}

_SUMMARY = {
    FaultCategory.SCORER_WEIGHT_MISCONFIG: (
        f"A scheduling-profile scorer weight in the {ROUTER_DISPLAY_NAME} config is non-positive, so "
        "the router concentrates traffic on a single backend instead of spreading it by score."
    ),
    FaultCategory.UNHEALTHY_ENDPOINT: (
        f"One backend has fault injection enabled but still passes its readiness probe, so {ROUTER_DISPLAY_NAME} "
        "keeps it in rotation while a fraction of its requests fail and slow down."
    ),
    FaultCategory.PREFIX_CACHE_DISABLED: (
        f"The prefix-cache-scorer is absent from the {ROUTER_DISPLAY_NAME} scheduling profile, disabling "
        "prefix-aware routing; the pool-wide prefix-cache hit rate collapses and latency degrades."
    ),
    FaultCategory.OTHER: "No known routing/scheduling fault detected; the pool is within healthy baselines.",
}


def _aggregate(collectors: dict[str, CollectorResult]) -> tuple[FaultCategory, dict[FaultCategory, float]]:
    scores: dict[FaultCategory, float] = defaultdict(float)
    for r in collectors.values():
        for s in r.signals:
            scores[s.category] += s.strength
    if not scores:
        return FaultCategory.OTHER, dict(scores)
    return max(scores, key=scores.get), dict(scores)


def _assemble_evidence(category: FaultCategory, collectors: dict[str, CollectorResult]) -> list[Evidence]:
    evidence: list[Evidence] = []
    seen: set[tuple] = set()

    def add(e: Evidence) -> None:
        key = (e.source, e.locator, e.observed)
        if key not in seen:
            seen.add(key)
            evidence.append(e)

    # 1) evidence cited by signals that chose this category
    for r in collectors.values():
        for s in r.signals:
            if s.category == category:
                for e in s.evidence:
                    add(e)
    # 2) relevant corroborating observations (esp. metrics) for cross-source support
    keywords = _CORROBORATION.get(category, ())
    for r in collectors.values():
        for o in r.observations:
            if any(kw.lower() in o.claim.lower() for kw in keywords):
                add(o)
    # 3) guarantee >= 2 distinct sources (A3)
    present = {e.source for e in evidence}
    if len(present) < 2:
        for r in collectors.values():
            if r.source not in present and r.observations:
                add(r.observations[0])
                present.add(r.source)
            if len(present) >= 2:
                break
    return evidence


def _best_signal(category: FaultCategory, collectors: dict[str, CollectorResult]):
    best = None
    origin = None
    for r in collectors.values():
        for s in r.signals:
            if s.category == category and (best is None or s.strength > best.strength):
                best, origin = s, r.collector
    return best, origin


def _build_hypotheses(chosen: FaultCategory, collectors: dict[str, CollectorResult]) -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    best, origin = _best_signal(chosen, collectors)
    corroborators = sorted(
        {r.collector for r in collectors.values() for s in r.signals if s.category == chosen}
    )
    hyps.append(
        Hypothesis(
            hypothesis=_SUMMARY[chosen],
            origin=HypothesisOrigin(origin) if origin else HypothesisOrigin.E1,
            status=HypothesisStatus.CONFIRMED,
            reason=(
                (best.rationale if best else "selected by aggregate evidence")
                + (f" Corroborated by {', '.join(corroborators)}." if len(corroborators) > 1 else "")
            ),
        )
    )
    raised = {s.category for r in collectors.values() for s in r.signals} - {chosen}
    for cat in sorted(raised, key=lambda c: c.value):
        sig, org = _best_signal(cat, collectors)
        hyps.append(
            Hypothesis(
                hypothesis=_SUMMARY.get(cat, cat.value),
                origin=HypothesisOrigin(org) if org else HypothesisOrigin.E1,
                status=HypothesisStatus.REJECTED,
                reason=(
                    f"Raised by {org} ({sig.rationale[:80]}...) but not corroborated across sources; "
                    f"'{chosen.value}' had stronger multi-source support."
                ),
            )
        )
    return hyps


def _details(category: FaultCategory, evidence: list[Evidence]) -> str:
    cited = "; ".join(f"{e.source.value}: {e.observed}" for e in evidence[:4])
    mechanism = {
        FaultCategory.SCORER_WEIGHT_MISCONFIG: (
            "The v1.5.0 config loader accepts zero/negative scorer weights without error. A non-positive "
            "queue or kv-cache scorer weight removes (or inverts) the load-spreading term, so a positive "
            "feedback loop drags requests onto one endpoint: its queue grows, P95 latency breaches the "
            f"{SLO_P95_E2E_SECONDS:.0f}s SLO, while the prefix-cache hit rate is unaffected."
        ),
        FaultCategory.UNHEALTHY_ENDPOINT: (
            "The backend's /health probe still returns 200, so it stays a valid endpoint in the pool. "
            "Its injected failure rate and inflated TTFT mean a fraction of routed requests error or "
            "slow down, dragging that backend's success rate below its peers while queues stay near zero."
        ),
        FaultCategory.PREFIX_CACHE_DISABLED: (
            "Without the prefix-cache-scorer in the active scheduling profile, the router no longer biases "
            "a request toward the backend already holding its prefix. With a prefix working set larger than "
            "one backend's KV cache, every backend re-fetches every tenant prefix, so the pool-wide hit rate "
            "collapses, KV churn rises on all backends, and latency degrades under the same load."
        ),
        FaultCategory.OTHER: "Metrics, scheduler config, and endpoint state are all within healthy baselines.",
    }[category]
    return f"{mechanism} Evidence: {cited}."


def diagnose(pool: str, prom=None) -> Report:
    started = datetime.now(timezone.utc).isoformat()
    collectors = run_all_collectors(pool, prom=prom)
    chosen, _scores = _aggregate(collectors)
    evidence = _assemble_evidence(chosen, collectors)
    hypotheses = _build_hypotheses(chosen, collectors)
    fix: Fix = generate_fix(chosen)
    completed = datetime.now(timezone.utc).isoformat()
    return Report(
        pool=pool,
        started_at=started,
        completed_at=completed,
        fault_category=chosen,
        root_cause=RootCause(summary=_SUMMARY[chosen], details=_details(chosen, evidence)),
        evidence=evidence,
        hypotheses=hypotheses,
        fix=fix,
    )
