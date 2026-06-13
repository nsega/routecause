"""End-to-end orchestrator test on a mocked diagnosis path (RUBRIC B3).

Runs the full deterministic pipeline (collectors -> aggregate -> evidence ->
hypotheses -> report) against mocked Prometheus/kubectl, with the live fix step
stubbed. No cluster or API key required.
"""

from __future__ import annotations

import copy

import yaml

from routecause import orchestrator
from routecause.collectors import clusterstate, schedconfig
from routecause.collectors.metrics import Q_HIT, Q_KV, Q_P95E2E, Q_P95TTFT, Q_QUEUE, Q_RPS, Q_TTFT_BE
from routecause.schema import FaultCategory, Fix, FixType


class FakeProm:
    def __init__(self, b, s):
        self._b, self._s = b, s

    def by_backend(self, q):
        return dict(self._b.get(q, {}))

    def scalar(self, q):
        return self._s.get(q)


_S1_PROM = FakeProm(
    {Q_QUEUE: {"b0": 88, "b1": 0, "b2": 0}, Q_RPS: {"b0": 5, "b1": 0, "b2": 0},
     Q_TTFT_BE: {"b0": 2.0, "b1": 1.9, "b2": 1.9}, Q_KV: {"b0": 0.6, "b1": 0.5, "b2": 0.5}},
    {Q_HIT: 0.73, Q_P95E2E: 19.8, Q_P95TTFT: 1.9},
)

_S1_CFG = {
    "plugins": [{"type": "queue-scorer"}, {"type": "prefix-cache-scorer"},
                {"type": "metrics-data-source"}, {"type": "core-metrics-extractor"}],
    "schedulingProfiles": [{"name": "default", "plugins": [
        {"pluginRef": "queue-scorer", "weight": -2},
        {"pluginRef": "prefix-cache-scorer", "weight": 3},
    ]}],
    "dataLayer": {"sources": [{"pluginRef": "metrics-data-source"}]},
}

_HEALTHY_ARGS = ["--port=8000", "--mode=echo", "--enable-kvcache"]


def _setup_s1(monkeypatch):
    monkeypatch.setattr(schedconfig, "get_json",
                        lambda *a, **k: {"data": {"config.yaml": yaml.safe_dump(_S1_CFG)}})

    def fake_cluster(*args, **kw):
        if args[0] == "inferencepools":
            return {"items": [{"metadata": {"name": "vllm-sim-pool"}, "spec": {"selector": {"app": "vllm-sim"}}}]}
        if args[0] == "deploy":
            seed = {"backend-0": "100", "backend-1": "101", "backend-2": "102"}[args[1]]
            return {"spec": {"template": {"spec": {"containers": [{"args": _HEALTHY_ARGS + [f"--seed={seed}"]}]}}}}
        if args[0] == "pods":
            return {"items": [{"metadata": {"labels": {"backend": b}},
                               "status": {"containerStatuses": [{"ready": True, "restartCount": 0}]}}
                              for b in ("b0", "b1", "b2")]}
        raise AssertionError(args)

    monkeypatch.setattr(clusterstate, "get_json", fake_cluster)
    monkeypatch.setattr(
        orchestrator, "generate_fix",
        lambda cat: Fix(type=FixType.MANIFEST_DIFF, diff="--- restore weight: -2 -> 2",
                        dry_run_validated=True, expected_impact="queue rebalances"),
    )


def test_orchestrator_diagnoses_s1_end_to_end(monkeypatch):
    _setup_s1(monkeypatch)
    report = orchestrator.diagnose("vllm-sim-pool", prom=_S1_PROM, use_llm=False)

    assert report.fault_category == FaultCategory.SCORER_WEIGHT_MISCONFIG
    assert report.schema_version == "1.0"
    # A3: >= 2 distinct evidence sources
    assert len({e.source for e in report.evidence}) >= 2
    # A4: a validated, actionable fix
    assert report.fix.dry_run_validated is True
    assert report.fix.diff
    # confirmed root-cause hypothesis present
    assert any(h.status.value == "confirmed" for h in report.hypotheses)
    # the report serializes
    assert report.model_dump_json()


def test_orchestrator_healthy_is_other(monkeypatch):
    healthy_prom = FakeProm(
        {Q_QUEUE: {"b0": 0, "b1": 0, "b2": 0}, Q_RPS: {"b0": 3, "b1": 3, "b2": 3},
         Q_TTFT_BE: {"b0": 1.9, "b1": 1.9, "b2": 1.9}, Q_KV: {"b0": 0.5, "b1": 0.5, "b2": 0.5}},
        {Q_HIT: 0.72, Q_P95E2E: 8.0, Q_P95TTFT: 1.9},
    )
    cfg = copy.deepcopy(_S1_CFG)
    cfg["schedulingProfiles"][0]["plugins"][0]["weight"] = 2  # healthy
    monkeypatch.setattr(schedconfig, "get_json",
                        lambda *a, **k: {"data": {"config.yaml": yaml.safe_dump(cfg)}})

    def fake_cluster(*args, **kw):
        if args[0] == "inferencepools":
            return {"items": [{"metadata": {"name": "vllm-sim-pool"}, "spec": {"selector": {}}}]}
        if args[0] == "deploy":
            return {"spec": {"template": {"spec": {"containers": [{"args": _HEALTHY_ARGS + ["--seed=1"]}]}}}}
        return {"items": []}

    monkeypatch.setattr(clusterstate, "get_json", fake_cluster)
    monkeypatch.setattr(orchestrator, "generate_fix",
                        lambda cat: Fix(type=FixType.MANIFEST_DIFF, diff="(no change)",
                                        dry_run_validated=False, expected_impact="no action"))

    report = orchestrator.diagnose("vllm-sim-pool", prom=healthy_prom, use_llm=False)
    assert report.fault_category == FaultCategory.OTHER
