"""Deterministic collector tests with mocked Prometheus/kubectl (RUBRIC B3).

These lock the S1/S2/S3 discrimination logic without needing a live cluster.
"""

from __future__ import annotations

import yaml

from routecause.collectors import clusterstate, metrics, schedconfig
from routecause.collectors.metrics import (
    Q_HIT,
    Q_KV,
    Q_P95E2E,
    Q_P95TTFT,
    Q_QUEUE,
    Q_RPS,
    Q_TTFT_BE,
    collect_metrics,
)
from routecause.schema import FaultCategory


class FakeProm:
    def __init__(self, backend_maps: dict, scalars: dict):
        self._b = backend_maps
        self._s = scalars

    def by_backend(self, q: str) -> dict:
        return dict(self._b.get(q, {}))

    def scalar(self, q: str):
        return self._s.get(q)


def _categories(result) -> set:
    return {s.category for s in result.signals}


# ---------------------------------------------------------------- E1 (metrics)


def _prom(queue, rps, hit, ttft_be=None, p95e2e=8.0, kv=None):
    ttft_be = ttft_be or {b: 1.9 for b in rps}
    kv = kv or {b: 0.5 for b in rps}
    return FakeProm(
        backend_maps={Q_QUEUE: queue, Q_RPS: rps, Q_TTFT_BE: ttft_be, Q_KV: kv},
        scalars={Q_HIT: hit, Q_P95E2E: p95e2e, Q_P95TTFT: 1.9},
    )


def test_e1_healthy_has_no_signals():
    r = collect_metrics("p", prom=_prom({"b0": 0, "b1": 0, "b2": 0}, {"b0": 3, "b1": 3, "b2": 3}, 0.70))
    assert r.signals == []


def test_e1_s1_queue_concentration():
    r = collect_metrics("p", prom=_prom({"b0": 88, "b1": 0, "b2": 0}, {"b0": 5, "b1": 0, "b2": 0}, 0.70, p95e2e=19.7))
    assert FaultCategory.SCORER_WEIGHT_MISCONFIG in _categories(r)
    assert FaultCategory.UNHEALTHY_ENDPOINT not in _categories(r)


def test_e1_s1_queue_concentration_before_rps_decays():
    # Ramp case: queue already piled on b0 but the 2m rps window still looks balanced.
    r = collect_metrics("p", prom=_prom({"b0": 88, "b1": 0, "b2": 0}, {"b0": 3, "b1": 3, "b2": 3}, 0.73, p95e2e=13.1))
    assert FaultCategory.SCORER_WEIGHT_MISCONFIG in _categories(r)
    assert FaultCategory.UNHEALTHY_ENDPOINT not in _categories(r)


def test_e1_s2_one_backend_degraded():
    r = collect_metrics(
        "p",
        prom=_prom(
            {"b0": 0, "b1": 0, "b2": 0},
            {"b0": 3, "b1": 3, "b2": 0.8},
            0.70,
            ttft_be={"b0": 1.9, "b1": 1.9, "b2": 5.0},
            p95e2e=14.7,
        ),
    )
    assert FaultCategory.UNHEALTHY_ENDPOINT in _categories(r)
    assert FaultCategory.SCORER_WEIGHT_MISCONFIG not in _categories(r)


def test_e1_s3_hit_rate_collapse():
    r = collect_metrics("p", prom=_prom({"b0": 0, "b1": 0, "b2": 0}, {"b0": 3, "b1": 3, "b2": 3}, 0.45))
    assert FaultCategory.PREFIX_CACHE_DISABLED in _categories(r)
    assert FaultCategory.UNHEALTHY_ENDPOINT not in _categories(r)


# ---------------------------------------------------------------- E2 (config)

_HEALTHY_CFG = {
    "plugins": [
        {"type": "queue-scorer"},
        {"type": "kv-cache-utilization-scorer"},
        {"type": "prefix-cache-scorer"},
        {"type": "max-score-picker"},
        {"type": "metrics-data-source"},
        {"type": "core-metrics-extractor"},
    ],
    "schedulingProfiles": [
        {
            "name": "default",
            "plugins": [
                {"pluginRef": "queue-scorer", "weight": 2},
                {"pluginRef": "kv-cache-utilization-scorer", "weight": 2},
                {"pluginRef": "prefix-cache-scorer", "weight": 3},
                {"pluginRef": "max-score-picker"},
            ],
        }
    ],
    "dataLayer": {"sources": [{"pluginRef": "metrics-data-source"}]},
}


def _patch_cm(monkeypatch, cfg: dict):
    def fake_get_json(*args, **kw):
        return {"data": {"config.yaml": yaml.safe_dump(cfg)}}

    monkeypatch.setattr(schedconfig, "get_json", fake_get_json)


def test_e2_healthy_has_no_signals(monkeypatch):
    _patch_cm(monkeypatch, _HEALTHY_CFG)
    r = schedconfig.collect_schedconfig("p")
    assert r.signals == []


