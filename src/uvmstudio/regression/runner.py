"""Regression orchestration.

Responsibilities:
  * expand (test x seed) into a job list
  * build once, run many (the image is shared across seeds)
  * execute jobs in parallel with per-job timeout and artifact capture
  * write one reproducibility record per run
  * record everything into the regression DB

Deliberately independent of the simulator: it takes an `ISimulator` and never
inspects which one it got.
"""

from __future__ import annotations

import concurrent.futures
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import RegressionError
from ..core.logging import get_logger
from ..core.project import Project, TestSpec
from ..language.frontend import CompileRequest, SVFrontend
from ..repro.metadata import ReproRecord, ToolVersions
from ..simulator.base import (
    BuildRequest,
    BuildResult,
    RunRequest,
    RunResult,
    RunStatus,
    Simulator,
    WaveFormat,
)
from ..uvm.library import detect_uvm_version
from .db import RegressionDB


@dataclass
class Job:
    test: TestSpec
    seed: int

    @property
    def label(self) -> str:
        return f"{self.test.name}/seed_{self.seed}"


@dataclass
class RegressionOutcome:
    regression_id: int
    summary: dict
    results: list[tuple[Job, RunResult]] = field(default_factory=list)
    build: BuildResult | None = None

    @property
    def status(self) -> str:
        return self.summary.get("status", "NOT_VERIFIED")

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


def expand_seeds(spec: TestSpec, *, base_seed: int | None = None) -> list[int]:
    """Deterministic seed expansion.

    With an explicit base seed the sequence is base, base+1, ...; that keeps a
    regression exactly reproducible. Without one, seeds are drawn from a random
    base but still recorded per run, so any single run remains reproducible.
    """
    if base_seed is None:
        base_seed = random.randrange(1, 2**31 - spec.seeds - 1)
    return [base_seed + i for i in range(spec.seeds)]


