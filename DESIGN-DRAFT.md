# RouteCause — Agent Design Draft (Pre-Build-Day)
 
> Working draft to carry into `nsega/routecause` on Build Day.
> Intended as the first document the builder agent reads. RUBRIC check IDs are referenced inline.
 
---
 
## 1. Architecture overview
 
```
POST /diagnose {pool}            (CLI: routecause diagnose <pool>)
        │
        ▼
┌─ Orchestrator ────────────────────────────────────────────┐
│                                                           │
│  Phase 1: Evidence collection (parallel, isolated ctx)    │
│   ├─ [E1] Metrics Agent      … Prometheus only            │
│   ├─ [E2] SchedConfig Agent  … ConfigMap epp-config only  │
│   └─ [E3] ClusterState Agent … K8s API (endpoint health,  │
│            pool membership, recent manifest diffs)        │
│        ※ Each agent forms hypotheses independently from   │
│          its own source only (avoids self-preferential    │
│          bias of a single context owning all evidence)    │
│                                                           │
│  Phase 2: Hypothesis merge + ranking (Orchestrator)       │
│                                                           │
│  Phase 3: Verification (one independent Verifier/Refuter  │
│           per hypothesis, in parallel)                    │
│   └─ Fresh context, mandate: "falsify this hypothesis"    │
│      → confirmed / rejected + reason            (A5)      │
│                                                           │
│  Phase 4: Report generation + fix dry-run       (A4)      │
│   └─ Run kubectl apply --dry-run=server; only patches     │
│      that pass make it into the report                    │
│                                                           │
│  [stretch] Phase 5: Actuator Agent              (A6)      │
│   └─ Quarantine split: E1–E3 and verifiers are            │
│      read-only. Only the Actuator may mutate the          │
│      cluster. Apply → watch same metrics for 5 min →      │
│      report recovery with before/after numbers            │
└───────────────────────────────────────────────────────────┘
```
 
**Hard rules**
- The builder/diagnosis agents NEVER read `lab/state/fault.json` (it is chmod 600, but additionally the path must not appear anywhere in the codebase; the session log is audited for access)
- Diagnosis-side agents get read-only tools only (`kubectl get` / Prometheus queries). Only the Actuator holds patch/apply
- **The live cluster is the only source of truth at diagnosis time.** No web lookups, no reliance on upstream docs: the ecosystem is mid-reorg (EPP code consolidated into llm-d, "Inference Scheduler" renamed to "llm-d Router", InferencePool API promoted to v1) and docs lag the lab's pinned versions (scheduler v0.8.0 / GIE v1.5.0)
- Whole run ≤ 10 minutes (A1). Symptoms are fully developed ~3 min after injection, so 2-minute-window PromQL can be queried immediately
## 2. Subagent I/O contracts
 
### E1: Metrics Agent (Prometheus only)
Input: pool name, Prometheus URL (:30090)
Queries to run (from `check-metrics.sh`, verified against the lab):
 
| Signal | PromQL |
|---|---|
| Queue depth (per backend) | `sum by (backend) (vllm:num_requests_waiting)` |
| KV-cache utilization | `sum by (backend) (vllm:kv_cache_usage_perc)` |
| Prefix-cache hit rate | `sum(rate(vllm:prefix_cache_hits[2m])) / sum(rate(vllm:prefix_cache_queries[2m]))` |
| P95 e2e latency | `histogram_quantile(0.95, sum by (le) (rate(vllm:e2e_request_latency_seconds_bucket[2m])))` |
| P95 TTFT | `histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[2m])))` |
| Per-backend success rps | `sum by (backend) (rate(vllm:request_success_total[2m]))` |
 
Output: observed values + deviation from healthy baseline + metrics-only hypothesis list
 
**Discrimination table (signatures measured in the lab)** — embed in E1's prompt:
 
| Observation | Suggests |
|---|---|
| Queue piles on ONE backend, others at 0 rps, hit rate unchanged | scorer-weight-misconfig |
| One backend has lowest success rps + errors, queues ~0, hit rate unchanged | unhealthy-endpoint-in-rotation |
| Hit rate collapses (~0.72 → ~0.48), KV churn on all backends, queues ~0, no error spike | prefix-cache-routing-disabled |
 
