"""CLI: ``routecause diagnose <pool>`` and ``routecause serve``."""

from __future__ import annotations

import argparse
import sys

from . import store
from .orchestrator import diagnose


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="routecause", description="Inference-infra diagnostics agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagnose", help="diagnose an InferencePool and print the RCA report JSON")
    d.add_argument("pool", nargs="?", default="vllm-sim-pool")
    d.add_argument("--no-llm", action="store_true", help="deterministic only (skip LLM subagents)")
    d.add_argument("--save", action="store_true", help="persist the report for the web view")

    a = sub.add_parser("apply", help="diagnose, apply the fix, and watch recovery (mutating; needs ROUTECAUSE_ALLOW_APPLY=1)")
    a.add_argument("pool", nargs="?", default="vllm-sim-pool")
    a.add_argument("--watch-timeout", type=int, default=300)

    s = sub.add_parser("serve", help="run the FastAPI service")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.cmd == "diagnose":
        report = diagnose(args.pool, use_llm=not args.no_llm)
        if args.save:
            store.save(report)
        print(report.model_dump_json(indent=2))
        return 0

    if args.cmd == "apply":
        from . import actuator

        if not actuator.apply_enabled():
            print(f"refusing to mutate: set {actuator.ALLOW_APPLY_ENV}=1 to allow apply", file=sys.stderr)
            return 2
        report = diagnose(args.pool)
        report.recovery = actuator.actuate(report.fix, watch_timeout=args.watch_timeout)
        store.save(report)
        print(report.model_dump_json(indent=2))
        return 0 if report.recovery.slo_met else 1

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("routecause.service:app", host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
