"""E1 — Metrics Agent (Prometheus only).

Reads the six fixed series and derives metrics-only signals using the lab's
measured discrimination signatures. Forms hypotheses from metrics alone.
"""

from __future__ import annotations

import statistics

from ..config import BASELINE, SLO_P95_E2E_SECONDS
from ..prom import PrometheusClient, PrometheusError
from ..schema import Evidence, EvidenceSource, FaultCategory
from .base import CollectorResult, Signal

# Fixed queries (confirmed against the live lab; label is `backend`=b0|b1|b2).
Q_QUEUE = "sum by (backend) (vllm:num_requests_waiting)"
Q_KV = "sum by (backend) (vllm:kv_cache_usage_perc)"
Q_HIT = "sum(rate(vllm:prefix_cache_hits[2m])) / sum(rate(vllm:prefix_cache_queries[2m]))"
Q_P95E2E = "histogram_quantile(0.95, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[2m])))"
Q_P95TTFT = "histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[2m])))"
Q_RPS = "sum by (backend) (rate(vllm:request_success_total[2m]))"
Q_TTFT_BE = "histogram_quantile(0.95, sum by (le, backend) (rate(vllm:time_to_first_token_seconds_bucket[2m])))"

# Discrimination thresholds (derived from the lab's healthy baseline + drills).
IDLE_RPS = 0.2  # a backend below this is effectively receiving no traffic
QUEUE_SPIKE = 5.0  # queue depth above this on one backend is a pile-up
HIT_RATE_FLOOR = 0.60  # below this the prefix-cache hit rate has collapsed
S2_RPS_RATIO = 0.6  # degraded backend success rps < this * peer median


def _fmt_map(m: dict[str, float], pct: bool = False) -> str:
    items = sorted(m.items())
    if pct:
        return ", ".join(f"{k}={v:.2f}" for k, v in items)
    return ", ".join(f"{k}={v:.2f}" for k, v in items)


