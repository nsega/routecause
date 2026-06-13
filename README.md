# RouteCause

**Autonomous inference-infrastructure diagnostics agent for llm-d Router (EPP) gateways on Kubernetes.**

You get paged on an SLO breach for a shared LLM inference gateway. The cause is
rarely capacity — it's routing/scheduling: a mis-weighted EPP scorer, an
unhealthy endpoint left in rotation, prefix-cache-aware routing silently
disabled by a rollout. Diagnosing it today means hand-correlating gateway
metrics, scheduler plugin config, endpoint state, and recent manifest diffs.

RouteCause does that correlation autonomously and hands back a **root-cause
report**: root cause, supporting evidence (specific metrics + config lines), and
a concrete fix (a `kubectl patch` / manifest diff validated by
`kubectl apply --dry-run=server`) with expected impact.

> The product is the **agent and its report**, not a dashboard. The live view is
> a single RCA report.

## How it works

`POST /diagnose {pool}` runs a quarantined multi-agent inner loop:

1. **Evidence collection** — three isolated-context agents read *disjoint*
   sources so no single context owns all the evidence (avoids self-preferential
   bias): **E1** Prometheus metrics, **E2** the `epp-config` scheduler config,
   **E3** Kubernetes cluster state (endpoint health, pool membership, arg diffs).
2. **Hypothesis merge + ranking.**
3. **Verification** — one independent refuter per surviving hypothesis, in a
   fresh context, mandated to *falsify* it.
4. **Report + fix** — a fix validated with `kubectl apply --dry-run=server`
   before it is allowed into the report.
5. *(stretch)* **Actuator** — a separate, mutate-capable agent applies the fix
   and watches the same metrics for recovery.

## Quickstart

Requires the [`inference-lab`](../inference-lab) environment booted alongside
this repo (`make up && make load`) and `ANTHROPIC_API_KEY` in `.env`.

```sh
make demo          # boots a diagnosis against an injected S1 fault end-to-end
make test          # pytest: schema contract + a mocked diagnosis path
```

> **Caveat:** fault symptoms take ~3 minutes to fully develop after injection;
> don't diagnose immediately after `make inject`.

## Report schema

Versioned and documented in [`routecause/schema.py`](routecause/schema.py)
(`schema_version` `1.0`). The schema enforces the report invariants: a
`fault_category` enum, ≥2 evidence citations from ≥2 distinct sources, and a fix
that carries an actionable artifact.

## Version pins

The EPP ecosystem is mid-reorg (EPP consolidated into llm-d, "Inference
Scheduler" → **llm-d Router**, InferencePool API promoted to v1). RouteCause
pins to the lab's vendored versions — **scheduler v0.8.0 / GIE v1.5.0** — and
validates every fix only against the **live cluster**, never upstream docs.

## Provenance

See [`PROVENANCE.md`](PROVENANCE.md). All agent code here was written in-window
on Build Day; `inference-lab` is a pre-event environment, referenced not copied.
