"""E3 — ClusterState Agent (Kubernetes API only).

Reads InferencePool membership and per-backend Deployment state. Detects "looks
healthy but recently changed" endpoints — chiefly an endpoint kept in rotation
(readiness still passing) whose container args were patched to inject failures
(S2 leaves ``--failure-injection-rate`` on one backend).
"""

from __future__ import annotations

from collections import Counter

from ..config import BACKEND_DEPLOYMENTS, ENDPOINT_SELECTOR
from ..kube import KubectlError, get_json
from ..schema import Evidence, EvidenceSource, FaultCategory
from .base import CollectorResult, Signal


def _arg_value(arg: str) -> str | None:
    return arg.split("=", 1)[1] if "=" in arg else None


def _non_seed(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("--seed")]


def collect_clusterstate(pool: str) -> CollectorResult:
    res = CollectorResult(collector="E3", source=EvidenceSource.K8S_API)

    # --- InferencePool membership ----------------------------------------------
    try:
        pools = get_json("inferencepools")
        names = [p["metadata"]["name"] for p in pools.get("items", [])]
        match = next(
            (p for p in pools.get("items", []) if p["metadata"]["name"] == pool),
            pools["items"][0] if pools.get("items") else None,
        )
        selector = (match or {}).get("spec", {}).get("selector", {}) if match else {}
        res.observations.append(
            Evidence(
                source=EvidenceSource.K8S_API,
                locator="kubectl get inferencepools -o json",
                observed=f"pools={names}; selector={selector}",
                baseline="pool selects all three vllm-sim backends",
                claim="InferencePool membership / selector",
            )
        )
    except (KubectlError, KeyError) as e:
        res.errors.append(f"inferencepool read failed: {e}")

    # --- Per-backend Deployment args + readiness -------------------------------
    args_by_backend: dict[str, list[str]] = {}
    for d in BACKEND_DEPLOYMENTS:
        try:
            dep = get_json("deploy", d)
            args = dep["spec"]["template"]["spec"]["containers"][0].get("args", [])
            args_by_backend[d] = args
        except (KubectlError, KeyError, IndexError) as e:
            res.errors.append(f"deploy {d} read failed: {e}")

    # Baseline = args shared by a MAJORITY of backends (ignoring --seed), NOT the
    # intersection of all: if one backend's args diverge, the intersection shrinks
    # and the healthy backends would wrongly look off-baseline. Anomalies = a
    # backend carrying non-seed args the majority doesn't.
    if args_by_backend:
        counts = Counter(a for args in args_by_backend.values() for a in _non_seed(args))
        threshold = (len(args_by_backend) // 2) + 1  # 3 backends -> 2
        baseline = {a for a, c in counts.items() if c >= threshold}

        for d, args in args_by_backend.items():
            extra = [a for a in _non_seed(args) if a not in baseline]
            fault_args = [a for a in args if a.startswith("--failure-injection-rate")]
            fault_on = any((_arg_value(a) or "0") not in ("0", "0.0", "") for a in fault_args)

            if extra:
                ev = Evidence(
                    source=EvidenceSource.K8S_API,
                    locator=f"kubectl get deploy {d} -o jsonpath='{{.spec.template.spec.containers[0].args}}'",
                    observed=f"{d} carries args absent on peers: {extra}",
                    baseline="all backends share identical args except --seed",
                    claim=f"{d} was patched away from the baseline configuration",
                )
                res.observations.append(ev)
                if fault_on:
                    res.signals.append(
                        Signal(
                            category=FaultCategory.UNHEALTHY_ENDPOINT,
                            strength=0.95,
                            rationale=(
                                f"{d} runs with {fault_args} (and {extra}); failure injection is enabled "
                                f"on a backend that still passes its readiness probe, so the router keeps "
                                f"it in rotation while a fraction of its requests fail/slow."
                            ),
                            evidence=[ev],
                        )
                    )
                else:
                    res.signals.append(
                        Signal(
                            category=FaultCategory.UNHEALTHY_ENDPOINT,
                            strength=0.6,
                            rationale=f"{d} was patched off-baseline ({extra}); inspect for degraded behavior.",
                            evidence=[ev],
                        )
                    )

    # --- Pod readiness / restarts ----------------------------------------------
    try:
        pods = get_json("pods", "-l", ENDPOINT_SELECTOR)
        rows = []
        for pod in pods.get("items", []):
            cs = (pod.get("status", {}).get("containerStatuses") or [{}])[0]
            be = pod.get("metadata", {}).get("labels", {}).get("backend", "?")
            rows.append(f"{be}:ready={cs.get('ready')},restarts={cs.get('restartCount', 0)}")
        res.observations.append(
            Evidence(
                source=EvidenceSource.K8S_API,
                locator=f"kubectl get pods -l {ENDPOINT_SELECTOR}",
                observed="; ".join(rows),
                baseline="all backends ready, 0 restarts",
                claim="backend pod readiness and restart counts",
            )
        )
    except (KubectlError, KeyError) as e:
        res.errors.append(f"pod readiness read failed: {e}")

    res.summary = (
        f"backends_read={list(args_by_backend)}; "
        f"anomalies={[s.rationale.split(' runs')[0].split(' was')[0] for s in res.signals] or 'none'}"
    )
    return res
