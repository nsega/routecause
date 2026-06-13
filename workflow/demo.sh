#!/usr/bin/env bash
# demo.sh — fresh-clone quickstart (RUBRIC B2): inject S1, diagnose, grade.
# Assumes the inference-lab is booted alongside this repo (make up && make load)
# and ANTHROPIC_API_KEY is set in .env.
set -euo pipefail
cd "$(dirname "$0")/.."

LAB="${INFERENCE_LAB:-../inference-lab}"
POOL="${POOL:-vllm-sim-pool}"
WAIT="${WAIT_SECONDS:-180}"

echo ">> healthy baseline + load (idempotent)"
make -C "$LAB" reset >/dev/null
make -C "$LAB" load   >/dev/null 2>&1 || true

echo ">> inject S1 (scorer-weight misconfiguration)"
make -C "$LAB" inject FAULT=s1 >/dev/null

echo ">> waiting ${WAIT}s for symptoms to mature (don't diagnose immediately after inject)"
sleep "$WAIT"

echo ">> diagnosing pool '$POOL'"
mkdir -p reports
uv run routecause diagnose "$POOL" --save >/dev/null
echo

echo ">> RCA summary"
python3 -c "import json;r=json.load(open('reports/latest.json'));\
print('  fault_category:',r['fault_category']);\
print('  root_cause    :',r['root_cause']['summary']);\
print('  evidence      :',len(r['evidence']),'citations from',len({e['source'] for e in r['evidence']}),'sources');\
print('  fix validated :',r['fix']['dry_run_validated'])"
echo

echo ">> grading against ground truth"
uv run python workflow/grade_scenario.py --report reports/latest.json --expected s1
echo
echo ">> done. Render the report: 'make serve' then open http://localhost:8000/reports/latest"
