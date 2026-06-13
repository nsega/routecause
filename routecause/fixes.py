"""Deterministic fix generation, validated by server-side dry-run (A4).

Fixes are computed from the *live* cluster (read-only), corrected, then validated
with ``kubectl apply --dry-run=server`` before they are allowed into a report.
``dry_run_validated`` is set true only when that validation passes. Generating a
fix never mutates the cluster — application is the Actuator's job (stretch).
"""

from __future__ import annotations

import copy
import difflib
from collections import Counter

import yaml

from .config import (
    BACKEND_DEPLOYMENTS,
    EPP_CONFIG_KEY,
    EPP_CONFIGMAP,
    HEALTHY_SCORER_WEIGHTS,
    KUBE_CONTEXT,
    NAMESPACE,
    SLO_P95_E2E_SECONDS,
)
from .kube import apply_dry_run_server, get_json
from .schema import FaultCategory, Fix, FixType

_APPLY = f"kubectl --context {KUBE_CONTEXT} -n {NAMESPACE} apply -f -"
_RELOAD = f"kubectl --context {KUBE_CONTEXT} -n {NAMESPACE} rollout restart deploy/epp"


def _unified_diff(before: str, after: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _sanitize(obj: dict) -> dict:
    """Strip server-managed fields so the object is cleanly re-appliable."""
    obj = copy.deepcopy(obj)
    obj.pop("status", None)
    md = obj.get("metadata", {})
    for k in ("managedFields", "resourceVersion", "uid", "creationTimestamp", "generation", "selfLink"):
        md.pop(k, None)
    ann = md.get("annotations", {}) or {}
    ann.pop("kubectl.kubernetes.io/last-applied-configuration", None)
    ann.pop("deployment.kubernetes.io/revision", None)
    if not ann:
        md.pop("annotations", None)
    tmpl_md = obj.get("spec", {}).get("template", {}).get("metadata", {})
    tmpl_md.pop("creationTimestamp", None)
    return obj


def _configmap_manifest(config_yaml: str) -> str:
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": EPP_CONFIGMAP, "namespace": NAMESPACE},
        "data": {EPP_CONFIG_KEY: config_yaml},
    }
    return yaml.safe_dump(manifest, sort_keys=False)


def _live_epp_config() -> tuple[dict, str]:
    cm = get_json("configmap", EPP_CONFIGMAP)
    cfg = yaml.safe_load(cm["data"][EPP_CONFIG_KEY])
    return cfg, yaml.safe_dump(cfg, sort_keys=False)


def _manifest_fix(before_yaml: str, after_yaml: str, manifest: str, path: str, impact: str, reload_needed: bool) -> Fix:
    ok, _ = apply_dry_run_server(manifest)
    cmd = f"{_APPLY} <<'EOF'\n{manifest}EOF"
    if reload_needed:
        cmd += f"\n{_RELOAD}"
    return Fix(
        type=FixType.MANIFEST_DIFF,
        diff=_unified_diff(before_yaml, after_yaml, path),
        command=cmd,
        dry_run_validated=ok,
        expected_impact=impact,
    )


def fix_scorer_weights() -> Fix:
    """S1: restore every scheduling-profile scorer weight to its healthy positive value."""
    cfg, before_yaml = _live_epp_config()
    corrected = copy.deepcopy(cfg)
    changed: list[str] = []
    for prof in corrected.get("schedulingProfiles", []) or []:
        for pl in prof.get("plugins", []) or []:
            ref = pl.get("pluginRef", "")
            if "scorer" in ref and "weight" in pl:
                healthy = HEALTHY_SCORER_WEIGHTS.get(ref)
                if healthy is not None and pl["weight"] != healthy:
                    changed.append(f"{ref} {pl['weight']}->{healthy}")
                    pl["weight"] = healthy
    after_yaml = yaml.safe_dump(corrected, sort_keys=False)
    impact = (
        f"restores positive scorer weights ({', '.join(changed) or 'none changed'}); the "
        f"{HEALTHY_SCORER_WEIGHTS['queue-scorer']}/{HEALTHY_SCORER_WEIGHTS['kv-cache-utilization-scorer']}/"
        f"{HEALTHY_SCORER_WEIGHTS['prefix-cache-scorer']} queue/kv/prefix weighting resumes spreading "
        f"requests by score. After the EPP reloads, queue depth returns to ~0 across backends and "
        f"P95 e2e falls back under the {SLO_P95_E2E_SECONDS:.0f}s SLO within ~3 minutes."
    )
    return _manifest_fix(before_yaml, after_yaml, _configmap_manifest(after_yaml),
                         f"{EPP_CONFIGMAP}/{EPP_CONFIG_KEY}", impact, reload_needed=True)


