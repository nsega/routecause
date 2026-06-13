# Build Brief — RouteCause: Inference-Infrastructure Diagnostics Agent

**Event:** Claude Build Day — June 13, 2026 · Shack15, Ferry Building, San Francisco
**Builder:** The autonomous coding agent, run via Claude Code. The human acts as director only.
**Repo (built today):** `nsega/routecause` (public, created at kickoff; all agent code is written inside the official build window).
**Environment (brought in, pre-event):** `nsega/inference-lab` (public, committed before the event) — kind cluster + llm-d Router (EPP) + InferencePool + simulated model backends + fault-injection scripts + Prometheus metrics. A pre-existing project brought in as scaffolding: it is referenced, not re-created or copied wholesale, today.

> Revised 2026-06-12 to track the updated **Claude Build Day** participant guide: model-agnostic framing, explicit original-work attribution (disqualification-level), avoidance of the prohibited "dashboard-as-main-feature" category, and the 10:30 AM→5:00 PM build window.

-----

## 1. Problem

LLM serving on Kubernetes degrades for reasons that are rarely about capacity. The usual culprits are routing and scheduling misconfigurations — a mis-weighted EPP scorer, an unhealthy endpoint left in rotation, prefix-cache-aware routing silently disabled by a rollout. These failures are hard to eyeball: diagnosing them requires cross-referencing gateway metrics, scheduler plugin config, endpoint state, and recent manifest diffs. The ecosystem is also mid-reorganization (EPP moved from kubernetes-sigs/gateway-api-inference-extension to llm-d), so documentation and operator intuition lag the code.

## 2. Target user

Platform/infra engineers operating shared LLM inference gateways on Kubernetes. They get paged on an SLO breach and today must manually correlate four sources of truth. RouteCause does that correlation for them and hands back a root-cause report with a fix.

## 3. What you are building today

An autonomous diagnostics agent, exposed as a small service:

1. **Trigger:** `POST /diagnose` (or CLI `routecause diagnose`) with the affected InferencePool name. An SLO-breach watcher may call this automatically (stretch).
1. **Investigate:** Pull gateway/EPP metrics from Prometheus, read the EPP scheduler plugin config (YAML / ConfigMap), check endpoint health and pool membership, and diff recent manifest changes.
1. **Hypothesize & verify:** Form ranked hypotheses. Verify each against evidence using parallel verifier-style subagents; falsify weak ones fast.
1. **Report:** Produce a root-cause analysis (RCA) report — root cause, supporting evidence (specific metrics + config lines), and a concrete fix as a manifest diff or `kubectl patch`, with expected impact. The report JSON must include a `fault_category` field with exactly one of: `scorer-weight-misconfig`, `unhealthy-endpoint-in-rotation`, `prefix-cache-routing-disabled`, or `other` — this is what the verifier matches against ground truth.
1. **Stretch — close the loop:** Apply the fix, watch the same metrics, and confirm recovery against the SLO.

The product is the agent and its reports. A minimal HTML view of the latest report at the live URL is acceptable; a dashboard is explicitly NOT the product. The rendering must present a **single RCA report** (root cause, evidence, fix) — never a multi-panel metrics dashboard. A dashboard-as-main-feature is a disqualifiable project category under the event rules.

**Architecture guidance (inner loop):** Generate hypotheses from disjoint evidence sources — separate subagents for metrics, scheduler config, and manifest diffs — so no single context window owns all the evidence (this avoids self-preferential bias). Each surviving hypothesis must face an independent verifier/refuter before it may appear in the report. In apply mode (stretch), use a quarantine split: agents that read cluster state may not mutate it; only a separate actuator agent applies the approved patch.

## 4. Definition of done

You are done when, and only when, the verifier subagent grades the work against `RUBRIC.md` and all MUST items pass for **all three fault scenarios** (S1 scorer-weight misconfig, S2 unhealthy endpoint in rotation, S3 prefix-cache routing disabled), plus the functional MUSTs. Do not stop, and do not ask the human whether it is good enough — ask the verifier.

## 5. How to work (orchestration contract)

- Read this brief and `RUBRIC.md` fully. Ask all clarifying questions at kickoff, then run autonomously.
- Set the target with `/goal`: “every MUST item in RUBRIC.md passes for S1–S3 and sections B–C, as graded by a fresh-context verifier subagent.” Hillclimb on that goal; collect feedback from the environment, not from the human.
- Orchestrate with a dynamic workflow, specified step-by-step (not a one-word trigger): builder implements one scenario → fan out hypothesis-verification where useful → an independent verifier subagent grades the scenario against `RUBRIC.md` → on failure, builder fixes and re-submits (blocker rule: 3 failed attempts → NOTES.md + human escalation) → regression pass on previously passed scenarios → loop until the goal is met. Save the working workflow file into this repo: it is a judged artifact and must be rerunnable by another team.
- The verifier always runs in a fresh context. The builder may never grade its own work.
- **Make the self-correction legible.** Deliberately preserve, in the session log, at least one clean *verifier rejects a report → builder fixes → re-grade passes* transition, tagged so it is trivial to locate. The Round 2 stage demo asks specifically for the moment the agent caught and fixed its own failure; this is the moment to surface.
- Keep persistent memory in `NOTES.md` through the full progression: fail → investigate → verify → distill → consult. Turn mistakes into general rules; consult the rules instead of re-deriving them.
- Maintain the session log; it is a judged artifact. If something breaks, catch it yourself via tests or the verifier — do not wait for the human.
- Commit early and often with clear messages distinguishing today’s work.

## 6. Environment reference (pre-built, do not rebuild)

- `make up` — boots kind + llm-d Router (standalone mode: EPP + Envoy sidecar) + simulated backends
- `make inject FAULT=s1|s2|s3` — injects a fault scenario; ground-truth label written to `lab/state/fault.json` (the agent must NOT read this file; the verifier uses it for grading)
- `make reset` — restores healthy state
- Prometheus at `http://localhost:30090`; EPP config in ConfigMap `epp-config`; backends expose queue depth, KV-cache utilization, prefix-cache hit rate
- `lab/state/fault.json` is ground truth for grading only. The builder agent must never read it (it is chmod 600 by the inject script); the verifier confirms non-access from the session log

## 7. Constraints

- Use only this repo, the pre-built `inference-lab` repo, and public open-source code. No company code, data, or credentials.
- **Original-work boundary (disqualification risk).** Everything demoed must be code this build produced today in `nsega/routecause`. `inference-lab` is a pre-existing project brought in as the environment — reference it, never copy it wholesale into `routecause`. Maintain a **PROVENANCE** statement (a README section or `PROVENANCE.md`) that says plainly what was built today (this repo) vs. brought in pre-event (`inference-lab`), and keep commit history granular so the boundary is self-evident. Under the event rules, failing to clearly distinguish today’s contributions is grounds for **immediate disqualification**.
- No Streamlit. No dashboard-first UX. The live HTML is a single RCA **report**, not a dashboard. Reports are the artifact.
- **Build window.** The hackathon runs 10:30 AM–5:00 PM; submissions are due 5:00 PM sharp. Use the 9:00–10:30 AM doors-open window for environment boot, GATE validation (arm64 images first), and briefing the agent — *not* for writing `routecause` code. Internal targets: live URL by 4:30 PM, a ~1-minute demo video (showing only what was built today) recorded by 4:45 PM, submit by 5:00 PM.
