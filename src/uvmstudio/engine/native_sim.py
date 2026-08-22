"""NativeSimulator — the engine behind `--backend native`.

EXPERIMENTAL. Interprets the slang bound AST directly: build is elaboration
(no C++ compile, no external toolchain), run executes the kernel and dumps
waves through our own VCD writer. The PASS rule is unchanged: named positive
evidence only, and UVM is not claimed — attempting a UVM design fails loudly
at elaboration rather than pretending.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core.errors import UnsupportedFeature
from ..plugins.interfaces import FeatureStatus
from .fourstate import FourState
from .interp import SUPPORTED, Interp
from .kernel import Kernel, SimulationError
from .vcd_writer import VCDWriter
from ..simulator.base import (
    BuildRequest,
    BuildResult,
    RunRequest,
    RunResult,
    RunStatus,
    Simulator,
    WaveFormat,
)


def _compile(files: list[Path], include_dirs: list[Path],
             defines: list[str]) -> Any:
    from pyslang import ast, syntax

    comp = ast.Compilation()
    # includes/defines go through slang's preprocessor options via source text
    # prelude; slang's python Bag options plumbing is not exposed uniformly
    # across versions, so defines are injected as text — visible and exact.
    prelude = "".join(
        f"`define {d.replace('=', ' ', 1)}\n" for d in defines
    )
    for f in files:
        text = Path(f).read_text()
        tree = syntax.SyntaxTree.fromText(prelude + text, str(f))
        comp.addSyntaxTree(tree)
    return comp


class NativeSimulator(Simulator):
    name = "native"

    def __init__(self, **_ignored: Any) -> None:
        self._designs: dict[str, Any] = {}

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        try:
            import pyslang  # noqa: F401
            return True
        except ImportError:
            return False

    def version(self) -> str:
        import pyslang
        return f"native-0.5 (slang {pyslang.__version__})"

    def exec_host(self) -> str:
        return "in-process"

    def capabilities(self) -> dict[str, FeatureStatus]:
        E, S, U, P = (FeatureStatus.EXPERIMENTAL, FeatureStatus.SUPPORTED,
                      FeatureStatus.UNSUPPORTED, FeatureStatus.PLANNED)
        return {
            "backend_installed": S if self.is_available() else U,
            "event_driven_kernel": E,
            "four_state": E,
            "rtl_synthesizable_subset": E,
            "timing_delays": E,
            "vcd_waves": E,
            "fst_waves": U,
            "classes": E,
            "randomize": E,       # N4: z3-backed, dist solved not dropped
            "constraints": E,     # N4: hard/soft/inside/dist/impl/if-else
            "covergroups": E,   # N5: coverpoints, bins, cross, illegal/ignore
            "sva_concurrent": E,
            "uvm_1_2": U,
            "uvm_ieee_1800_2": U,
            "dpi_c": U,
            "supported_subset": E,
        }

    def solver_available(self) -> bool:
        try:
            import z3  # noqa: F401
            return True
        except ImportError:
            return False

    # -- build = elaborate -------------------------------------------------
    def build(self, request: BuildRequest) -> BuildResult:
        t0 = time.monotonic()
        request.build_dir.mkdir(parents=True, exist_ok=True)
        log_lines: list[str] = []
        try:
            comp = _compile(request.files, request.include_dirs,
                            request.defines)
            errors = [str(d) for d in comp.getAllDiagnostics() if d.isError]
            if errors:
                return BuildResult(
                    ok=False, binary=None,
                    log="\n".join(errors[:50]),
                    duration_s=time.monotonic() - t0,
                    backend=self.name, backend_version=self.version(),
                    status=RunStatus.BLOCKED,
                    reasons=[f"frontend reported {len(errors)} error(s)"],
                )
            # trial elaboration: surface UnsupportedFeature at build time,
            # where an operator expects capability errors to appear
            Interp(comp, Kernel()).elaborate()
            self._designs[str(request.build_dir)] = (request, comp)
            log_lines.append(
                f"native elaboration ok: top={request.top}"
            )
            marker = request.build_dir / "native.elaborated"
            marker.write_text("ok\n")
            return BuildResult(
                ok=True, binary=marker,
                log="\n".join(log_lines),
                duration_s=time.monotonic() - t0,
                backend=self.name, backend_version=self.version(),
                status=RunStatus.PASS,
            )
        except UnsupportedFeature as exc:
            return BuildResult(
                ok=False, binary=None, log=str(exc),
                duration_s=time.monotonic() - t0,
                backend=self.name, backend_version=self.version(),
                status=RunStatus.BLOCKED,
                reasons=[str(exc)],
            )

    # -- run = interpret ---------------------------------------------------
    def run(self, request: RunRequest) -> RunResult:
        t0 = time.monotonic()
        request.run_dir.mkdir(parents=True, exist_ok=True)
        stored = self._designs.get(str(request.binary.parent))
        if stored is None:
            return RunResult(
                status=RunStatus.BLOCKED, seed=request.seed, returncode=-1,
                duration_s=0.0, backend=self.name,
                reasons=["no elaborated design for this build dir — "
                         "build() must run in the same process (native "
                         "backend holds the design in memory)"],
            )
        build_req, comp = stored
        kernel = Kernel()
        interp = Interp(comp, kernel, seed=request.seed)
        interp.elaborate()

        writer: VCDWriter | None = None
        if request.waves is not WaveFormat.NONE:
            writer = VCDWriter(request.run_dir / "waves.vcd")
            for path, sigs in interp.scopes:
                writer.enter_path(path)
                for s in sigs:
                    writer.add_signal(s.key, s.name.rsplit(".", 1)[-1], s.width)
            writer.close_scopes()
            writer.end_definitions()
            for s in kernel.signals:
                writer.change(s.key, s.value, 0)
            kernel.on_signal_change = lambda sig, t: writer.change(
                sig.key, sig.value, t)

        log_path = request.run_dir / "sim.log"
        status = RunStatus.NOT_VERIFIED
        reasons: list[str] = []
        rc = 0
        try:
            kernel.run()
            if kernel.finish_time is not None:
                status = RunStatus.PASS
            else:
                status = RunStatus.NOT_VERIFIED
                reasons.append(
                    "simulation ended by event starvation, not $finish — "
                    "no positive completion evidence"
                )
        except (SimulationError, UnsupportedFeature) as exc:
            status = RunStatus.FAIL if isinstance(exc, SimulationError) \
                else RunStatus.BLOCKED
            reasons.append(str(exc))
            rc = 1

        text = "".join(kernel.stdout)
        errors = [ln for ln in text.splitlines()
                  if ln.startswith(("ERROR", "FATAL"))]
        if errors and status is RunStatus.PASS:
            status = RunStatus.FAIL
            reasons.extend(errors[:5])

        # concurrent assertion verdicts (engine N2)
        sva_fails = sum(r.fails for r in kernel.sva)
        sva_vacuous = [r.name for r in kernel.sva if r.vacuous]
        if sva_fails and status is RunStatus.PASS:
            status = RunStatus.FAIL
            reasons.extend(
                f"assertion {r.name} failed {r.fails}x"
                for r in kernel.sva if r.fails)
        if sva_vacuous:
            # GATE 7 policy: vacuous proves nothing — surfaced, not hidden
            reasons.append(
                "vacuous assertion(s): " + ", ".join(sva_vacuous[:5]))
        log_path.write_text(
            text + f"\n- native engine: "
            f"{'$finish at ' + str(kernel.finish_time) if kernel.finish_time is not None else 'event starvation'}"
            f" (t={kernel.time})\n"
        )
        if writer:
            writer.close(kernel.time)

        counters = {
            "sva_asserts": sum(1 for r in kernel.sva if r.kind == "assert"),
            "sva_passes": sum(r.passes for r in kernel.sva),
            "sva_fails": sum(r.fails for r in kernel.sva),
            "sva_vacuous": sum(1 for r in kernel.sva if r.vacuous),
            "sva_covered": sum(r.covered for r in kernel.sva),
        } if kernel.sva else {}

        # functional coverage (engine N5)
        cg_reports = [cg.report() for cg in kernel.covergroups]
        if cg_reports:
            overall = sum(r["overall"] for r in cg_reports) / len(cg_reports)
            counters["covergroups"] = len(cg_reports)
            counters["functional_coverage_pct"] = round(overall, 2)

        return RunResult(
            status=status, seed=request.seed, returncode=rc,
            duration_s=time.monotonic() - t0, backend=self.name,
            log_path=log_path,
            wave_path=(request.run_dir / "waves.vcd") if writer else None,
            reasons=reasons, counters=counters,
        )

    def failure_signature(self, result: RunResult) -> str:
        return ";".join(result.reasons[:2]) or "native-unknown"
