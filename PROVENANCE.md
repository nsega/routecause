# PROVENANCE

The Claude Build Day rules require a clear, file-checkable boundary between work
authored in-window today and material brought in pre-event. Failing to
distinguish today's contributions is a disqualification-level issue, so this
statement is kept consistent with the granular commit history (see `git log`).

## Brought in (pre-event) — referenced, never copied wholesale

- **`nsega/inference-lab`** (sibling repo) — the diagnostic *environment*: kind
  cluster + llm-d Router (EPP) + InferencePool + simulated vLLM backends +
  fault-injection scripts + Prometheus. RouteCause *references* it (drives
  `make up/load/inject/reset`, reads its live cluster) but copies none of its
  code. Healthy baselines used in `routecause/config.py` are documented
  expectations, not copied files.
- **Briefing inputs in this repo** — `BRIEF.md`, `RUBRIC.md`, `DESIGN-DRAFT.md`:
  authored before Build Day to direct the agent. Committed pre-kickoff in
  `docs: briefing inputs ...` (before the kickoff boundary commit).

## Built today (June 13, in-window) — all of it

The first in-window commit is **`kickoff: begin in-window build`**; everything
after it is event-day work. All RouteCause agent code was written today:

- `routecause/schema.py` — versioned RCA report schema v1.0 (the product contract)
- `routecause/config.py` — pinned lab interface + version pins + baselines
- `routecause/prom.py`, `routecause/kube.py` — read-only Prometheus + kubectl access
- `routecause/collectors/` — deterministic E1 (metrics) / E2 (config) / E3 (cluster) collectors
- `routecause/llm.py` — isolated hypothesis subagents + independent refuters (Anthropic API)
- `routecause/orchestrator.py` — evidence merge/rank, reconcile, report assembly
- `routecause/fixes.py` — dry-run-validated fix generation
- `routecause/service.py`, `routecause/cli.py`, `routecause/store.py`, `routecause/templates/` — service, CLI, single-report HTML
- `workflow/` — deterministic grader + rerunnable scenario workflow (judged artifact)
- `tests/` — schema + collector + mocked-diagnosis tests
- `Makefile`, `NOTES.md`, this `PROVENANCE.md` section

The commit history is intentionally granular (no squash) so the boundary is
self-evident.
