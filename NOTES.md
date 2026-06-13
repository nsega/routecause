# NOTES.md — RouteCause build log (fail → investigate → verify → distill → consult)

Persistent memory across the build. Mistakes become general rules; consult the
rules instead of re-deriving them. Self-correction transitions are tagged
`[SELF-CORRECTION]` so they are trivial to locate (the Round-2 demo asks for the
moment the agent caught and fixed its own failure).

## Architecture decisions

- **Deterministic collectors first, LLM on top.** E1/E2/E3 are LLM-free and emit
  evidence JSON + signals; the LLM hypothesis/refuter layer enriches but never
  gates correctness. The orchestrator's `fault_category` stays the validated
  deterministic choice unless a refuter *rejects* it AND confirms a
  deterministically-supported alternative. Rule: **trust evidence-grounded
  deterministic signals for the enum (A2); use the LLM for agentic
  hypothesis/refutation and narration.** This makes A1 (no crash) and A2
  (correct category) robust even if the API hiccups.
- **Cross-source A3 by construction.** S1 = E2 (weight≤0) + E1 (queue
  concentration); S2 = E3 (failure-injection arg) + E1 (low rps/high TTFT);
  S3 = E2 (prefix-scorer absent) + E1 (hit-rate collapse). Each fault is
  corroborated by two disjoint sources, so the report always cites ≥2 sources.
- **Quarantine in code.** `kube.py` exposes only read verbs + a non-mutating
  server-side dry-run; mutation lives in the Actuator (stretch). The diagnosis
  path can't mutate the cluster.

## Traps (inherited from inference-lab + confirmed live)

- **S1 is the "invert" variant.** It sets `queue-scorer weight: -2` AND
  `kv-cache-utilization-scorer weight: 0` (confirmed live). Rule: the fix must
  restore **every** scorer weight to its healthy positive value, not just one.
- **`weight: 0`/negative is accepted by the v1.5.0 loader** → config validation
  alone can't catch S1; compare actual values against healthy (queue=2, kv=2,
  prefix=3).
- **dataLayer is REQUIRED in GIE v1.5.0**; without it queue/kv scorers silently
  score 0. Treated as an "other"-class invariant (I3).
- **Symptoms mature ~2.5–3 min after inject.** The 2m success-rps window lags
  the inject, so E1's S1 *idle-backend* signal appears late — but the queue
  pile-up shows within ~1 min, so E1 fires S1 on **queue concentration**, not on
  idle rps. Rule: don't gate detection on a slow-moving window when a
  fast-moving one carries the same signal.
- **No `vllm:request_errors_total` series exists.** S2's error signal is **low
  `request_success_total` rps + elevated per-backend TTFT** on the degraded
  backend, corroborated by E3's arg diff (`--failure-injection-rate`).
- **Pin every kubectl to `--context kind-inference-lab`.** The kubeconfig also
  has company GKE contexts; the read-only helper hard-pins the lab context.

## Self-correction log

### [SELF-CORRECTION] #1 — A5 rejected-hypothesis enumeration (S1)
- **Fail:** the fresh-context verifier passed A1–A4 for S1 but flagged A5
  (SHOULD): the report listed only the *confirmed* hypothesis; "why the rejected
  ones were rejected" was not demonstrated.
- **Fix:** orchestrator now always enumerates the other real fault categories as
  explicitly `rejected` with evidence-grounded reasons (`_alt_hypotheses` /
  `_ruled_out_reason`), e.g. "S2 ruled out: no backend carries fault-injection
  args and per-backend success rps is balanced".
- **Re-grade:** S1 report now lists 3 hypotheses (1 confirmed + 2 rejected with
  reasons); A5 fully satisfied.
- **Rule distilled:** a diagnosis is not done when it names the cause — it must
  also show the alternatives it falsified. Enumerate rejections by default.

## Status

- S1: A1–A4 PASS (fresh-context verifier), A5 PASS after [SELF-CORRECTION] #1.
- S2, S3: pending.
- Tests: schema + collectors + mocked orchestrator path, all green (`make test`).
