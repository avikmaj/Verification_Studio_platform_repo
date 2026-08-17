"""Frontend abstraction: request/result types plus a registry.

Callers construct a `CompileRequest` and get a `CompileResult`. They never
import a concrete frontend. Adding Surelog/UHDM later means registering another
implementation, not editing any caller.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.errors import FrontendError
from ..plugins.interfaces import FeatureStatus
from .diagnostics import DiagnosticBag
from .ir import Design


@dataclass
class CompileRequest:
    files: list[Path]
    include_dirs: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)          # "NAME" or "NAME=value"
    top: str | None = None
    language_standard: str = "1800-2017"
    # Default timescale applied to design elements that declare none.
    # Required for mixed libraries: IEEE 1800 makes it an error for some
    # elements to have a timescale while others do not, and third-party VIP
    # (including Accellera UVM) frequently omits it.
    timescale: str | None = None            # e.g. "1ns/1ps"
    library_files: list[Path] = field(default_factory=list)
    max_errors: int = 100
    build_ir: bool = True
    # Warnings to suppress by slang/tool code. UVM sources trip several
    # legitimate-but-noisy warnings; suppressing them is a project decision,
    # never a silent default.
    suppress_warnings: list[str] = field(default_factory=list)
    timeout_s: float = 600.0

    def cache_key_parts(self) -> list[str]:
        return [
            *(str(f) for f in self.files),
            *(f"+incdir+{d}" for d in self.include_dirs),
            *(f"+define+{d}" for d in self.defines),
            f"std={self.language_standard}",
            f"top={self.top or ''}",
        ]


@dataclass
class CompileResult:
    ok: bool
    diagnostics: DiagnosticBag
    design: Design | None = None
    frontend: str = ""
    frontend_version: str = ""
    duration_s: float = 0.0

    @property
    def error_count(self) -> int:
        return len(self.diagnostics.errors)

    @property
    def warning_count(self) -> int:
        return len(self.diagnostics.warnings)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "frontend": self.frontend,
            "frontend_version": self.frontend_version,
            "duration_s": round(self.duration_s, 4),
            "errors": self.error_count,
            "warnings": self.warning_count,
            "diagnostics": self.diagnostics.to_dict(),
            "stats": self.design.stats() if self.design else None,
        }


class SVFrontend(abc.ABC):
    """Base class every SystemVerilog frontend implements."""

    name: str = "abstract"
    version: str = "0"

    @abc.abstractmethod
    def is_available(self) -> bool: ...

    @abc.abstractmethod
    def compile(self, request: CompileRequest) -> CompileResult: ...

    @abc.abstractmethod
    def capabilities(self) -> dict[str, FeatureStatus]: ...

    def preprocess(self, request: CompileRequest) -> str:
        raise NotImplementedError(f"{self.name} does not implement preprocess()")


_REGISTRY: dict[str, Callable[[], SVFrontend]] = {}


def register_frontend(name: str, factory: Callable[[], SVFrontend]) -> None:
    _REGISTRY[name] = factory


def available_frontends() -> list[str]:
    out = []
    for name, factory in _REGISTRY.items():
        try:
            if factory().is_available():
                out.append(name)
        except Exception:
            continue
    return out


def get_frontend(name: str = "slang") -> SVFrontend:
    if name not in _REGISTRY:
        raise FrontendError(
            f"unknown frontend {name!r}; registered: {sorted(_REGISTRY)}"
        )
    fe = _REGISTRY[name]()
    if not fe.is_available():
        raise FrontendError(
            f"frontend {name!r} is registered but not available on this machine"
        )
    return fe


def _install_builtin_frontends() -> None:
    from .slang_frontend import SlangFrontend

    register_frontend("slang", SlangFrontend)


_install_builtin_frontends()
