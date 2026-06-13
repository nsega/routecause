"""Static configuration and pinned constants for RouteCause.

Rules encoded here (from BRIEF/DESIGN-DRAFT):
- The live cluster is the only source of truth. No web/upstream-doc lookups.
- Pin to the lab's vendored versions (scheduler v0.8.0 / GIE v1.5.0).
- Healthy baselines below are *documented expectations*, NOT a copied golden file
  (keeps the C1 original-work boundary clean and survives version drift).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Lab interface (every kubectl call is pinned to this context) -------------
KUBE_CONTEXT = "kind-inference-lab"
NAMESPACE = "inference-lab"
PROM_URL_DEFAULT = "http://localhost:30090"
GATEWAY_URL_DEFAULT = "http://localhost:30080"
EPP_CONFIGMAP = "epp-config"
EPP_CONFIG_KEY = "config.yaml"
BACKEND_DEPLOYMENTS = ("backend-0", "backend-1", "backend-2")
ENDPOINT_SELECTOR = "app=vllm-sim"

# --- Pinned vendored versions; report text uses the router display name -------
SCHEDULER_VERSION = "v0.8.0"
GIE_VERSION = "v1.5.0"
ROUTER_DISPLAY_NAME = "llm-d Router (EPP)"

# --- SLO (inference-lab README): P95 e2e < 16 s, error rate < 1% --------------
SLO_P95_E2E_SECONDS = 16.0
SLO_ERROR_RATE = 0.01

# --- Healthy baselines (documented expectations, not a golden-file diff) ------
# Healthy scheduling-profile scorer weights, per the live healthy config.
HEALTHY_SCORER_WEIGHTS = {
    "queue-scorer": 2,
    "kv-cache-utilization-scorer": 2,
    "prefix-cache-scorer": 3,
}
# 2-minute-window healthy metric baselines (under `make load`).
BASELINE = {
    "queue_depth": 0.0,
    "kv_cache_usage": 0.05,
    "prefix_hit_rate": 0.72,
    "p95_e2e_seconds": 8.0,
    "p95_ttft_seconds": 1.9,
    "error_rate": 0.0,
}


class Settings(BaseSettings):
    """Runtime settings; secrets come from ``.env`` (never committed)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    prometheus_url: str = PROM_URL_DEFAULT
    gateway_url: str = GATEWAY_URL_DEFAULT
    kube_context: str = KUBE_CONTEXT
    namespace: str = NAMESPACE

    # Pinned to the latest Claude models (DESIGN-DRAFT §4): a higher tier for the
    # orchestrator, Sonnet for the parallel evidence/refuter subagents.
    orchestrator_model: str = "claude-opus-4-8"
    subagent_model: str = "claude-sonnet-4-6"

    # Whole run budget (A1: <= 10 minutes).
    diagnosis_timeout_seconds: int = 540


def get_settings() -> Settings:
    return Settings()
