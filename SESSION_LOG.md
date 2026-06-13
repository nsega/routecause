# SESSION_LOG.md — RouteCause in-window build session (2026-06-13)

Judged artifact (RUBRIC C2). Chronological record of the autonomous build: what
was done, the fresh-context verifier verdicts, the self-correction transitions,
and the ground-truth-access audit. Complements `NOTES.md` (rules/decisions) and
the granular `git log`.

> This file is the curated, auditable summary. The **full session transcript**
> is exported to [`session-log.md`](session-log.md) (secret-scanned clean); the
> per-verifier subagent transcripts (verbatim verdicts) are under the Claude Code
> session's `subagents/` directory and can be attached alongside.

## Ground-truth access audit (RUBRIC A)

- The builder (diagnosis agents, orchestrator, collectors, fixes, service)
  **never** reads `lab/state/fault.json`. The literal path appears **nowhere** in
  `routecause/` — confirmed by every verifier via `grep -rn "fault.json" routecause/`
  → 0 matches. It appears only in the briefing docs that describe the prohibition.
- Ground truth is read **only** by fresh-context verifier subagents (a separate
  role/context), and by `workflow/grade_scenario.py` **at runtime via a flag/env**
  (`--expected` / `--ground-truth-env`), never as a committed literal.
- `diagnose()` receives the **pool name only** — never which fault was injected.

## Timeline

1. **Kickoff** — read BRIEF/RUBRIC/DESIGN-DRAFT; read-only recon of the lab
   (Makefile, NOTES, healthy manifests, PromQL) — never the `faults/` spoilers.
   Boundary commit `kickoff: begin in-window build`.
2. **Schema v1.0** — Pydantic report contract enforcing the rubric invariants
   (fault_category enum, ≥2 evidence sources, actionable fix). 9 tests.
3. **Deterministic collectors E1/E2/E3** — Prometheus / epp-config invariants /
   cluster-state arg-diff. Validated live: healthy→0 signals, S1→cross-source.
4. **Fixes + orchestrator** — aggregate signals → category, cross-source
   evidence, dry-run-validated fix.
5. **LLM layer** — isolated hypothesis subagents + independent refuters
   (Anthropic API, parallel), graceful fallback to the deterministic core.
6. **Service** — FastAPI `POST /diagnose` + single-report HTML; CLI; tunnel.
7. **Verifier harness** — deterministic grader + rerunnable `workflow/`.
8. **S1 → S2 → S3**, each graded by a fresh-context verifier; A7 regression.
9. **B/C** — public URL, `make demo`, `make test`, push public, provenance.
10. **A6 stretch** — quarantined actuator: apply + recovery watch.

## Fresh-context verifier verdicts (builder never self-graded)

- **S1** — PASS A1–A4. A5 flagged (only confirmed hypothesis listed). → see SC#1.
- **S2 (first)** — **REJECTED on A3**: two k8s-api citations falsely claimed
  healthy backend-0/backend-1 were "patched off-baseline." → see SC#2.
- **S2 (re-grade after fix)** — PASS A1–A4; only backend-2 cited; no false evidence.
- **S3** — PASS A1–A4 (independently re-verified config + metrics + fix dry-run).
- **Final B+C** — B1–B4 PASS, C1/C3/C4/C5 PASS; C2 pending raw-transcript export.

## Self-corrections (tagged `[SELF-CORRECTION]` in NOTES.md and commits)

- **SC#1 (A5)** — verifier flagged missing rejected-hypothesis enumeration →
  orchestrator now enumerates ruled-out categories with reasons → A5 satisfied.
- **SC#2 (A3, MUST)** — verifier **rejected** S2 (false off-baseline citations) +
  a fix-gen crash, both from an intersection-baseline bug → switched to a
  **majority** baseline + robust target selection; also hardened the grader to
  validate k8s-api citations (the verifier caught the grader's blind spot) →
  fresh-context **re-grade passes**. This is the clean reject→fix→pass moment.

## Result

All MUSTs for S1/S2/S3 (A1–A4) + A7 regression pass under fresh-context
verification; B1–B4 and C1/C3/C4/C5 pass; A6 stretch demonstrated (P95 e2e
18.96s→15.28s under the 16s SLO, queue 88→0, `slo_met=true`). C2 raw transcript
is exported at submission.