### E2: SchedConfig Agent (EPP config only)
Input: `config.yaml` from ConfigMap `epp-config` (ns: inference-lab)
Checks — expressed as **invariants**, not a diff against a copied golden file (keeps C1 clean — lab code is referenced, not copied — and survives version drift):
- I1: every scorer weight in `schedulingProfiles[].plugins[]` is **> 0** (the loader accepts 0 and negative values; healthy lab values: queue=2, kv=2, prefix=3)
- I2: `prefix-cache-scorer` exists in BOTH the plugin definitions and the scheduling profile
- I3: the `dataLayer` section is present (REQUIRED in GIE v1.5.0; without it every scorer silently scores 0 → an important "other"-class hypothesis)
- Unknown plugin types must be tolerated, never crash (the upstream plugin ecosystem grows monthly); unexplained anomalies route to `other`
Output: violated invariants + config-only hypothesis list
### E3: ClusterState Agent (K8s API only)
Input: pool name
Checks:
- InferencePool selector and membership (which pods are actually selected)
- Per-backend Deployment/Pod readiness, restart counts, and **args diffs** (S2 leaves traces in backend-2's args: `--failure-injection-rate=25` etc.)
- Recent resource changes (`metadata.generation` / managedFields timestamps, rollout history)
Output: list of "looks healthy but recently changed" items + hypotheses
### Verifier/Refuter (one per hypothesis, fresh context)
Input: a single hypothesis + read-only tools to test it
Mandate: "Try to **destroy** this hypothesis. If you cannot, produce ≥ 2 concrete supporting pieces of evidence (metric name + value, config line)" (A3)
Output: `confirmed | rejected` + evidence or refutation reason
 
## 3. Report JSON schema v1.0 (B3/B4)
 
```json
{
  "schema_version": "1.0",
  "report_id": "uuid",
  "pool": "string",
  "started_at": "ISO8601",
  "completed_at": "ISO8601",
  "fault_category": "scorer-weight-misconfig | unhealthy-endpoint-in-rotation | prefix-cache-routing-disabled | other",
  "root_cause": {
    "summary": "1–2 sentence conclusion",
    "details": "Mechanism: why this config produces this symptom"
  },
  "evidence": [
    {
      "source": "prometheus | configmap | k8s-api | manifest-diff",
      "locator": "PromQL query, or ConfigMap name + YAML path, or kubectl command",
      "observed": "actual value / line",
      "baseline": "healthy expectation (when known)",
      "claim": "what this evidence supports"
    }
  ],
  "hypotheses": [
    {
      "hypothesis": "string",
      "origin": "E1 | E2 | E3",
      "status": "confirmed | rejected",
      "reason": "why rejected, or summary of confirming evidence"
    }
  ],
  "fix": {
    "type": "kubectl-patch | manifest-diff",
    "command": "command to run (patch case)",
    "diff": "unified diff (manifest case)",
    "dry_run_validated": true,
    "expected_impact": "which metrics should recover and rough timeframe"
  },
  "recovery": {
    "applied": false,
    "before": {},
    "after": {},
    "slo_met": null
  }
}
```
 
- `evidence` requires ≥ 2 items from distinct sources (A3). Every `locator` must be re-executable by a third party
- `fix.dry_run_validated` is set to true only after actually running `kubectl apply --dry-run=server` before report emission (A4)
- `recovery` is for the stretch goal (A6); stays `applied: false` when unused
## 4. Tech choices (agent side)
 
| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Speed of iteration on the day; one-liners for both K8s and Prometheus |
| HTTP | FastAPI + uvicorn | Just two routes: `POST /diagnose`, `GET /reports/latest` (HTML) |
| K8s access | `kubectl` subprocess | Direct use of dry-run=server / patch / rollout; fewer surprises than a client library |
| Prometheus | httpx against the HTTP API | Exactly six fixed queries |
| LLM | Anthropic API (claude-sonnet-4-6 for subagents; a higher-tier model for the orchestrator if needed) | Cost/speed balance |
| HTML | Jinja2, single template | Dashboards are forbidden; just a readable rendering of the latest report (B1) |
| Exposure | cloudflared quick tunnel | Already rehearsed; up in ~10s |
| Tests | pytest: schema validation + one diagnosis path with mocked Prometheus/kubectl (B3) | |
 
## 5. Build-day implementation order (critical path)
 
1. Report schema + Pydantic models + pytest skeleton (lock B3 in first)
2. Implement E1–E3 as **deterministic collectors** first (no LLM; they return evidence JSON) → raw symptom data is available early
3. Layer the LLM hypothesis/verification logic on top → end-to-end on S1
4. Have the verifier subagent (fresh context, grading against RUBRIC) grade S1
5. S2 → S3, with a regression pass after each (A7)
6. FastAPI + tunnel + README quickstart (`make demo`) (B1/B2)
7. Actuator if time remains (A6)
**Three-strikes rule**: 3 consecutive failures on the same MUST → record the blocker in NOTES.md and escalate to the human (the only sanctioned escalation, per RUBRIC).
 
## 6. Known traps (inherited from inference-lab NOTES.md)
 
- GIE v1.5.0 requires the `dataLayer` section; the llm-d sample configs omit it — never copy them blindly
- The config loader ACCEPTS `weight: 0` and negative weights (no error → config validation alone cannot catch S1; compare actual values)
- Symptoms take ~3 min post-injection to develop. 2-minute windows are mature by diagnosis time, but document the "don't diagnose immediately after `make inject`" caveat in the README
- The literal path `lab/state/fault.json` must not appear anywhere in the codebase (so even a grep audit comes up clean)
- **Ecosystem drift (as of June 2026):** EPP code has been consolidated from kubernetes-sigs/gateway-api-inference-extension into llm-d (the GIE repo keeps only the InferencePool API + EPP protocol; old packages are headed for archive), "Inference Scheduler" was renamed **"llm-d Router"**, and the InferencePool API was promoted to v1. Consequences: (a) LLM subagents may "know" newer scorer names or config schemas from training data — pin their prompts to the lab's vendored versions (scheduler v0.8.0 / GIE v1.5.0) and validate fixes ONLY via `kubectl apply --dry-run=server` against the live cluster; (b) use "llm-d Router (EPP)" in human-readable report text; (c) record the version pins and this rationale in the README
