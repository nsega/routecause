# RUBRIC.md — RouteCause completion rubric

Graded by a fresh-context verifier subagent. The builder may not self-grade and may not stop until every MUST passes. Each item is pass/fail with the check stated explicitly so the verifier needs no human judgment.

## A. Per-scenario checks (run for S1, S2, S3 independently)

For each scenario: `make reset && make inject FAULT=<s>` then trigger the agent. The verifier — not the builder — then compares the report against `lab/state/fault.json` (ground truth; off-limits to the builder agent). The verifier also confirms from the session log that the builder never accessed `lab/state/fault.json`; any access fails the scenario.

| ID | Level | Check |
|----|-------|-------|
| A1 | MUST | Agent completes a diagnosis run end-to-end with no human input and no crash, in ≤ 10 minutes |
| A2 | MUST | The report's `fault_category` enum equals the ground-truth `fault` label in `lab/state/fault.json` (s1 ↔ scorer-weight-misconfig, s2 ↔ unhealthy-endpoint-in-rotation, s3 ↔ prefix-cache-routing-disabled) |
| A3 | MUST | Report cites ≥ 2 distinct evidence sources (e.g., a named metric with its value AND the offending config line), and each citation is verifiably true against the live environment |
| A4 | MUST | Report includes a concrete fix: a manifest diff or `kubectl patch` that passes `kubectl apply --dry-run=server` |
| A5 | SHOULD | Report lists the hypotheses considered and why the rejected ones were rejected |
| A6 | SHOULD (stretch) | With apply mode enabled, the fix is applied and P95 latency returns under SLO within 5 minutes; the agent reports recovery with before/after numbers |
| A7 | MUST | Re-running the two previously passed scenarios still passes A1–A4 (no regressions) |

## B. Functional checks

| ID | Level | Check |
|----|-------|-------|
| B1 | MUST | Live URL responds 200; `POST /diagnose` returns the report as JSON, and a human-readable rendering exists. The rendering presents a **single RCA report** (root cause / evidence / fix), not a multi-panel metrics dashboard (dashboard-as-main-feature is a disqualifiable category) |
| B2 | MUST | Fresh clone + README quickstart (`make demo` or equivalent) reaches a successful S1 diagnosis without edits |
| B3 | MUST | Test suite exists and passes (`make test`); covers report schema and at least one mocked diagnosis path |
| B4 | SHOULD | Report schema is versioned and documented in the README |

## C. Submission hygiene

| ID | Level | Check |
|----|-------|-------|
| C1 | MUST | Repo is public; all demoed code lives in this repo; pre-built lab code remains in `inference-lab` and is referenced, not copied wholesale |
| C2 | MUST | Session log preserved and included in the submission |
| C3 | MUST | BRIEF.md, RUBRIC.md, NOTES.md, and any workflow scripts committed to the repo |
| C4 | SHOULD | Commit history clearly shows the day's progression (no squash into one blob) |
| C5 | MUST | A provenance statement (README section or `PROVENANCE.md`) explicitly lists what was built today in this repo vs. brought in pre-event (`inference-lab`), and is consistent with the commit history (C4). This is the event's disqualification-level original-work requirement, made file-checkable |

## Completion rule

DONE = all MUST items pass for S1, S2, S3 and sections B–C, verified by the verifier subagent in a fresh context. SHOULD items are tie-breakers: report their pass rate in the final summary. If a MUST cannot pass after 3 distinct fix attempts, write the blocker to NOTES.md and surface it to the human — that is the only sanctioned human escalation.
