"""LLM agent layer — isolated hypothesis subagents + independent refuters.

The brief's inner-loop architecture, made real with the Anthropic API:

- One hypothesis subagent per evidence source (E1/E2/E3). Each sees ONLY its own
  collector's evidence, so no single context owns all the evidence
  (avoids self-preferential bias). They run in parallel.
- One refuter per candidate fault, in a fresh context, mandated to *falsify* it
  against the full evidence bundle. A hypothesis must survive refutation to be
  confirmed.

Every prompt is pinned to the lab's vendored versions (scheduler v0.8.0 /
GIE v1.5.0) and told the live cluster is the only source of truth — no web, no
training-data scorer names. The whole layer is best-effort: if the API key is
absent or a call fails, the orchestrator falls back to its deterministic core,
so a diagnosis always completes (A1).
"""

from __future__ import annotations

import asyncio

import anthropic
from pydantic import BaseModel, Field

from .collectors import CollectorResult
from .config import GIE_VERSION, ROUTER_DISPLAY_NAME, SCHEDULER_VERSION, get_settings
from .schema import FaultCategory, HypothesisStatus

_MAX_CONCURRENCY = 5

_SYSTEM = (
    f"You are a diagnostic subagent for RouteCause, analyzing an {ROUTER_DISPLAY_NAME} "
    f"inference gateway on Kubernetes. Pin ALL reasoning to the lab's vendored versions: "
    f"llm-d-inference-scheduler {SCHEDULER_VERSION} / Gateway API Inference Extension {GIE_VERSION}. "
    f"Do NOT rely on newer scorer names or config schemas from your training data. The live cluster "
    f"is the only source of truth — reason only over the evidence you are shown; never invent metrics "
    f"or config lines. The four fault categories are: scorer-weight-misconfig (a scheduling scorer "
    f"weight is <= 0), unhealthy-endpoint-in-rotation (a backend fails/slows but stays Ready), "
    f"prefix-cache-routing-disabled (prefix-cache-scorer missing from the profile), or other."
)

# Per-source framing so each agent reasons within its own lane.
_SOURCE_BRIEF = {
    "E1": (
        "You analyze PROMETHEUS METRICS only. Discrimination signatures measured in this lab:\n"
        "- queue depth piled on ONE backend (others ~0 rps), hit rate unchanged => scorer-weight-misconfig\n"
        "- one backend lowest success rps + elevated TTFT, queues ~0, hit rate unchanged => unhealthy-endpoint\n"
        "- pool prefix-cache hit rate collapses (~0.72 -> ~0.45), KV churn up on all, queues ~0 => prefix-cache-routing-disabled"
    ),
    "E2": (
        "You analyze the EPP scheduler CONFIG only (epp-config ConfigMap). Invariants: every scheduling "
        "scorer weight must be > 0 (the loader silently accepts 0/negative); prefix-cache-scorer must be "
        "present in both the plugin defs and the scheduling profile; the dataLayer section is required."
    ),
    "E3": (
        "You analyze KUBERNETES CLUSTER STATE only (InferencePool membership, per-backend Deployment args, "
        "readiness). A backend patched off-baseline (e.g. carrying --failure-injection-rate) while still "
        "Ready indicates an unhealthy endpoint kept in rotation."
    ),
}


class _LLMHypothesis(BaseModel):
    fault_category: FaultCategory
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class _LLMHypothesisList(BaseModel):
    hypotheses: list[_LLMHypothesis]


class _Verdict(BaseModel):
    verdict: HypothesisStatus  # confirmed | rejected
    reason: str
    supporting_evidence: list[str] = Field(default_factory=list)


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)


def _collector_evidence_text(r: CollectorResult) -> str:
    lines = [f"# Source {r.collector} ({r.source.value}) — summary: {r.summary}"]
    if r.errors:
        lines.append(f"errors: {r.errors}")
    lines.append("observations:")
    for o in r.observations:
        lines.append(f"  - {o.claim}: observed={o.observed!r} baseline={o.baseline!r} [{o.locator}]")
    if r.signals:
        lines.append("deterministic_signals:")
        for s in r.signals:
            lines.append(f"  - {s.category.value} (strength {s.strength}): {s.rationale}")
    return "\n".join(lines)


async def _hypothesize(r: CollectorResult, model: str, sem: asyncio.Semaphore) -> tuple[str, list[_LLMHypothesis]]:
    user = (
        f"{_SOURCE_BRIEF.get(r.collector, '')}\n\n"
        f"Evidence available to you (this source only):\n{_collector_evidence_text(r)}\n\n"
        "From THIS source alone, list the fault hypotheses the evidence supports, each with a "
        "confidence 0-1 and a one-sentence rationale grounded in the evidence above. If the evidence "
        "looks healthy from your source, return an empty list."
    )
    async with sem:
        resp = await _client().messages.parse(
            model=model, max_tokens=1500, system=_SYSTEM,
            messages=[{"role": "user", "content": user}], output_format=_LLMHypothesisList,
        )
    return r.collector, resp.parsed_output.hypotheses


async def _refute(category: FaultCategory, full_evidence: str, model: str, sem: asyncio.Semaphore) -> _Verdict:
    user = (
        f"Candidate root cause: {category.value}.\n\n"
        f"FULL evidence bundle gathered read-only from the live cluster:\n{full_evidence}\n\n"
        "Your mandate is to FALSIFY this candidate. Look for evidence that contradicts it or that "
        "better fits a different category. If you cannot falsify it, return verdict 'confirmed' and "
        "cite at least 2 concrete supporting pieces (a metric name with its value, or a config line). "
        "If the evidence contradicts it, return 'rejected' with the reason."
    )
    async with sem:
        resp = await _client().messages.parse(
            model=model, max_tokens=1200, system=_SYSTEM,
            messages=[{"role": "user", "content": user}], output_format=_Verdict,
        )
    return resp.parsed_output


async def hypothesize_all(collectors: dict[str, CollectorResult]) -> dict[str, list[_LLMHypothesis]]:
    model = get_settings().subagent_model
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    pairs = await asyncio.gather(
        *(_hypothesize(r, model, sem) for r in collectors.values()),
        return_exceptions=True,
    )
    out: dict[str, list[_LLMHypothesis]] = {}
    for p in pairs:
        if isinstance(p, Exception):
            continue
        name, hyps = p
        out[name] = hyps
    return out


async def refute_all(categories: list[FaultCategory], collectors: dict[str, CollectorResult]) -> dict[FaultCategory, _Verdict]:
    model = get_settings().subagent_model
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    full = "\n\n".join(_collector_evidence_text(r) for r in collectors.values())
    verdicts = await asyncio.gather(
        *(_refute(c, full, model, sem) for c in categories),
        return_exceptions=True,
    )
    out: dict[FaultCategory, _Verdict] = {}
    for cat, v in zip(categories, verdicts):
        if not isinstance(v, Exception):
            out[cat] = v
    return out
