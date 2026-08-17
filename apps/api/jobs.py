"""In-process job queue for remote execution.

Deliberately small: a bounded worker pool, an append-only log per job, and a
status that follows the same discipline as everything else in the platform —
a job that did not produce evidence is `NOT_VERIFIED`, never `PASS`.

Why in-process rather than Celery/Redis: a verification job is minutes-to-hours
of subprocess time, so throughput is irrelevant and an extra broker would be
infrastructure without benefit. The queue is intentionally replaceable — swap
this class for a farm submitter (LSF/Slurm) without touching `app.py`.

State is lost on restart. That is acceptable because the durable record lives
where it should: the regression SQLite DB and the per-run `repro.json`.
"""

from __future__ import annotations

import io
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from uvmstudio.core.project import Project
from uvmstudio.language.frontend import CompileRequest, get_frontend
from uvmstudio.lint.engine import LintEngine
from uvmstudio.regression.runner import RegressionRunner
from uvmstudio.simulator.base import get_simulator

MAX_JOBS_RETAINED = 200
MAX_CONCURRENT = 1          # a simulation build saturates a small container


class JobKind(str, Enum):
    COMPILE = "compile"
    LINT = "lint"
    BUILD = "build"
    REGRESS = "regress"


class JobState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class Job:
    id: str
    kind: JobKind
    project: str
    params: dict[str, Any]
    state: JobState = JobState.QUEUED
    status: str = "NOT_VERIFIED"      # PASS | FAIL | NOT_VERIFIED | BLOCKED | ERROR
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: dict | None = None
    error: str | None = None
    _log: io.StringIO = field(default_factory=io.StringIO, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def write(self, text: str) -> None:
        with self._lock:
            self._log.write(text.rstrip("\n") + "\n")

    def log_tail(self, n: int) -> str:
        with self._lock:
            lines = self._log.getvalue().splitlines()
        return "\n".join(lines[-n:])

    @property
    def duration_s(self) -> float | None:
        if self.started is None:
            return None
        return (self.finished or time.time()) - self.started

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "project": self.project,
            "params": {k: v for k, v in self.params.items()
                       if k not in ("project", "kind")},
            "state": self.state.value,
            "status": self.status,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "duration_s": round(self.duration_s, 2) if self.duration_s else None,
            "result": self.result,
            "error": self.error,
        }


class JobQueue:
    def __init__(self, workspace_root: Path, max_concurrent: int = MAX_CONCURRENT):
        self.root = Path(workspace_root)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(max_concurrent)

    # -- queue ops ---------------------------------------------------------
    def submit(self, params: dict) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=JobKind(params["kind"]),
            project=params["project"],
            params=params,
        )
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > MAX_JOBS_RETAINED:
                self._jobs.popitem(last=False)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            return list(reversed(list(self._jobs.values())))[:limit]

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        job._cancel.set()
        if job.state is JobState.QUEUED:
            job.state = JobState.CANCELLED
            job.status = "BLOCKED"
            job.finished = time.time()
        return job

    # -- worker ------------------------------------------------------------
    def _run(self, job: Job) -> None:
        self._sem.acquire()
        try:
            if job._cancel.is_set():
                job.state = JobState.CANCELLED
                job.status = "BLOCKED"
                job.finished = time.time()
                return

            job.state = JobState.RUNNING
            job.started = time.time()
            job.write(f"[{job.kind.value}] project={job.project}")

            proj = Project.load(self.root / job.project)
            handler = {
                JobKind.COMPILE: self._compile,
                JobKind.LINT: self._lint,
                JobKind.BUILD: self._build,
                JobKind.REGRESS: self._regress,
            }[job.kind]
            handler(job, proj)
            job.state = JobState.DONE
        except Exception as exc:
            job.state = JobState.FAILED
            job.status = "ERROR"
            job.error = f"{type(exc).__name__}: {exc}"
            job.write(job.error)
            job.write(traceback.format_exc(limit=8))
        finally:
            job.finished = time.time()
            self._sem.release()

    # -- handlers ----------------------------------------------------------
    @staticmethod
    def _request(proj: Project) -> CompileRequest:
        return CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
            suppress_warnings=list(proj.backend_options.get("suppress_warnings", [])),
        )

    def _compile(self, job: Job, proj: Project) -> None:
        fe = get_frontend("slang")
        res = fe.compile(self._request(proj))
        job.write(res.diagnostics.format(root=proj.root, limit=200))
        job.status = "PASS" if res.ok else "FAIL"
        job.result = {
            "errors": res.error_count,
            "warnings": res.warning_count,
            "stats": res.design.stats() if res.design else None,
            "frontend": f"{res.frontend} {res.frontend_version}",
        }
        job.write(f"STATUS: {job.status}")

    def _lint(self, job: Job, proj: Project) -> None:
        fe = get_frontend("slang")
        res = fe.compile(self._request(proj))
        exclude = [proj.resolved_uvm_home()] if proj.resolved_uvm_home() else []
        engine = LintEngine(scope_paths=[proj.root], exclude_paths=exclude)
        findings = engine.check(res.design) if res.design else []
        for f in findings:
            job.write(f.format(root=proj.root))
        errors = sum(1 for f in findings if f.severity_is_error)
        job.status = "FAIL" if (not res.ok or errors) else "PASS"
        job.result = {
            "findings": len(findings),
            "errors": errors,
            "items": [f.to_dict() for f in findings],
        }
        job.write(f"STATUS: {job.status}")

    def _build(self, job: Job, proj: Project) -> None:
        sim = get_simulator(proj.default_backend, jobs=job.params.get("jobs", 2))
        runner = RegressionRunner(proj, sim, jobs=job.params.get("jobs", 2))
        result = runner.build()
        job.write(result.log[-20000:])
        job.status = "PASS" if result.ok else "BLOCKED"
        job.result = result.to_dict()
        job.write(f"STATUS: {job.status}")

    def _regress(self, job: Job, proj: Project) -> None:
        sim = get_simulator(proj.default_backend, jobs=job.params.get("jobs", 2))
        runner = RegressionRunner(
            proj,
            sim,
            frontend=get_frontend("slang"),
            jobs=job.params.get("jobs", 2),
            on_result=lambda j, r: job.write(
                f"{r.status.value:<13} {j.test.name} seed={j.seed} "
                f"({r.duration_s:.1f}s)"
                + (f"  {r.reasons[0]}" if r.reasons else "")
            ),
        )
        outcome = runner.run_regression(
            tier=job.params.get("tier", "L1"),
            tests=job.params.get("tests"),
            base_seed=job.params.get("seed"),
            seeds_override=job.params.get("seeds"),
        )

        # A build failure blocks every job. Without the build log the operator
        # sees "BLOCKED x6" and no cause — surface it.
        if outcome.build is not None and not outcome.build.ok:
            job.write("--- build failed; every run is BLOCKED ---")
            job.write(outcome.build.log[-20000:])

        job.status = outcome.summary.get("status", "NOT_VERIFIED")
        job.result = {
            "regression_id": outcome.regression_id,
            "summary": outcome.summary,
        }
        job.write(
            f"STATUS: {job.status}  "
            f"({outcome.summary.get('passed')}/{outcome.summary.get('total')} passed)"
        )
