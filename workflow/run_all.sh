#!/usr/bin/env bash
# run_all.sh — dynamic workflow over all three scenarios with a regression pass.
#
# For each scenario S1, S2, S3: reset -> inject -> diagnose -> grade. After S3,
# re-run S1 as a regression check (A7: previously passed scenarios still pass).
# Rerunnable by another team (see run_scenario.sh prereqs).
set -euo pipefail
cd "$(dirname "$0")/.."

for s in s1 s2 s3; do
  echo "======================== SCENARIO ${s} ========================"
  workflow/run_scenario.sh "$s"
done

echo "======================== REGRESSION (re-run S1) ========================"
workflow/run_scenario.sh s1

echo "All scenarios + regression graded."