class RegressionRunner:
    def __init__(
        self,
        project: Project,
        simulator: Simulator,
        *,
        frontend: SVFrontend | None = None,
        db: RegressionDB | None = None,
        jobs: int = 1,
        on_result: Callable[[Job, RunResult], None] | None = None,
    ) -> None:
        self.project = project
        self.sim = simulator
        self.frontend = frontend
        self.db = db or RegressionDB(project.results_path / "regression.db")
        self.jobs = max(1, jobs)
        self.on_result = on_result
        self.log = get_logger()

    # -- build -------------------------------------------------------------
    def build(self, *, waves: WaveFormat | None = None) -> BuildResult:
        p = self.project
        # Waves must be compiled in; deciding at run time is impossible with a
        # compiled simulator. "on_fail" therefore compiles tracing in and only
        # *emits* on failure, at the cost of some run-time overhead.
        wave_fmt = waves if waves is not None else (
            WaveFormat.NONE if p.waves == "never" else WaveFormat.FST
        )
        req = BuildRequest(
            files=p.source_files(),
            top=p.top,
            build_dir=p.build_path,
            include_dirs=p.include_dirs(),
            defines=p.defines(),
            language_standard=p.language_standard,
            timescale=p.timescale,
            coverage=p.coverage,
            waves=wave_fmt,
            uvm_home=p.resolved_uvm_home(),
            threads=self.jobs,
            extra_args=list(p.backend_options.get("extra_build_args", [])),
            # Peak compiler memory knob. Left unset the backend picks a
            # measured default; a project on a memory-tight runner can raise
            # it. See VerilatorSimulator.compile_split().
            compile_split=p.backend_options.get("compile_split"),
        )
        self.log.info(
            "building", backend=self.sim.name, top=p.top, files=len(req.files)
        )
        return self.sim.build(req)

    # -- run ---------------------------------------------------------------
    def run_regression(
        self,
        *,
        tier: str = "L1",
        tests: list[str] | None = None,
        base_seed: int | None = None,
        name: str | None = None,
        seeds_override: int | None = None,
    ) -> RegressionOutcome:
        p = self.project
        specs = (
            [p.get_test(t) for t in tests] if tests else p.tests_in_tier(tier)
        )
        if not specs:
            raise RegressionError(
                f"no tests selected (tier={tier}, tests={tests}); "
                f"project defines {[t.name for t in p.tests]}"
            )
        if seeds_override:
            specs = [
                TestSpec(**{**spec.__dict__, "seeds": seeds_override}) for spec in specs
            ]

        build = self.build()
        frontend_version = (
            f"{self.frontend.name}-{self.frontend.version}" if self.frontend else None
        )
        uvm_ver = detect_uvm_version(p.resolved_uvm_home()) if p.resolved_uvm_home() else None

        from ..repro.metadata import GitState

        git = GitState.capture(p.root)
        reg_id = self.db.start_regression(
            name=name or f"{p.name}-{tier}",
            project=p.name,
            tier=tier,
            git_commit=git.commit,
            git_branch=git.branch,
            git_dirty=int(bool(git.dirty)) if git.dirty is not None else None,
            backend=self.sim.name,
            backend_version=self.sim.version() if self.sim.is_available() else None,
            frontend_version=frontend_version,
            uvm_version=uvm_ver,
            host=str(getattr(self.sim, "exec_host", lambda: "native")()),
        )

        if not build.ok:
            # Build failure blocks every job; record them so the dashboard shows
            # the true test count rather than an empty regression.
            for spec in specs:
                for seed in expand_seeds(spec, base_seed=base_seed):
                    blocked = RunResult(
                        status=RunStatus.BLOCKED,
                        seed=seed,
                        returncode=-1,
                        duration_s=0.0,
                        reasons=["build failed — see build.log"],
                        failure_signature="BUILD_FAILURE",
                    )
                    self.db.record_run(
                        reg_id, blocked, test=spec.name,
                        uvm_testname=spec.uvm_testname, tier=spec.tier,
                    )
            summary = self.db.finish_regression(reg_id)
            return RegressionOutcome(reg_id, summary, [], build)

        jobs = [
            Job(spec, seed)
            for spec in specs
            for seed in expand_seeds(spec, base_seed=base_seed)
        ]
        self.log.info("regression start", id=reg_id, jobs=len(jobs), parallel=self.jobs)

        results: list[tuple[Job, RunResult]] = []
        t0 = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as pool:
            futures = {
                pool.submit(self._run_one, job, build, reg_id): job for job in jobs
            }
            for fut in concurrent.futures.as_completed(futures):
                job = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    res = RunResult(
                        status=RunStatus.ERROR,
                        seed=job.seed,
                        returncode=-1,
                        duration_s=0.0,
                        reasons=[f"runner exception: {exc}"],
                        failure_signature=f"RUNNER_EXCEPTION:{type(exc).__name__}",
                    )
                results.append((job, res))
                if self.on_result:
                    self.on_result(job, res)
                self.log.info(
                    res.status.value, test=job.test.name, seed=job.seed,
                    dur=f"{res.duration_s:.1f}s",
                )

        summary = self.db.finish_regression(reg_id)
        summary["wall_s"] = round(time.monotonic() - t0, 2)
        results.sort(key=lambda r: (r[0].test.name, r[0].seed))
        return RegressionOutcome(reg_id, summary, results, build)

    # -- single job --------------------------------------------------------
    def _run_one(self, job: Job, build: BuildResult, reg_id: int) -> RunResult:
        p = self.project
        run_dir = p.results_path / job.test.name / f"seed_{job.seed}"
        run_dir.mkdir(parents=True, exist_ok=True)

        want_waves = p.waves in ("always", "on_fail")
        req = RunRequest(
            binary=build.binary,  # type: ignore[arg-type]
            run_dir=run_dir,
            seed=job.seed,
            uvm_testname=job.test.uvm_testname,
            plusargs=list(job.test.plusargs),
            waves=WaveFormat.FST if want_waves else WaveFormat.NONE,
            coverage=p.coverage,
            timeout_s=job.test.timeout_s,
            expect=job.test.expect,
        )

        record = ReproRecord.begin(
            project_name=p.name,
            project_root=p.root,
            test=job.test.name,
            seed=job.seed,
            top=p.top,
            language_standard=p.language_standard,
            source_files=p.source_files(),
            include_paths=p.include_dirs(),
            defines=p.defines(),
            plusargs=list(job.test.plusargs),
            uvm_testname=job.test.uvm_testname,
        )

        result = self.sim.run(req)

        record.tools = ToolVersions(
            frontend=self.frontend.name if self.frontend else None,
            frontend_version=self.frontend.version if self.frontend else None,
            simulator_backend=result.backend or self.sim.name,
            simulator_version=result.backend_version or None,
            simulator_banner=(
                self.sim.version_banner()
                if hasattr(self.sim, "version_banner")
                else None
            ),
            exec_host=str(getattr(self.sim, "exec_host", lambda: "native")()),
            uvm_version=(
                detect_uvm_version(p.resolved_uvm_home())
                if p.resolved_uvm_home()
                else None
            ),
            uvm_home=str(p.resolved_uvm_home()) if p.resolved_uvm_home() else None,
            solver="z3" if getattr(self.sim, "solver_available", lambda: False)() else None,
            studio_version=_studio_version(),
        )
        record.build_command = build.command
        record.run_command = result.command
        record.result = result.status.value
        record.duration_s = result.duration_s
        record.artifacts = {
            "log": str(result.log_path) if result.log_path else None,
            "waves": str(result.wave_path) if result.wave_path else None,
            "coverage": str(result.coverage_path) if result.coverage_path else None,
        }
        repro_path = record.write(run_dir / "repro.json")

        # Waves are compiled in but only *kept* per policy.
        if p.waves == "on_fail" and result.status is RunStatus.PASS and result.wave_path:
            try:
                result.wave_path.unlink()
                result.wave_path = None
            except OSError:
                pass

        self.db.record_run(
            reg_id, result, test=job.test.name,
            uvm_testname=job.test.uvm_testname, tier=job.test.tier,
            repro_path=repro_path,
        )
        return result


def _studio_version() -> str:
    try:
        from importlib.metadata import version

        return version("uvm-verification-studio")
    except Exception:
        return "0.0.0+dev"
