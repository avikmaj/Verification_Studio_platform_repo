"""UVM Verification Studio — HTTP API.

A *thin* layer over the platform modules. It imports `uvmstudio.*` directly and
holds no verification logic of its own, so the web dashboard can never disagree
with `uvmstudio report` — both render the same functions.

Two responsibilities:

  1. **Read** — projects, regressions, runs, coverage, waveforms, repro records
  2. **Execute** — queue compile/lint/build/regress jobs and stream their logs

Execution is what makes this an `ExecHost.REMOTE` for `ISimulator`: a laptop or
CI runner without a local Verilator can drive a real simulation through here.

Security posture: this service runs a simulator, which runs arbitrary code.
It is designed for a trusted deployment — a bearer token gates every mutating
route, jobs run in a workspace under a configured root, and the job runner
enforces timeouts. It is **not** hardened for untrusted multi-tenant input;
that is stated plainly rather than implied.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from uvmstudio.core.errors import StudioError
from uvmstudio.core.platform import platform_report
from uvmstudio.core.project import Project
from uvmstudio.coverage.model import VerilatorCoverageReader, render_coverage_markdown
from uvmstudio.language.frontend import (
    CompileRequest,
    available_frontends,
    get_frontend,
)
from uvmstudio.lint.engine import LintEngine
from uvmstudio.regression.db import RegressionDB
from uvmstudio.regression.report import build_report
from uvmstudio.repro.metadata import ReproRecord
from uvmstudio.simulator.base import (
    available_simulators,
    get_simulator,
    registered_simulators,
)
from uvmstudio.uvm.library import find_uvm_home, inspect_uvm
from uvmstudio.waveform.reader import open_waveform

from .jobs import JobKind, JobQueue

# --- configuration --------------------------------------------------------
WORKSPACE_ROOT = Path(os.environ.get("UVMSTUDIO_WORKSPACE", "/workspace")).resolve()
API_TOKEN = os.environ.get("UVMSTUDIO_API_TOKEN", "")
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("UVMSTUDIO_CORS_ORIGINS", "*").split(",")
    if o.strip()
]

app = FastAPI(
    title="UVM Verification Studio API",
    version="0.3.0",
    description="Thin HTTP layer over the UVM Verification Studio platform modules.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

queue = JobQueue(workspace_root=WORKSPACE_ROOT)


# --- auth -----------------------------------------------------------------
def require_token(authorization: str = Header(default="")) -> None:
    """Gate mutating routes. No token configured => execution is disabled.

    Refusing by default is deliberate: an unauthenticated endpoint that runs a
    simulator is a remote code execution primitive.
    """
    if not API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="execution disabled: UVMSTUDIO_API_TOKEN is not configured",
        )
    expected = f"Bearer {API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


# --- helpers --------------------------------------------------------------
def _safe_project_path(name: str) -> Path:
    """Resolve a project directory, refusing anything outside the workspace."""
    candidate = (WORKSPACE_ROOT / name).resolve()
    try:
        candidate.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes the workspace root")
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"no project directory {name!r}")
    return candidate


def _load(name: str) -> Project:
    try:
        return Project.load(_safe_project_path(name))
    except StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _safe_artifact(project: Project, rel: str) -> Path:
    p = (project.root / rel).resolve()
    try:
        p.relative_to(project.root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes the project root")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"no such artifact: {rel}")
    return p


# --- health / environment -------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.get("/env")
def env() -> dict:
    """What this deployment can actually do. The dashboard renders this verbatim."""
    sims: dict[str, Any] = {}
    for name in registered_simulators():
        try:
            s = get_simulator(name)
            ok = s.is_available()
            sims[name] = {
                "available": ok,
                "version": s.version() if ok else None,
                "exec_host": getattr(s, "exec_host", lambda: "native")(),
                "solver": getattr(s, "solver_available", lambda: None)() if ok else None,
                "capabilities": (
                    {k: v.value for k, v in s.capabilities().items()} if ok else {}
                ),
            }
        except Exception as exc:
            sims[name] = {"available": False, "error": str(exc)}

    fes = {}
    for name in available_frontends():
        fe = get_frontend(name)
        fes[name] = {
            "version": fe.version,
            "capabilities": {k: v.value for k, v in fe.capabilities().items()},
        }

    home = find_uvm_home(os.environ.get("UVM_HOME"))
    return {
        "platform": platform_report(),
        "frontends": fes,
        "simulators": sims,
        "uvm": inspect_uvm(str(home)).to_dict() if home else None,
        "workspace": str(WORKSPACE_ROOT),
        "execution_enabled": bool(API_TOKEN),
        "disk_free_mb": shutil.disk_usage(WORKSPACE_ROOT).free // (1 << 20)
        if WORKSPACE_ROOT.exists()
        else None,
    }


# --- projects -------------------------------------------------------------
@app.get("/projects")
def list_projects() -> dict:
    if not WORKSPACE_ROOT.exists():
        return {"workspace": str(WORKSPACE_ROOT), "projects": []}
    out = []
    for d in sorted(WORKSPACE_ROOT.iterdir()):
        if not (d / "uvmstudio.yaml").exists():
            continue
        try:
            p = Project.load(d)
            out.append(
                {
                    "name": p.name,
                    "dir": d.name,
                    "top": p.top,
                    "language_standard": p.language_standard,
                    "backend": p.default_backend,
                    "tests": [
                        {"name": t.name, "tier": t.tier, "seeds": t.seeds,
                         "expect": t.expect, "tags": t.tags}
                        for t in p.tests
                    ],
                }
            )
        except StudioError as exc:
            out.append({"name": d.name, "dir": d.name, "error": str(exc)})
    return {"workspace": str(WORKSPACE_ROOT), "projects": out}


@app.get("/projects/{name}/design")
def project_design(name: str) -> dict:
    """Compile and return the elaborated IR — hierarchy, classes, covergroups."""
    proj = _load(name)
    fe = get_frontend("slang")
    res = fe.compile(
        CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
            suppress_warnings=list(proj.backend_options.get("suppress_warnings", [])),
        )
    )
    return {
        "ok": res.ok,
        "frontend": f"{res.frontend} {res.frontend_version}",
        "duration_s": round(res.duration_s, 3),
        "diagnostics": res.diagnostics.to_dict(),
        "design": res.design.to_dict() if res.design else None,
    }


@app.get("/projects/{name}/lint")
def project_lint(name: str, all_files: bool = False) -> dict:
    proj = _load(name)
    fe = get_frontend("slang")
    res = fe.compile(
        CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
        )
    )
    exclude = [proj.resolved_uvm_home()] if proj.resolved_uvm_home() else []
    engine = LintEngine(
        scope_paths=None if all_files else [proj.root], exclude_paths=exclude
    )
    findings = engine.check(res.design) if res.design else []
    return {
        "frontend_ok": res.ok,
        "diagnostics": res.diagnostics.to_dict(),
        "findings": [f.to_dict() for f in findings],
        "rules": engine.rule_catalogue(),
    }


# --- regressions ----------------------------------------------------------
def _db(proj: Project) -> RegressionDB:
    return RegressionDB(proj.results_path / "regression.db")


@app.get("/projects/{name}/regressions")
def regressions(name: str, limit: int = Query(default=25, le=200)) -> dict:
    proj = _load(name)
    db = _db(proj)
    return {
        "history": db.history(proj.name, limit=limit),
        "clusters": db.clusters(limit=25),
        "seed_effectiveness": db.seed_effectiveness(limit=25),
    }


@app.get("/projects/{name}/regressions/{regression_id}")
def regression_report(name: str, regression_id: int) -> dict:
    proj = _load(name)
    try:
        return build_report(_db(proj), regression_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{name}/runs/{run_log:path}", response_class=PlainTextResponse)
def run_log(name: str, run_log: str, tail: int = Query(default=2000, le=100_000)) -> str:
    """Fetch a log/artifact by project-relative path."""
    proj = _load(name)
    p = _safe_artifact(proj, run_log)
    text = p.read_text(errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-tail:])


@app.get("/projects/{name}/repro")
def repro(name: str, path: str) -> dict:
    proj = _load(name)
    p = _safe_artifact(proj, path)
    try:
        return ReproRecord.load(p)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- coverage -------------------------------------------------------------
@app.get("/projects/{name}/coverage")
def coverage(name: str, markdown: bool = False) -> dict:
    proj = _load(name)
    dats = sorted(proj.results_path.rglob("coverage.dat"))
    if not dats:
        raise HTTPException(
            status_code=404,
            detail="no coverage.dat found — run a regression with coverage enabled",
        )
    db = VerilatorCoverageReader().load_many(dats)
    out = db.summary()
    out["databases"] = len(dats)
    if markdown:
        out["markdown"] = render_coverage_markdown(db)
    return out


# --- waveform -------------------------------------------------------------
@app.get("/projects/{name}/waveform")
def waveform(name: str, path: str) -> dict:
    proj = _load(name)
    p = _safe_artifact(proj, path)
    try:
        db, fmt = open_waveform(p)
    except StudioError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    data = db.summary()
    data["format"] = fmt
    return data


@app.get("/projects/{name}/waveform/signal")
def waveform_signal(
    name: str,
    path: str,
    signal: str,
    t0: int = 0,
    t1: int | None = None,
    limit: int = Query(default=5000, le=200_000),
) -> dict:
    proj = _load(name)
    p = _safe_artifact(proj, path)
    db, fmt = open_waveform(p)
    if db.find(signal) is None:
        raise HTTPException(status_code=404, detail=f"signal not found: {signal}")
    end = db.end_time if t1 is None else t1
    series = db.window(signal, t0, end)[:limit]
    return {
        "signal": signal,
        "format": fmt,
        "t0": t0,
        "t1": end,
        "count": len(series),
        "changes": [{"time": t, "value": v} for t, v in series],
    }


# --- execution ------------------------------------------------------------
class JobRequest(BaseModel):
    project: str = Field(description="project directory name inside the workspace")
    kind: JobKind
    tier: str = "L1"
    tests: list[str] | None = None
    seed: int | None = None
    seeds: int | None = None
    jobs: int = 2
    timeout_s: float = 3600.0


@app.post("/jobs", dependencies=[Depends(require_token)])
def submit_job(req: JobRequest) -> dict:
    _load(req.project)              # validate before queueing
    job = queue.submit(req.model_dump())
    return job.to_dict()


@app.get("/jobs")
def list_jobs(limit: int = Query(default=50, le=500)) -> dict:
    return {"jobs": [j.to_dict() for j in queue.list(limit=limit)]}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job.to_dict()


@app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
def job_log(job_id: str, tail: int = Query(default=500, le=50_000)) -> str:
    job = queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job.log_tail(tail)


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_token)])
def cancel_job(job_id: str) -> dict:
    job = queue.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    return job.to_dict()
