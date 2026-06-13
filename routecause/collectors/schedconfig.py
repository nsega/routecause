"""E2 — SchedConfig Agent (EPP config only).

Reads the live ``epp-config`` ConfigMap and checks invariants (NOT a diff against
a copied golden file — keeps C1 clean and survives version drift). Tolerates
unknown plugin types (the upstream plugin ecosystem grows monthly).

Invariants (DESIGN-DRAFT §2):
- I1: every scorer weight in a scheduling profile is > 0 (loader accepts 0 and
  negatives → silent mis-routing). Healthy: queue=2, kv=2, prefix=3.
- I2: prefix-cache-scorer exists in BOTH plugin defs AND a scheduling profile.
- I3: the dataLayer section is present (REQUIRED in GIE v1.5.0).
"""

from __future__ import annotations

import yaml

from ..config import EPP_CONFIG_KEY, EPP_CONFIGMAP, HEALTHY_SCORER_WEIGHTS
from ..kube import KubectlError, get_json
from ..schema import Evidence, EvidenceSource, FaultCategory
from .base import CollectorResult, Signal

_CM_LOC = f"{EPP_CONFIGMAP}:{EPP_CONFIG_KEY}"


def collect_schedconfig(pool: str) -> CollectorResult:
    res = CollectorResult(collector="E2", source=EvidenceSource.CONFIGMAP)
    try:
        cm = get_json("configmap", EPP_CONFIGMAP)
        raw = cm["data"][EPP_CONFIG_KEY]
        cfg = yaml.safe_load(raw)
    except (KubectlError, KeyError, yaml.YAMLError) as e:
        res.errors.append(f"could not read/parse {_CM_LOC}: {e}")
        res.summary = "E2 could not read the EPP config"
        return res

    plugins = cfg.get("plugins", []) or []
    plugin_types = {p.get("type") for p in plugins if isinstance(p, dict)}
    profiles = cfg.get("schedulingProfiles", []) or []

    # --- I1: scorer weights > 0 -------------------------------------------------
    for prof in profiles:
        pname = prof.get("name", "?")
        for pl in prof.get("plugins", []) or []:
            ref = pl.get("pluginRef", "")
            weight = pl.get("weight")
            if weight is None or "scorer" not in ref:
                continue
            loc = f"{_CM_LOC} schedulingProfiles[{pname}].plugins[{ref}].weight"
            ev = Evidence(
                source=EvidenceSource.CONFIGMAP, locator=loc, observed=str(weight),
                baseline=str(HEALTHY_SCORER_WEIGHTS.get(ref, "> 0")),
                claim=f"scheduling weight of {ref}",
            )
            res.observations.append(ev)
            if weight <= 0:
                res.signals.append(
                    Signal(
                        category=FaultCategory.SCORER_WEIGHT_MISCONFIG,
                        strength=0.95,
                        rationale=(
                            f"scorer '{ref}' has weight={weight} (<= 0) in profile '{pname}'. "
                            f"The v1.5.0 loader accepts non-positive weights without error, so this "
                            f"silently distorts endpoint scoring (healthy {ref} weight is "
                            f"{HEALTHY_SCORER_WEIGHTS.get(ref, '> 0')})."
                        ),
                        evidence=[ev],
                    )
                )

    # --- I2: prefix-cache-scorer present in plugins AND a profile ---------------
    in_plugins = "prefix-cache-scorer" in plugin_types
    in_profile = any(
        pl.get("pluginRef") == "prefix-cache-scorer"
        for prof in profiles
        for pl in (prof.get("plugins", []) or [])
    )
    ev_prefix = Evidence(
        source=EvidenceSource.CONFIGMAP,
        locator=f"{_CM_LOC} (prefix-cache-scorer presence)",
        observed=f"in plugins={in_plugins}, in schedulingProfile={in_profile}",
        baseline="present in both plugins and the scheduling profile",
        claim="whether prefix-aware routing is active",
    )
    res.observations.append(ev_prefix)
    if not in_profile:
        res.signals.append(
            Signal(
                category=FaultCategory.PREFIX_CACHE_DISABLED,
                strength=0.95,
                rationale=(
                    "prefix-cache-scorer is absent from the scheduling profile, so the router no "
                    "longer biases requests toward the endpoint already holding a request's prefix — "
                    "prefix-aware routing is effectively disabled."
                ),
                evidence=[ev_prefix],
            )
        )
    elif not in_plugins:
        res.signals.append(
            Signal(
                category=FaultCategory.PREFIX_CACHE_DISABLED,
                strength=0.6,
                rationale="prefix-cache-scorer is referenced in a profile but not defined in plugins.",
                evidence=[ev_prefix],
            )
        )

    # --- I3: dataLayer present --------------------------------------------------
    data_layer = cfg.get("dataLayer")
    has_data_layer = bool(data_layer and data_layer.get("sources"))
    ev_dl = Evidence(
        source=EvidenceSource.CONFIGMAP, locator=f"{_CM_LOC} dataLayer",
        observed="present" if has_data_layer else "missing/empty",
        baseline="present (REQUIRED in GIE v1.5.0)",
        claim="whether scorers receive metrics to score on",
    )
    res.observations.append(ev_dl)
    if not has_data_layer:
        res.signals.append(
            Signal(
                category=FaultCategory.OTHER,
                strength=0.8,
                rationale=(
                    "the dataLayer section is missing; in GIE v1.5.0 this disables metrics collection "
                    "so the queue/kv-cache scorers silently score every endpoint 0."
                ),
                evidence=[ev_dl],
            )
        )

    unknown = plugin_types - {
        "queue-scorer", "kv-cache-utilization-scorer", "prefix-cache-scorer",
        "max-score-picker", "single-profile-handler", "metrics-data-source",
        "core-metrics-extractor",
    }
    res.summary = (
        f"scorer_weights_ok={not any(s.category == FaultCategory.SCORER_WEIGHT_MISCONFIG for s in res.signals)}; "
        f"prefix_scorer_active={in_profile and in_plugins}; dataLayer={has_data_layer}"
        + (f"; tolerated_unknown_plugins={sorted(unknown)}" if unknown else "")
    )
    return res
