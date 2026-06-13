#!/usr/bin/env bash
# run_scenario.sh sN — full single-scenario workflow, rerunnable by another team.
#
# Steps: reset lab -> inject fault sN -> wait for symptoms to mature -> POST
# /diagnose -> save report -> grade with the deterministic grader (RUBRIC A).
#
# Prereqs: inference-lab booted (make up && make load) at $INFERENCE_LAB, the
# routecause service running at $ROUTECAUSE_URL, and ANTHROPIC_API_KEY set.
#
# Ground truth for grading is passed as --expected (the operator injected it).
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:?usage: run_scenario.sh s1|s2|s3}"
LAB="${INFERENCE_LAB:-../inference-lab}"
BASE="${ROUTECAUSE_URL:-http://127.0.0.1:8000}"
POOL="${POOL:-vllm-sim-pool}"
WAIT="${WAIT_SECONDS:-180}"

echo ">> reset + inject $SCENARIO"
make -C "$LAB" reset >/dev/null
make -C "$LAB" inject FAULT="$SCENARIO" >/dev/null
echo ">> waiting ${WAIT}s for symptoms to mature (don't diagnose immediately)"
sleep "$WAIT"

mkdir -p reports
echo ">> POST /diagnose"
curl -s -X POST "$BASE/diagnose" -H 'content-type: application/json' \
  -d "{\"pool\":\"$POOL\"}" -o reports/latest.json

echo ">> grade"
python workflow/grade_scenario.py --report reports/latest.json --expected "$SCENARIO"