def test_e2_negative_weight_is_s1(monkeypatch):
    import copy

    cfg = copy.deepcopy(_HEALTHY_CFG)
    cfg["schedulingProfiles"][0]["plugins"][0]["weight"] = -2  # queue-scorer
    _patch_cm(monkeypatch, cfg)
    r = schedconfig.collect_schedconfig("p")
    assert FaultCategory.SCORER_WEIGHT_MISCONFIG in _categories(r)


def test_e2_missing_prefix_scorer_is_s3(monkeypatch):
    import copy

    cfg = copy.deepcopy(_HEALTHY_CFG)
    cfg["schedulingProfiles"][0]["plugins"] = [
        p for p in cfg["schedulingProfiles"][0]["plugins"] if p.get("pluginRef") != "prefix-cache-scorer"
    ]
    _patch_cm(monkeypatch, cfg)
    r = schedconfig.collect_schedconfig("p")
    assert FaultCategory.PREFIX_CACHE_DISABLED in _categories(r)


def test_e2_missing_datalayer_is_other(monkeypatch):
    import copy

    cfg = copy.deepcopy(_HEALTHY_CFG)
    del cfg["dataLayer"]
    _patch_cm(monkeypatch, cfg)
    r = schedconfig.collect_schedconfig("p")
    assert FaultCategory.OTHER in _categories(r)


def test_e2_tolerates_unknown_plugin(monkeypatch):
    import copy

    cfg = copy.deepcopy(_HEALTHY_CFG)
    cfg["plugins"].append({"type": "some-future-scorer-v9"})
    _patch_cm(monkeypatch, cfg)
    r = schedconfig.collect_schedconfig("p")  # must not raise
    assert r.errors == []


# ---------------------------------------------------------------- E3 (cluster)

_BASE_ARGS = [
    "--port=8000",
    "--model=meta-llama/Llama-3.1-8B-Instruct",
    "--mode=echo",
    "--enable-kvcache",
    "--max-num-seqs=8",
]


def _deploy(name: str, args: list[str]) -> dict:
    return {"spec": {"template": {"spec": {"containers": [{"args": args}]}}}}


def _pods() -> dict:
    return {
        "items": [
            {
                "metadata": {"labels": {"backend": b}},
                "status": {"containerStatuses": [{"ready": True, "restartCount": 0}]},
            }
            for b in ("b0", "b1", "b2")
        ]
    }


def _patch_cluster(monkeypatch, args_by_backend: dict):
    pools = {"items": [{"metadata": {"name": "vllm-sim-pool"}, "spec": {"selector": {"app": "vllm-sim"}}}]}

    def fake_get_json(*args, **kw):
        if args[0] == "inferencepools":
            return pools
        if args[0] == "deploy":
            name = args[1]
            return _deploy(name, args_by_backend[name])
        if args[0] == "pods":
            return _pods()
        raise AssertionError(f"unexpected get_json args: {args}")

    monkeypatch.setattr(clusterstate, "get_json", fake_get_json)


def test_e3_healthy_identical_args_has_no_signals(monkeypatch):
    _patch_cluster(
        monkeypatch,
        {
            "backend-0": _BASE_ARGS + ["--seed=100"],
            "backend-1": _BASE_ARGS + ["--seed=101"],
            "backend-2": _BASE_ARGS + ["--seed=102"],
        },
    )
    r = clusterstate.collect_clusterstate("vllm-sim-pool")
    assert r.signals == []


def test_e3_majority_baseline_only_flags_culprit(monkeypatch):
    # b2 diverges on latency args + failure injection; b0/b1 share the baseline.
    # Majority baseline must flag ONLY b2 (intersection-of-all would wrongly flag b0/b1).
    base = ["--port=8000", "--prefill-time-per-token=2ms", "--inter-token-latency=25ms"]
    _patch_cluster(
        monkeypatch,
        {
            "backend-0": base + ["--seed=100"],
            "backend-1": base + ["--seed=101"],
            "backend-2": ["--port=8000", "--prefill-time-per-token=10ms",
                          "--inter-token-latency=40ms", "--failure-injection-rate=25", "--seed=102"],
        },
    )
    r = clusterstate.collect_clusterstate("vllm-sim-pool")
    assert FaultCategory.UNHEALTHY_ENDPOINT in _categories(r)
    assert len(r.signals) == 1, "only the culprit backend should be flagged"
    assert "backend-2" in r.signals[0].rationale


def test_e3_failure_injection_is_s2(monkeypatch):
    _patch_cluster(
        monkeypatch,
        {
            "backend-0": _BASE_ARGS + ["--seed=100"],
            "backend-1": _BASE_ARGS + ["--seed=101"],
            "backend-2": _BASE_ARGS + ["--failure-injection-rate=25", "--seed=102"],
        },
    )
    r = clusterstate.collect_clusterstate("vllm-sim-pool")
    assert FaultCategory.UNHEALTHY_ENDPOINT in _categories(r)
    top = r.top_signal()
    assert top is not None and top.category == FaultCategory.UNHEALTHY_ENDPOINT
