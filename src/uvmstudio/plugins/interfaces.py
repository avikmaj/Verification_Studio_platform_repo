"""Stable plugin boundaries for UVM Verification Studio.

These Protocols are the contract that keeps the platform backend-independent.
Rules that follow from them:

  * The IDE, regression engine, coverage DB and transaction DB import from this
    module — never from a concrete backend.
  * A concrete backend (Verilator, VCS, a native kernel) imports the platform,
    not the other way round.
  * Adding a backend must never require editing a caller.

`FeatureStatus` is deliberately part of the interface. Every implementation is
required to publish a capability map, so the platform can refuse to claim
support it has not demonstrated.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable


class FeatureStatus(str, Enum):
    """The only four legal answers to 'do you support X?'."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    EXPERIMENTAL = "EXPERIMENTAL"
    PLANNED = "PLANNED"
    UNSUPPORTED = "UNSUPPORTED"


class Capability(Protocol):
    def capabilities(self) -> dict[str, FeatureStatus]:
        """Feature name -> status. Must reflect what is actually implemented."""
        ...


@runtime_checkable
class ISVFrontend(Capability, Protocol):
    """SystemVerilog preprocess / parse / elaborate provider."""

    name: str
    version: str

    def compile(self, request: Any) -> Any: ...
    def is_available(self) -> bool: ...


@runtime_checkable
class ISimulator(Capability, Protocol):
    """Simulation backend: build an executable, then run it with a seed."""

    name: str

    def is_available(self) -> bool: ...
    def version(self) -> str: ...
    def build(self, request: Any) -> Any: ...
    def run(self, request: Any) -> Any: ...


@runtime_checkable
class IWaveformReader(Capability, Protocol):
    def open(self, path: Path) -> Any: ...
    def hierarchy(self) -> Any: ...
    def signal_values(self, handle: Any, t0: int, t1: int) -> Iterable[tuple[int, Any]]: ...


@runtime_checkable
class ICoverageEngine(Capability, Protocol):
    def load(self, path: Path) -> Any: ...
    def merge(self, dbs: list[Any]) -> Any: ...
    def report(self, db: Any) -> dict: ...


@runtime_checkable
class ILintEngine(Capability, Protocol):
    def check(self, design: Any) -> list[Any]: ...


@runtime_checkable
class IConstraintSolver(Capability, Protocol):
    def solve(self, problem: Any, seed: int) -> Any: ...


@runtime_checkable
class IRegressionEngine(Protocol):
    def submit(self, jobs: list[Any]) -> Any: ...
    def wait(self, handle: Any) -> Any: ...


@runtime_checkable
class ITransactionDatabase(Protocol):
    def record(self, txn: Any) -> int: ...
    def query(self, **filters: Any) -> Iterable[Any]: ...


@runtime_checkable
class ICIBackend(Protocol):
    name: str

    def generate(self, project: Any, out_dir: Path) -> list[Path]: ...


__all__ = [
    "FeatureStatus",
    "Capability",
    "ISVFrontend",
    "ISimulator",
    "IWaveformReader",
    "ICoverageEngine",
    "ILintEngine",
    "IConstraintSolver",
    "IRegressionEngine",
    "ITransactionDatabase",
    "ICIBackend",
]
