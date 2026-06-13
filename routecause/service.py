"""FastAPI service — the live surface for RouteCause.

Two routes matter (B1): ``POST /diagnose`` returns the report JSON, and
``GET /reports/latest`` renders a single human-readable RCA report (root cause /
evidence / fix) — deliberately one report, never a multi-panel dashboard.
"""

from __future__ import annotations

import html
from pathlib import Path

import jinja2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import store
from .config import ROUTER_DISPLAY_NAME
from .orchestrator import diagnose_async
from .schema import FaultCategory, Report

app = FastAPI(
    title="RouteCause",
    description="Autonomous diagnostics agent for llm-d Router (EPP) inference gateways.",
    version="1.0",
)

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=True,
)

_CAT_COLOR = {
    FaultCategory.SCORER_WEIGHT_MISCONFIG: "#d29922",
    FaultCategory.UNHEALTHY_ENDPOINT: "#f85149",
    FaultCategory.PREFIX_CACHE_DISABLED: "#a371f7",
    FaultCategory.OTHER: "#3fb950",
}


def _diff_to_html(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        esc = html.escape(line)
        if line.startswith(("+++", "---", "@@")):
            out.append(f'<span class="hd">{esc}</span>')
        elif line.startswith("+"):
            out.append(f'<span class="add">{esc}</span>')
        elif line.startswith("-"):
            out.append(f'<span class="del">{esc}</span>')
        else:
            out.append(esc)
    return "\n".join(out)


def render_report(r: Report) -> str:
    tmpl = _env.get_template("report.html")
    return tmpl.render(
        r=r,
        cat_color=_CAT_COLOR.get(r.fault_category, "#9aa3b2"),
        n_sources=len({e.source for e in r.evidence}),
        fix_diff_html=_diff_to_html(r.fix.diff or ""),
        router_name=ROUTER_DISPLAY_NAME,
    )


class DiagnoseRequest(BaseModel):
    pool: str = "vllm-sim-pool"


_LANDING = (
    "<body style='background:#0f1115;color:#e6e8ec;font-family:sans-serif;max-width:680px;"
    "margin:60px auto;padding:0 20px'><h1>Route<span style='color:#c96442'>Cause</span></h1>"
    "<p>No report yet. Trigger a diagnosis:</p>"
    "<pre style='background:#171a21;padding:14px;border-radius:8px'>"
    "curl -s -X POST localhost:8000/diagnose -H 'content-type: application/json' "
    "-d '{\"pool\":\"vllm-sim-pool\"}'</pre>"
    "<p>Then reload <a style='color:#c96442' href='/reports/latest'>/reports/latest</a>.</p></body>"
)


@app.post("/diagnose", response_model=Report)
async def post_diagnose(req: DiagnoseRequest) -> Report:
    report = await diagnose_async(req.pool)
    store.save(report)
    return report


@app.get("/reports/latest", response_class=HTMLResponse)
async def latest_html() -> HTMLResponse:
    r = store.latest()
    if r is None:
        return HTMLResponse(_LANDING)
    return HTMLResponse(render_report(r))


@app.get("/reports/latest.json", response_model=Report)
async def latest_json() -> Report:
    r = store.latest()
    if r is None:
        raise HTTPException(status_code=404, detail="no report yet; POST /diagnose first")
    return r


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    r = store.latest()
    return HTMLResponse(render_report(r) if r else _LANDING)