def fix_prefix_cache_disabled() -> Fix:
    """S3: re-add prefix-cache-scorer to the plugins and the scheduling profile."""
    cfg, before_yaml = _live_epp_config()
    corrected = copy.deepcopy(cfg)
    plugin_types = [p.get("type") for p in corrected.get("plugins", []) or []]
    if "prefix-cache-scorer" not in plugin_types:
        corrected.setdefault("plugins", []).append(
            {"type": "prefix-cache-scorer",
             "parameters": {"maxPrefixBlocksToMatch": 256, "lruCapacityPerServer": 31250}}
        )
    for prof in corrected.get("schedulingProfiles", []) or []:
        plugins = prof.setdefault("plugins", [])
        refs = [pl.get("pluginRef") for pl in plugins]
        if "prefix-cache-scorer" not in refs:
            idx = next((i for i, pl in enumerate(plugins) if "picker" in (pl.get("pluginRef") or "")), len(plugins))
            plugins.insert(idx, {"pluginRef": "prefix-cache-scorer", "weight": HEALTHY_SCORER_WEIGHTS["prefix-cache-scorer"]})
    after_yaml = yaml.safe_dump(corrected, sort_keys=False)
    impact = (
        "re-enables prefix-aware routing (prefix-cache-scorer weight "
        f"{HEALTHY_SCORER_WEIGHTS['prefix-cache-scorer']}); requests for a shared tenant prefix are "
        "again homed to the backend already holding it. Pool prefix-cache hit rate climbs back toward "
        "~0.72, KV churn subsides, and latency recovers within ~3 minutes after the EPP reloads."
    )
    return _manifest_fix(before_yaml, after_yaml, _configmap_manifest(after_yaml),
                         f"{EPP_CONFIGMAP}/{EPP_CONFIG_KEY}", impact, reload_needed=True)


def _fault_on(args: list[str]) -> bool:
    for a in args:
        if a.startswith("--failure-injection-rate"):
            val = a.split("=", 1)[1] if "=" in a else "0"
            if val not in ("0", "0.0", ""):
                return True
    return False


def fix_unhealthy_endpoint() -> Fix:
    """S2: restore the degraded backend's container args to the healthy peer baseline."""
    deploys = {d: get_json("deploy", d) for d in BACKEND_DEPLOYMENTS}
    args_by = {
        d: dep["spec"]["template"]["spec"]["containers"][0].get("args", [])
        for d, dep in deploys.items()
    }
    # Majority baseline: an arg shared by >= 2 backends is the baseline.
    counts = Counter(a for args in args_by.values() for a in args if not a.startswith("--seed"))
    baseline = {a for a, c in counts.items() if c >= 2}

    def divergence(args: list[str]) -> int:
        return sum(1 for a in args if not a.startswith("--seed") and a not in baseline)

    # Target the backend with active failure injection; else the most off-baseline one.
    target = next((d for d, args in args_by.items() if _fault_on(args)), None)
    if target is None:
        target = max(args_by, key=lambda d: divergence(args_by[d]))
        if divergence(args_by[target]) == 0:
            target = None
    if target is None:
        return Fix(type=FixType.MANIFEST_DIFF, diff="(no off-baseline backend found)",
                   dry_run_validated=False, expected_impact="no action")

    peer = next(d for d in args_by if d != target and not _fault_on(args_by[d]))
    target_seed = next((a for a in args_by[target] if a.startswith("--seed")), None)
    corrected_args = [a for a in args_by[peer] if not a.startswith("--seed")]
    if target_seed:
        corrected_args.append(target_seed)

    dep = _sanitize(deploys[target])
    dep["spec"]["template"]["spec"]["containers"][0]["args"] = corrected_args
    manifest = yaml.safe_dump(dep, sort_keys=False)
    before_args = "\n".join(args_by[target]) + "\n"
    after_args = "\n".join(corrected_args) + "\n"
    ok, _ = apply_dry_run_server(manifest)
    removed = [a for a in args_by[target] if a not in corrected_args]
    impact = (
        f"removes the injected fault args from {target} ({', '.join(removed) or 'none'}) and restores it "
        "to the pool baseline. A rolling restart replaces the degraded pod; its success rate and TTFT "
        f"return to peer levels, clearing the partial errors/slowdown within ~3 minutes."
    )
    return Fix(
        type=FixType.MANIFEST_DIFF,
        diff=_unified_diff(before_args, after_args, f"deploy/{target} args"),
        command=f"{_APPLY} <<'EOF'\n{manifest}EOF",
        dry_run_validated=ok,
        expected_impact=impact,
    )


def _no_fault_fix() -> Fix:
    return Fix(
        type=FixType.MANIFEST_DIFF,
        diff="(no change required — scheduler config, endpoints, and metrics are within healthy baselines)",
        dry_run_validated=False,
        expected_impact="no action: the pool is serving within SLO",
    )


_FIX_BY_CATEGORY = {
    FaultCategory.SCORER_WEIGHT_MISCONFIG: fix_scorer_weights,
    FaultCategory.PREFIX_CACHE_DISABLED: fix_prefix_cache_disabled,
    FaultCategory.UNHEALTHY_ENDPOINT: fix_unhealthy_endpoint,
}


def generate_fix(category: FaultCategory) -> Fix:
    return _FIX_BY_CATEGORY.get(category, _no_fault_fix)()
