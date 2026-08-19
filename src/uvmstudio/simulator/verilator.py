"""Verilator backend.

Verilator is the platform's first `ISimulator` implementation and its reference
oracle for differential testing. It is intentionally replaceable: everything
Verilator-specific — argument construction, log grammar, coverage file layout —
lives in this file and nowhere else.

Capability probing, not assumption: `capabilities()` reflects what *this
installed version* reports, so a 5.020 install and a 5.050 install do not claim
the same feature set.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from functools import lru_cache
from pathlib import Path

from ..core.errors import SimulatorError
from ..core.hashing import hash_sources, hash_text, short
from ..core.logging import get_logger
from ..core.platform import ExecContext, ExecHost, is_windows
from ..core.process import ProcessManager
from ..plugins.interfaces import FeatureStatus
from .base import (
    BuildRequest,
    BuildResult,
    RunRequest,
    RunResult,
    RunStatus,
    Simulator,
    WaveFormat,
)

# --- log grammar ---------------------------------------------------------
# UVM report summary counters, e.g. "UVM_ERROR :    3"
_UVM_COUNT_RE = re.compile(r"^\s*(UVM_(?:INFO|WARNING|ERROR|FATAL))\s*:\s*(\d+)\s*$", re.M)
# Inline UVM messages, e.g. "UVM_ERROR tb.sv(42) @ 100: uvm_test_top [TAG] msg"
_UVM_MSG_RE = re.compile(r"^(UVM_(?:ERROR|FATAL))\b\s*(.*)$", re.M)
_UVM_SUMMARY_RE = re.compile(r"--- UVM Report Summary ---")
_ASSERT_FAIL_RE = re.compile(
    r"(%Error:.*[Aa]ssert(?:ion)? failed|Assertion failed|\[%Error\].*assert)", re.M
)
_VERILATOR_ERROR_RE = re.compile(r"^%Error(-[A-Z0-9_]+)?:\s*(.*)$", re.M)
_VERILATOR_WARN_RE = re.compile(r"^%Warning(-[A-Z0-9_]+)?:\s*(.*)$", re.M)
_FINISH_RE = re.compile(r"- (?:\S+:\d+: )?Verilog \$finish|\$finish called|- V e r i l a t i o n")
_TIMEOUT_TOKENS = ("UVM_FATAL", "TIMEOUT", "watchdog")

# --- build failure signatures --------------------------------------------
# The kernel OOM killer surfaces through the compiler driver rather than as an
# obvious out-of-memory message, which sends people looking for a compiler bug.
_OOM_RE = re.compile(
    r"Killed signal terminated program|cc1plus.*out of memory|"
    r"virtual memory exhausted|std::bad_alloc",
    re.I,
)
_NOSPACE_RE = re.compile(r"No space left on device", re.I)
_MISSING_HDR_RE = re.compile(r"fatal error: ([\w./+-]+\.h[px]{0,2}): No such file")
_MISSING_CMD_RE = re.compile(r"make: (\S+): No such file or directory")


@lru_cache(maxsize=8)
def _probe_verilator(exe: str, launcher: tuple[str, ...]) -> tuple[str, str]:
    """Return (version_string, full --version output) for an executable.

    `launcher` is the exec-host prefix (empty for native, ("wsl.exe","--") or
    ("wsl.exe","-d","<distro>","--") when dispatching into WSL from Windows).
    """
    from subprocess import run

    argv = list(launcher) + [exe, "--version"]
    try:
        out = run(argv, capture_output=True, text=True, timeout=120)
    except Exception as exc:
        raise SimulatorError(f"could not probe {' '.join(argv)}: {exc}") from exc
    text = (out.stdout or out.stderr).strip()
    m = re.search(r"Verilator\s+([0-9]+\.[0-9]+)", text)
    return (m.group(1) if m else "unknown"), text


class VerilatorSimulator(Simulator):
    name = "verilator"

    def __init__(
        self,
        executable: str | None = None,
        jobs: int | None = None,
        exec_host: ExecHost | None = None,
        wsl_distro: str | None = None,
    ) -> None:
        self.executable = (
            executable
            or os.environ.get("UVMSTUDIO_VERILATOR")
            or shutil.which("verilator")
            or "verilator"
        )
        self.jobs = jobs or max(1, (os.cpu_count() or 2))
        self.proc = ProcessManager()
        self.log = get_logger()

        # Verilator has no supported native Windows build; on Windows the
        # backend dispatches into WSL and translates paths at the boundary.
        self.ctx = ExecContext.detect(exec_host)
        if wsl_distro:
            self.ctx.wsl_distro = wsl_distro
        if self.ctx.host is ExecHost.WSL:
            # PATH lookup happens inside the distro, not on the Windows host.
            self.executable = executable or os.environ.get(
                "UVMSTUDIO_VERILATOR", "verilator"
            )

    # -- exec-host plumbing ------------------------------------------------
    @property
    def _launcher(self) -> tuple[str, ...]:
        return tuple(self.ctx.wrap([])) if self.ctx.host is not ExecHost.NATIVE else ()

    def _argv(self, argv: list[str]) -> list[str]:
        return self.ctx.wrap(argv)

    def _p(self, path: Path | str) -> str:
        """Translate a host path into the execution host's namespace."""
        return self.ctx.path(path)

    # -- availability ------------------------------------------------------
    def is_available(self) -> bool:
        if self.ctx.host is ExecHost.WSL:
            try:
                _probe_verilator(self.executable, self._launcher)
                return True
            except SimulatorError:
                return False
        if is_windows():
            # Native Windows Verilator is not a supported configuration.
            return False
        return (
            shutil.which(self.executable) is not None
            or Path(self.executable).is_file()
        )

    def version(self) -> str:
        self.require_available()
        return _probe_verilator(self.executable, self._launcher)[0]

    def version_banner(self) -> str:
        self.require_available()
        return _probe_verilator(self.executable, self._launcher)[1]

    def exec_host(self) -> str:
        return self.ctx.describe()

    def capabilities(self) -> dict[str, FeatureStatus]:
        """Feature map derived from the *installed* version, not assumed.

        Verilator's class/randomisation/UVM support improved substantially
        across the 5.0x series; reporting a fixed map would be a false claim.
        """
        S, P, E, PL, U = (
            FeatureStatus.SUPPORTED,
            FeatureStatus.PARTIALLY_SUPPORTED,
            FeatureStatus.EXPERIMENTAL,
            FeatureStatus.PLANNED,
            FeatureStatus.UNSUPPORTED,
        )
        if not self.is_available():
            return {"backend_installed": U}

        try:
            ver = float(self.version())
        except ValueError:
            ver = 0.0

        caps: dict[str, FeatureStatus] = {
            "backend_installed": S,
            "rtl_synthesizable_subset": S,
            "timing_delays": S,          # --timing, 5.006+
            "vcd_waves": S,
            "fst_waves": S,
            "line_coverage": S,
            "toggle_coverage": S,
            "dpi_c": S,
            "four_state_x_prop": P,      # 2-state by default; --x-assign only
            "classes": P,
            "randomize": P,
            "constraints": P,
            "covergroups": P,
            "sva_concurrent": P,
            "uvm_1_2": P,
            "uvm_ieee_1800_2": P,
            "vpi": P,
        }
        # 5.03x+ materially improved class/constraint/covergroup handling and
        # added the external SMT solver path used by randomize().
        if ver >= 5.036:
            caps.update({"classes": S, "randomize": P, "constraints": P,
                         "covergroups": P, "uvm_1_2": E})
        if ver >= 5.048:
            caps.update({"uvm_1_2": E, "uvm_ieee_1800_2": E})
        return caps

    def solver_available(self) -> bool:
        """Verilator delegates `randomize()` constraints to an external SMT solver."""
        return shutil.which("z3") is not None

    # -- build -------------------------------------------------------------
    def build(self, request: BuildRequest) -> BuildResult:
        self.require_available()
        t0 = time.monotonic()
        build_dir = request.build_dir
        build_dir.mkdir(parents=True, exist_ok=True)
        obj_dir = build_dir / "obj_dir"
        binary = obj_dir / request.binary_name

        # -- compile cache: identical inputs => reuse the image -------------
        key = self._cache_key(request)
        stamp = build_dir / "build_stamp.json"
        if binary.exists() and stamp.exists():
            try:
                prev = json.loads(stamp.read_text())
                if prev.get("key") == key:
                    self.log.info("build cache hit", key=short(key), binary=str(binary))
                    return BuildResult(
                        ok=True,
                        binary=binary,
                        log=(build_dir / "build.log").read_text(errors="replace")
                        if (build_dir / "build.log").exists()
                        else "",
                        duration_s=0.0,
                        command=prev.get("command", []),
                        backend=self.name,
                        backend_version=self.version(),
                        cached=True,
                        status=RunStatus.PASS,
                    )
            except Exception:
                pass

        lowmem = self.low_memory_mode()
        argv = self._argv(self._build_argv(request, obj_dir, lowmem=lowmem))
        res = self.proc.run(
            argv,
            cwd=build_dir,
            timeout_s=request.timeout_s,
            log_path=build_dir / "build.log",
        )
        combined_log = res.combined()

        if lowmem and res.ok:
            # Phase 2: drive make ourselves so the PCH can be stubbed out.
            # Measured on UVM 2020.3.1 (91 TUs, -O0, stub PCH, -j1): peak
            # cc1plus 641 MB — and the full build passes inside a hard 1 GB
            # cgroup with swap off. -Os + real PCH cannot get there: the PCH
            # compile alone costs 940 MB.
            stub = obj_dir / "uvmstudio_pch_stub.h"
            stub.write_text("// intentionally empty: low-memory stub PCH\n")
            mk = self.proc.run(
                self._argv([
                    "make", "-C", self._p(obj_dir),
                    "-f", f"V{request.top}.mk", "-j", "1",
                    "OPT_FAST=-O0", "OPT_SLOW=-O0", "OPT_GLOBAL=-O0",
                    "VK_PCH_I_FAST=", "VK_PCH_I_SLOW=",
                    f"VK_PCH_H={stub.name}",
                ]),
                cwd=build_dir,
                timeout_s=request.timeout_s,
                log_path=build_dir / "build.log",
            )
            combined_log += "\n" + mk.combined()
            res = mk

        ok = res.ok and binary.exists()
        if ok:
            stamp.write_text(json.dumps({"key": key, "command": argv}, indent=2))

        return BuildResult(
            ok=ok,
            binary=binary if ok else None,
            log=combined_log,
            duration_s=time.monotonic() - t0,
            command=argv,
            backend=self.name,
            backend_version=self.version(),
            cached=False,
            status=RunStatus.PASS if ok else RunStatus.BLOCKED,
            reasons=[] if ok else self.classify_build_failure(combined_log),
        )

    # -- compile memory ----------------------------------------------------
    # Verilator concatenates its ~2500 generated .cpp files into a small number
    # of aggregate translation units, and *the bucket count equals
    # `--build-jobs`*. That is a trap: lowering -j to "save memory" produces
    # FEWER, LARGER translation units and makes the peak worse. Splitting is
    # therefore decoupled from make parallelism here.
    #
    # Measured on this container (2 cores, gcc, Accellera UVM 2020.3.1 + a full
    # APB agent stack, -Os, no ccache), peak RSS of the largest single cc1plus:
    #
    #   --build-jobs 2  ->  4 buckets  ->  2620 MB   C++ build 350.5 s
    #   --build-jobs 16 -> 31 buckets  ->  1096 MB   C++ build 326.9 s
    #
    # 2.4x less memory and slightly faster, so the split is not a trade-off.
    # The floor is the precompiled header: building Vtb_top__pch.h.fast.gch
    # alone peaks near 940 MB and every unit maps the resulting ~312 MB .gch.
    # No amount of splitting goes below that, which is why UVM cannot build in
    # a 1 GB container however it is tuned.
    UVM_COMPILE_PEAK_RSS_MB = 1100        # tuned split, measured
    UVM_COMPILE_PCH_FLOOR_MB = 940        # PCH build, measured
    DEFAULT_COMPILE_SPLIT = 16

    LOW_MEMORY_SPLIT = 48          # ~91 aggregate TUs on UVM 2020.3.1
    LOW_MEMORY_BUDGET_MB = 2000    # below this, -Os + PCH cannot fit
    SERIAL_BUDGET_MB = 3500        # below this, force make -j1

    @staticmethod
    def memory_budget_mb() -> int | None:
        """Effective memory ceiling: min(cgroup limit, physical RAM)."""
        candidates: list[int] = []
        for path in ("/sys/fs/cgroup/memory.max",
                     "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            try:
                raw = Path(path).read_text().strip()
                if raw.isdigit():
                    candidates.append(int(raw) // (1024 * 1024))
            except OSError:
                pass
        try:
            total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
            candidates.append(total // (1024 * 1024))
        except (ValueError, OSError):
            pass
        return min(candidates) if candidates else None

    def low_memory_mode(self) -> bool:
        env = os.environ.get("UVMSTUDIO_LOW_MEMORY")
        if env is not None:
            return env not in ("0", "false", "")
        budget = self.memory_budget_mb()
        return budget is not None and budget < self.LOW_MEMORY_BUDGET_MB

    def compile_split(self, request: BuildRequest) -> int:
        """Number of aggregate C++ translation units to generate.

        Deliberately *not* tied to `jobs`. Splitting controls peak memory;
        `jobs` controls how many of those units compile at once.
        """
        if request.compile_split is not None:
            return max(1, request.compile_split)
        env = os.environ.get("UVMSTUDIO_COMPILE_SPLIT")
        if env and env.isdigit() and int(env) > 0:
            return int(env)
        return self.DEFAULT_COMPILE_SPLIT

    # -- build failure classification --------------------------------------

    @classmethod
    def classify_build_failure(cls, log: str) -> list[str]:
        """Turn a raw build log into named causes.

        The OOM case matters most: the kernel kills cc1plus and g++ reports
        "Killed signal terminated program cc1plus", which reads like a compiler
        bug. It is a container memory limit, and the fix is a bigger container.
        A raw compiler message is not a diagnosis.
        """
        reasons: list[str] = []

        if _OOM_RE.search(log):
            reasons.append(
                "compiler killed by the OOM killer - the container ran out of "
                "memory. Measured on UVM 2020.3.1: normal mode (-Os, PCH) "
                f"peaks at {cls.UVM_COMPILE_PEAK_RSS_MB} MB per cc1plus and "
                f"needs about 2 GB; low-memory mode (-O0, no PCH, "
                f"{cls.LOW_MEMORY_SPLIT}-way split) peaks at 641 MB and builds "
                "inside a hard 1 GB cgroup. Set UVMSTUDIO_LOW_MEMORY=1 to "
                "force it - it should have engaged automatically if the "
                "container limit was detectable. Cost: ~5x slower simulation "
                "(-O0) and ~2.2x longer C++ compile."
            )
        if _NOSPACE_RE.search(log):
            reasons.append("build ran out of disk space")
        for m in _MISSING_HDR_RE.finditer(log):
            reasons.append(
                f"missing header {m.group(1)!r} - the image needs the matching "
                f"-dev package (Verilator compiles verilated*.cpp at build time)"
            )
        for m in _MISSING_CMD_RE.finditer(log):
            reasons.append(f"missing build tool {m.group(1)!r} (exit 127)")

        if not reasons:
            errs = _VERILATOR_ERROR_RE.findall(log)
            reasons.append(
                f"verilator reported {len(errs)} error(s)" if errs
                else "build failed - see build.log"
            )
        return reasons

    def _cache_key(self, request: BuildRequest) -> str:
        parts = list(request.cache_key_parts())
        parts.append(f"verilator={self.version()}")
        parts.append(f"lowmem={self.low_memory_mode()}")
        try:
            parts.append("srcs=" + hash_sources(request.files))
        except Exception:
            pass
        return hash_text("\n".join(parts))

    def _build_argv(self, request: BuildRequest, obj_dir: Path,
                    lowmem: bool = False) -> list[str]:
        if lowmem:
            # generate only; build() runs make itself with the stub PCH
            argv = [self.executable, "--main", "--exe", "--timing", "--sv"]
        else:
            argv = [self.executable, "--binary", "--sv"]
        argv += ["--Mdir", self._p(obj_dir), "-o", request.binary_name]
        argv += ["--top-module", request.top]

        # Two different knobs that Verilator conflates behind `-j`:
        #   --build-jobs  decides how many C++ translation units are generated
        #   -MAKEFLAGS -j decides how many of them compile at once
        # Passing a single `-j` ties them together and makes a low-parallelism
        # build allocate the most memory. Split them.
        budget = self.memory_budget_mb()
        make_jobs = min(self.jobs, max(1, request.threads or self.jobs))
        if budget is not None and budget < self.SERIAL_BUDGET_MB:
            make_jobs = 1              # each -Os cc1plus peaks ~1.1 GB
        split = (self.LOW_MEMORY_SPLIT if lowmem
                 else self.compile_split(request))
        argv += ["--verilate-jobs", str(min(self.jobs, 4))]
        argv += ["--build-jobs", str(split)]
        if not lowmem:
            argv += ["-MAKEFLAGS", f"-j{make_jobs}"]

        if request.timescale:
            argv += ["--timescale", request.timescale]
        if request.timing:
            argv.append("--timing")
        if request.coverage:
            argv.append("--coverage")
        if request.waves is WaveFormat.FST:
            argv += ["--trace-fst", "--trace-structs"]
        elif request.waves is WaveFormat.VCD:
            argv += ["--trace", "--trace-structs"]

        # Verilator is strict by default; these are style-level lints that would
        # otherwise mask real errors in third-party VIP. They are *warnings*
        # being demoted, never errors being hidden.
        for w in (
            "WIDTHTRUNC", "WIDTHEXPAND", "UNOPTFLAT", "DECLFILENAME",
            "UNUSEDSIGNAL", "UNUSEDPARAM", "VARHIDDEN", "CASEINCOMPLETE",
            "SYNCASYNCNET", "MULTIDRIVEN", "PINCONNECTEMPTY", "IMPLICITSTATIC",
        ):
            argv += [f"-Wno-{w}"]

        for inc in request.include_dirs:
            argv.append(f"+incdir+{self._p(inc)}")
        for d in request.defines:
            argv.append(f"+define+{d}")
        if request.uvm_home is not None:
            argv.append(f"+incdir+{self._p(request.uvm_home)}")

        argv += request.extra_args
        argv += [self._p(f) for f in request.files]
        return argv

    # -- run ---------------------------------------------------------------
    def run(self, request: RunRequest) -> RunResult:
        self.require_available()
        t0 = time.monotonic()
        run_dir = request.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)

        if not request.binary.exists():
            return RunResult(
                status=RunStatus.BLOCKED,
                seed=request.seed,
                returncode=-1,
                duration_s=0.0,
                reasons=[f"simulation binary not found: {request.binary}"],
                backend=self.name,
            )

        argv = [self._p(request.binary)]
        argv.append(f"+ntb_random_seed={request.seed}")
        argv.append(f"+verilator+seed+{request.seed}")
        if request.uvm_testname:
            argv.append(f"+UVM_TESTNAME={request.uvm_testname}")
            argv.append(f"+UVM_VERBOSITY={request.verbosity}")
        if request.waves is not WaveFormat.NONE:
            argv.append("+DUMP_WAVES=1")
            argv.append(f"+WAVE_FILE=waves.{request.waves.value}")
        argv += request.plusargs

        log_path = run_dir / "sim.log"
        res = self.proc.run(
            self._argv(argv),
            cwd=run_dir,
            timeout_s=request.timeout_s,
            log_path=log_path,
        )

        wave = self._find_wave(run_dir, request.waves)
        cov = run_dir / "coverage.dat"
        result = self._classify(
            text=res.combined(),
            returncode=res.returncode,
            timed_out=res.timed_out,
            request=request,
        )
        result.duration_s = time.monotonic() - t0
        result.log_path = log_path
        result.wave_path = wave
        result.coverage_path = cov if cov.exists() else None
        result.stdout_tail = res.stdout[-4000:]
        result.backend = self.name
        result.backend_version = self.version()
        result.command = argv
        return result

    @staticmethod
    def _find_wave(run_dir: Path, fmt: WaveFormat) -> Path | None:
        if fmt is WaveFormat.NONE:
            return None
        for pattern in (f"*.{fmt.value}", "*.fst", "*.vcd"):
            hits = sorted(run_dir.glob(pattern))
            if hits:
                return hits[0]
        return None

    # -- result classification --------------------------------------------
    def _classify(
        self, *, text: str, returncode: int, timed_out: bool, request: RunRequest
    ) -> RunResult:
        """Turn raw simulator output into a status backed by named evidence.

        Every status carries the reason(s) that produced it. A run whose output
        contains no recognisable evidence is NOT_VERIFIED — never PASS.
        """
        reasons: list[str] = []
        counters: dict[str, int] = {}

        for m in _UVM_COUNT_RE.finditer(text):
            counters[m.group(1)] = int(m.group(2))

        uvm_ran = bool(_UVM_SUMMARY_RE.search(text))
        n_err = counters.get("UVM_ERROR", 0)
        n_fatal = counters.get("UVM_FATAL", 0)
        inline_fatal = [m.group(0)[:200] for m in _UVM_MSG_RE.finditer(text)]
        vl_errors = [m.group(2)[:200] for m in _VERILATOR_ERROR_RE.finditer(text)]
        assert_fail = bool(_ASSERT_FAIL_RE.search(text))
        finished = bool(_FINISH_RE.search(text))

        counters["verilator_errors"] = len(vl_errors)

        if timed_out:
            return RunResult(
                status=RunStatus.FAIL,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=[f"simulation timed out after {request.timeout_s}s"],
                failure_signature=f"TIMEOUT:{request.uvm_testname or 'sim'}",
                counters=counters,
                timed_out=True,
            )

        # --- observed failures -------------------------------------------
        if n_fatal:
            reasons.append(f"UVM_FATAL count = {n_fatal}")
        if n_err:
            reasons.append(f"UVM_ERROR count = {n_err}")
        if not uvm_ran and inline_fatal:
            reasons.append(f"{len(inline_fatal)} inline UVM_ERROR/UVM_FATAL message(s)")
        if vl_errors:
            reasons.append(f"simulator reported {len(vl_errors)} %Error(s)")
        if assert_fail:
            reasons.append("assertion failure observed")
        if returncode != 0:
            reasons.append(f"non-zero exit code {returncode}")

        failed = bool(reasons)

        # --- negative tests: a detected violation IS the pass criterion ----
        if request.expect == "FAIL":
            if failed:
                return RunResult(
                    status=RunStatus.PASS,
                    seed=request.seed,
                    returncode=returncode,
                    duration_s=0.0,
                    reasons=["negative test: expected violation was DETECTED"] + reasons,
                    counters=counters,
                )
            return RunResult(
                status=RunStatus.FAIL,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=["negative test: expected violation was NOT detected"],
                failure_signature=f"NEG_NOT_DETECTED:{request.uvm_testname or 'sim'}",
                counters=counters,
            )

        if failed:
            return RunResult(
                status=RunStatus.FAIL,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=reasons,
                failure_signature=self.failure_signature(text, vl_errors, inline_fatal),
                counters=counters,
            )

        # --- positive evidence required for PASS --------------------------
        # Red-team RT-P-002: a UVM summary alone is not enough. A summary
        # with no $finish means the run printed its report but never reached
        # orderly completion — that is NOT_VERIFIED, not PASS.
        if uvm_ran and finished and n_err == 0 and n_fatal == 0 \
                and returncode == 0:
            return RunResult(
                status=RunStatus.PASS,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=["UVM report summary present with 0 UVM_ERROR / "
                         "0 UVM_FATAL and $finish reached"],
                counters=counters,
            )
        if uvm_ran and not finished and n_err == 0 and n_fatal == 0 \
                and returncode == 0:
            return RunResult(
                status=RunStatus.NOT_VERIFIED,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=["UVM summary is clean but $finish was never "
                         "observed - the run did not complete in an orderly "
                         "way, so the summary proves nothing (RT-P-002)"],
                counters=counters,
            )
        if finished and returncode == 0:
            return RunResult(
                status=RunStatus.PASS,
                seed=request.seed,
                returncode=returncode,
                duration_s=0.0,
                reasons=["simulation reached $finish with exit code 0"],
                counters=counters,
            )

        return RunResult(
            status=RunStatus.NOT_VERIFIED,
            seed=request.seed,
            returncode=returncode,
            duration_s=0.0,
            reasons=[
                "no pass evidence in simulator output: "
                "no UVM report summary and no $finish observed"
            ],
            counters=counters,
        )

    @staticmethod
    def failure_signature(text: str, vl_errors: list[str], uvm_msgs: list[str]) -> str:
        """Stable key for clustering identical failures across seeds.

        Numbers, hex literals, times and paths are normalised out so that the
        same defect hit at a different time or address clusters together.
        """
        candidates = uvm_msgs[:1] or vl_errors[:1]
        if not candidates:
            tail = [l for l in text.strip().splitlines()[-20:] if l.strip()]
            candidates = tail[-1:] if tail else ["unknown"]
        sig = candidates[0]
        sig = re.sub(r"0x[0-9a-fA-F]+", "<HEX>", sig)
        sig = re.sub(r"@\s*\d+", "@<TIME>", sig)
        sig = re.sub(r"\b\d+\b", "<N>", sig)
        sig = re.sub(r"[/\w.-]+\.(sv|svh|v|cpp|h)\(?\d*\)?", "<FILE>", sig)
        sig = re.sub(r"\s+", " ", sig).strip()
        return sig[:200]