def collect_metrics(pool: str, prom: PrometheusClient | None = None) -> CollectorResult:
    prom = prom or PrometheusClient()
    res = CollectorResult(collector="E1", source=EvidenceSource.PROMETHEUS)
    try:
        queue = prom.by_backend(Q_QUEUE)
        kv = prom.by_backend(Q_KV)
        rps = prom.by_backend(Q_RPS)
        ttft_be = prom.by_backend(Q_TTFT_BE)
        hit = prom.scalar(Q_HIT)
        p95e2e = prom.scalar(Q_P95E2E)
        p95ttft = prom.scalar(Q_P95TTFT)
    except PrometheusError as e:
        res.errors.append(str(e))
        res.summary = "E1 could not read Prometheus"
        return res

    ev_queue = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_QUEUE,
        observed=_fmt_map(queue), baseline="~0 on every backend",
        claim="per-backend queue depth",
    )
    ev_rps = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_RPS,
        observed=_fmt_map(rps), baseline="balanced across backends (~2-3 rps each)",
        claim="per-backend successful request rate",
    )
    ev_hit = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_HIT,
        observed=f"{hit:.3f}" if hit is not None else "no data",
        baseline=f"~{BASELINE['prefix_hit_rate']:.2f}",
        claim="pool-wide prefix-cache hit rate",
    )
    ev_p95 = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_P95E2E,
        observed=f"{p95e2e:.2f}s" if p95e2e is not None else "no data",
        baseline=f"~{BASELINE['p95_e2e_seconds']:.0f}s (SLO {SLO_P95_E2E_SECONDS:.0f}s)",
        claim="pool-wide P95 end-to-end latency",
    )
    ev_ttft = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_TTFT_BE,
        observed=_fmt_map(ttft_be), baseline=f"~{BASELINE['p95_ttft_seconds']:.1f}s each",
        claim="per-backend P95 time-to-first-token",
    )
    ev_kv = Evidence(
        source=EvidenceSource.PROMETHEUS, locator=Q_KV,
        observed=_fmt_map(kv), baseline="moderate, balanced across backends",
        claim="per-backend KV-cache utilization",
    )
    res.observations.extend([ev_queue, ev_rps, ev_hit, ev_p95, ev_ttft, ev_kv])

    idle = [b for b, v in rps.items() if v < IDLE_RPS]
    max_queue_backend = max(queue, key=queue.get) if queue else None
    max_queue = max(queue.values()) if queue else 0.0
    total_queue = sum(queue.values()) if queue else 0.0
    others_queue = total_queue - max_queue
    slo_breached = p95e2e is not None and p95e2e > SLO_P95_E2E_SECONDS

    # --- S1: routing concentrated on one backend (queue piles on ONE, others ~0) ---
    # Queue concentration alone is the S1 signature (S2/S3 keep queues ~balanced/0).
    # We do NOT require idle rps: the 2m success-rps window lags inject by ~2 min, but
    # the queue pile-up shows within ~1 min. Idle backends just raise the strength.
    queue_concentrated = max_queue > QUEUE_SPIKE and others_queue <= 0.25 * max_queue
    if queue_concentrated:
        strength = 0.9 if len(idle) >= 2 else (0.8 if len(idle) == 1 else 0.75)
        hit_note = f"; prefix hit rate {hit:.2f} unchanged" if hit is not None else ""
        idle_note = (
            f"; {len(idle)} backend(s) already at ~0 rps ({', '.join(idle)})" if idle else ""
        )
        res.signals.append(
            Signal(
                category=FaultCategory.SCORER_WEIGHT_MISCONFIG,
                strength=strength,
                rationale=(
                    f"queue depth {max_queue:.0f} is concentrated on {max_queue_backend} "
                    f"({others_queue:.0f} queued across all other backends) — routing is skewed onto "
                    f"a single endpoint rather than spread by score{idle_note}{hit_note}"
                ),
                evidence=[ev_queue, ev_rps],
            )
        )

    # --- S3: prefix-cache hit rate collapse, KV churn, queues balanced ---
    if hit is not None and hit < HIT_RATE_FLOOR and max_queue <= QUEUE_SPIKE:
        res.signals.append(
            Signal(
                category=FaultCategory.PREFIX_CACHE_DISABLED,
                strength=0.9,
                rationale=(
                    f"pool prefix-cache hit rate {hit:.2f} collapsed vs ~{BASELINE['prefix_hit_rate']:.2f} "
                    f"baseline with queues balanced (~{max_queue:.0f}) and no rps concentration — "
                    f"prefix-aware routing is not taking effect"
                ),
                evidence=[ev_hit, ev_kv, ev_queue],
            )
        )

    # --- S2: one backend degraded (low success rps + elevated TTFT), queues low ---
    if rps and max_queue <= QUEUE_SPIKE and not idle:
        median_rps = statistics.median(rps.values())
        low_b = min(rps, key=rps.get)
        peer_ttft = [v for b, v in ttft_be.items() if b != low_b]
        ttft_elevated = bool(peer_ttft) and ttft_be.get(low_b, 0) > 1.5 * statistics.median(peer_ttft)
        if median_rps > 0 and rps[low_b] < S2_RPS_RATIO * median_rps:
            res.signals.append(
                Signal(
                    category=FaultCategory.UNHEALTHY_ENDPOINT,
                    strength=0.8 if ttft_elevated else 0.55,
                    rationale=(
                        f"{low_b} success rps {rps[low_b]:.2f} is well below peer median {median_rps:.2f}; "
                        f"its P95 TTFT {ttft_be.get(low_b, 0):.2f}s"
                        + (" is elevated vs peers" if ttft_elevated else "")
                        + f"; queues ~{max_queue:.0f} and hit rate "
                        + (f"{hit:.2f}" if hit is not None else "n/a")
                        + " unchanged — one endpoint failing/slow but kept in rotation"
                    ),
                    evidence=[ev_rps, ev_ttft, ev_queue],
                )
            )

    breach_note = f"; P95 e2e {p95e2e:.1f}s BREACHES SLO {SLO_P95_E2E_SECONDS:.0f}s" if slo_breached else ""
    res.summary = (
        f"queue_max={max_queue:.0f}@{max_queue_backend}; idle_backends={idle or 'none'}; "
        f"hit_rate={'%.2f' % hit if hit is not None else 'n/a'}; "
        f"p95_e2e={'%.1fs' % p95e2e if p95e2e is not None else 'n/a'}{breach_note}"
    )
    return res
