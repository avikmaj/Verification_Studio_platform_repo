"""ISimulator — the boundary that keeps the platform backend-independent.

Nothing above this module knows which simulator ran. A backend is a class that
implements `Simulator`; adding VCS, Questa, Xcelium or a native kernel means
adding a class and registering it, never editing a caller.

Status discipline (non-negotiable):
  PASS          simulator executed AND the run satisfied every pass criterion
  FAIL          simulator executed AND a failure was observed
  NOT_VERIFIED  no simulator evidence, or evidence insufficient to judge
  BLOCKED       could not get as far as running (build/tool/environment)
  ERROR         infrastructure fault in the platform itself

NOT_VERIFIED is never promoted to PASS. Absence of an error message is not
evidence of success.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..core.errors import BackendUnavailable, SimulatorError
from ..plugins.interfaces import FeatureStatus


class RunStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"

    @property
    def is_conclusive(self) -> bool:
        return self in (RunStatus.PASS, RunStatus.FAIL)


class WaveFormat(str, Enum):
    NONE = "none"
    VCD = "vcd"
    FST = "fst"


@dataclass
class BuildRequest:
    """Everything needed to produce a runnable simulation image."""

    files: list[Path]
    top: str
    build_dir: Path
    include_dirs: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    language_standard: str = "1800-2017"
    timescale: str | None = None
    coverage: bool = True
    waves: WaveFormat = WaveFormat.FST
    timing: bool = True
    threads: int = 1
    timeout_s: float = 3600.0
    extra_args: list[str] = field(default_factory=list)
    uvm_home: Path | None = None
    binary_name: str = "simv"

    def cache_key_parts(self) -> list[str]:
        return [
            *(str(f) for f in self.files),
            *(f"+incdir+{d}" for d in self.include_dirs),
            *(f"+define+{d}" for d in self.defines),
            f"top={self.top}",
            f"std={self.language_standard}",
            f"timescale={self.timescale}",
            f"cov={self.coverage}",
            f"waves={self.waves.value}",
            f"timing={self.timing}",
            f"uvm={self.uvm_home}",
            *self.extra_args,
        ]


@dataclass
class BuildResult:
    ok: bool
    binary: Path | None
    log: str
    duration_s: float
    command: list[str] = field(default_factory=list)
    backend: str = ""
    backend_version: str = ""
    cached: bool = False
    diagnostics: Any = None
    status: RunStatus = RunStatus.NOT_VERIFIED

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "binary": str(self.binary) if self.binary else None,
            "duration_s": round(self.duration_s, 4),
            "command": self.command,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "cached": self.cached,
            "status": self.status.value,
        }


@dataclass
class RunRequest:
    binary: Path
    run_dir: Path
    seed: int = 1
    uvm_testname: str | None = None
    verbosity: str = "UVM_MEDIUM"
    plusargs: list[str] = field(default_factory=list)
    waves: WaveFormat = WaveFormat.NONE
    coverage: bool = True
    timeout_s: float = 900.0
    expect: str = "PASS"          # "FAIL" => negative test, violation expected


@dataclass
class RunResult:
    status: RunStatus
    seed: int
    returncode: int
    duration_s: float
    log_path: Path | None = None
    wave_path: Path | None = None
    coverage_path: Path | None = None
    stdout_tail: str = ""
    failure_signature: str = ""
    reasons: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    timed_out: bool = False
    backend: str = ""
    backend_version: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        for k in ("log_path", "wave_path", "coverage_path"):
            d[k] = str(d[k]) if d[k] else None
        d["duration_s"] = round(self.duration_s, 4)
        return d


class Simulator(abc.ABC):
    """Base class for every simulation backend."""

    name: str = "abstract"

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    @abc.abstractmethod
    def version(self) -> str: ...

    @abc.abstractmethod
    def build(self, request: BuildRequest) -> BuildResult: ...

    @abc.abstractmethod
    def run(self, request: RunRequest) -> RunResult: ...

    @abc.abstractmethod
    def capabilities(self) -> dict[str, FeatureStatus]: ...

    def require_available(self) -> None:
        if not self.is_available():
            raise BackendUnavailable(
                f"simulator backend {self.name!r} is not installed or not on PATH"
            )


_REGISTRY: dict[str, Callable[..., Simulator]] = {}


def register_simulator(name: str, factory: Callable[..., Simulator]) -> None:
    _REGISTRY[name] = factory


def registered_simulators() -> list[str]:
    return sorted(_REGISTRY)


def available_simulators() -> list[str]:
    out = []
    for name, factory in _REGISTRY.items():
        try:
            if factory().is_available():
                out.append(name)
        except Exception:
            continue
    return out


def get_simulator(name: str, **kwargs: Any) -> Simulator:
    if name not in _REGISTRY:
        raise SimulatorError(
            f"unknown simulator backend {name!r}; registered: {registered_simulators()}"
        )
    return _REGISTRY[name](**kwargs)


def _install_builtin_backends() -> None:
    from .verilator import VerilatorSimulator

    register_simulator("verilator", VerilatorSimulator)

    # Registers itself on import (ExecHost.REMOTE).
    from . import remote  # noqa: F401


_install_builtin_backends()
