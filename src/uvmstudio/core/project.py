"""Project model — the single source of truth for what gets compiled and run.

A project is a YAML file (`uvmstudio.yaml`) at the root of a verification
workspace. Everything downstream (frontend, simulator backends, regression,
reproducibility metadata) reads this model and nothing else. There is no
implicit file discovery, because implicit discovery is not reproducible.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

from .errors import ProjectError

PROJECT_FILENAME = "uvmstudio.yaml"

# Language standards the frontend can be asked to enforce.
LANGUAGE_STANDARDS = ("1800-2017", "1800-2023")

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: str) -> str:
    """Expand ${VAR} against the environment. Missing vars are an error, not ''.

    Silently expanding an unset variable to an empty string is how build systems
    produce runs that cannot be reproduced.
    """

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise ProjectError(
                f"environment variable ${{{name}}} referenced in project file is not set"
            )
        return os.environ[name]

    return _ENV_RE.sub(repl, value)


@dataclass
class TestSpec:
    """One entry in the project's test database."""

    name: str
    uvm_testname: str | None = None
    top: str | None = None
    tier: str = "L1"
    seeds: int = 1
    plusargs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    timeout_s: float = 900.0
    tags: list[str] = field(default_factory=list)
    expect: str = "PASS"  # PASS | FAIL  (FAIL = negative test, violation expected)

    def __post_init__(self) -> None:
        if self.expect not in ("PASS", "FAIL"):
            raise ProjectError(f"test {self.name}: expect must be PASS or FAIL")
        if self.seeds < 1:
            raise ProjectError(f"test {self.name}: seeds must be >= 1")
        if self.uvm_testname is None:
            self.uvm_testname = self.name


@dataclass
class FileSet:
    """Ordered source list plus the include/define context it compiles under."""

    name: str
    files: list[str] = field(default_factory=list)
    include_dirs: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str
    root: Path
    top: str
    language_standard: str = "1800-2017"
    timescale: str | None = None   # default for elements declaring none
    uvm_version: str | None = None          # e.g. "1.2", "2020-3.1", or None
    uvm_home: str | None = None             # path to UVM src/ (overrides $UVM_HOME)
    # When a UVM home resolves, compile uvm_pkg.sv ahead of user sources.
    # Set false only if the fileset lists the UVM sources itself.
    include_uvm_sources: bool = True
    filesets: list[FileSet] = field(default_factory=list)
    tests: list[TestSpec] = field(default_factory=list)
    default_backend: str = "verilator"
    backend_options: dict[str, Any] = field(default_factory=dict)
    coverage: bool = True
    waves: str = "on_fail"                  # always | on_fail | never
    build_dir: str = "build"
    results_dir: str = "results"

    # -- construction -----------------------------------------------------
    @staticmethod
    def load(path: Path | str) -> "Project":
        path = Path(path)
        if path.is_dir():
            path = path / PROJECT_FILENAME
        if not path.exists():
            raise ProjectError(f"no project file at {path}")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ProjectError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ProjectError(f"{path}: top level must be a mapping")
        return Project.from_dict(raw, root=path.parent.resolve())

    @staticmethod
    def from_dict(raw: dict, *, root: Path) -> "Project":
        for key in ("name", "top"):
            if key not in raw:
                raise ProjectError(f"project file missing required key '{key}'")

        std = raw.get("language_standard", "1800-2017")
        if std not in LANGUAGE_STANDARDS:
            raise ProjectError(
                f"language_standard must be one of {LANGUAGE_STANDARDS}, got {std!r}"
            )

        waves = raw.get("waves", "on_fail")
        if waves not in ("always", "on_fail", "never"):
            raise ProjectError("waves must be one of: always, on_fail, never")

        filesets: list[FileSet] = []
        fs_raw = raw.get("filesets") or []
        if isinstance(fs_raw, dict):  # allow mapping form
            fs_raw = [{"name": k, **(v or {})} for k, v in fs_raw.items()]
        for entry in fs_raw:
            filesets.append(
                FileSet(
                    name=entry["name"],
                    files=[_expand(f) for f in entry.get("files", [])],
                    include_dirs=[_expand(d) for d in entry.get("include_dirs", [])],
                    defines=list(entry.get("defines", [])),
                )
            )
        if not filesets:
            raise ProjectError("project defines no filesets — nothing to compile")

        tests: list[TestSpec] = []
        for entry in raw.get("tests") or []:
            if isinstance(entry, str):
                tests.append(TestSpec(name=entry))
            else:
                tests.append(TestSpec(**entry))

        uvm_home = raw.get("uvm_home")
        if uvm_home:
            uvm_home = _expand(uvm_home)

        return Project(
            name=raw["name"],
            root=root,
            top=raw["top"],
            language_standard=std,
            timescale=raw.get("timescale"),
            uvm_version=raw.get("uvm_version"),
            uvm_home=uvm_home,
            include_uvm_sources=bool(raw.get("include_uvm_sources", True)),
            filesets=filesets,
            tests=tests,
            default_backend=raw.get("default_backend", "verilator"),
            backend_options=raw.get("backend_options") or {},
            coverage=bool(raw.get("coverage", True)),
            waves=waves,
            build_dir=raw.get("build_dir", "build"),
            results_dir=raw.get("results_dir", "results"),
        )

    # -- resolution -------------------------------------------------------
    def _resolve(self, item: str) -> Path:
        p = Path(item)
        return p if p.is_absolute() else (self.root / p)

    def source_files(self, *, filesets: list[str] | None = None) -> list[Path]:
        """Ordered, de-duplicated, glob-expanded source list.

        Order matters in SystemVerilog (package-before-use), so this preserves
        declaration order and only removes exact duplicates.

        When a UVM home resolves, `uvm_pkg.sv` is prepended automatically: a
        testbench that imports `uvm_pkg` cannot elaborate unless the library is
        part of the same compilation. Making the user list it in every fileset
        is the kind of boilerplate that silently rots.
        """
        out: list[Path] = []
        seen: set[Path] = set()

        uvm_home = self.resolved_uvm_home()
        if self.include_uvm_sources and uvm_home is not None:
            pkg = uvm_home / "uvm_pkg.sv"
            if not pkg.exists():
                raise ProjectError(
                    f"uvm_home={uvm_home} does not contain uvm_pkg.sv; "
                    f"point it at the Accellera 'src' directory"
                )
            out.append(pkg.resolve())
            seen.add(pkg.resolve())
        for fs in self.filesets:
            if filesets and fs.name not in filesets:
                continue
            for entry in fs.files:
                base = self._resolve(entry)
                if any(ch in entry for ch in "*?["):
                    matches = sorted(self.root.glob(entry))
                    if not matches:
                        raise ProjectError(
                            f"fileset '{fs.name}': glob {entry!r} matched no files"
                        )
                    candidates = matches
                else:
                    if not base.exists():
                        raise ProjectError(
                            f"fileset '{fs.name}': source file not found: {base}"
                        )
                    candidates = [base]
                for c in candidates:
                    rc = c.resolve()
                    if rc not in seen:
                        seen.add(rc)
                        out.append(rc)
        if not out:
            raise ProjectError("resolved source list is empty")
        return out

    def include_dirs(self, *, filesets: list[str] | None = None) -> list[Path]:
        out: list[Path] = []
        seen: set[Path] = set()
        for fs in self.filesets:
            if filesets and fs.name not in filesets:
                continue
            for d in fs.include_dirs:
                p = self._resolve(d).resolve()
                if not p.is_dir():
                    raise ProjectError(f"include dir does not exist: {p}")
                if p not in seen:
                    seen.add(p)
                    out.append(p)
        if self.resolved_uvm_home():
            out.append(self.resolved_uvm_home())
        return out

    def defines(self, *, filesets: list[str] | None = None) -> list[str]:
        out: list[str] = []
        for fs in self.filesets:
            if filesets and fs.name not in filesets:
                continue
            for d in fs.defines:
                if d not in out:
                    out.append(d)
        return out

    def resolved_uvm_home(self) -> Path | None:
        raw = self.uvm_home or os.environ.get("UVM_HOME")
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = self.root / p
        return p.resolve() if p.exists() else None

    def get_test(self, name: str) -> TestSpec:
        for t in self.tests:
            if t.name == name:
                return t
        raise ProjectError(
            f"no test named {name!r}; known tests: {[t.name for t in self.tests]}"
        )

    def tests_in_tier(self, tier: str) -> list[TestSpec]:
        """Tiers are cumulative: L2 includes L0/L1/L2."""
        order = ["L0", "L1", "L2", "L3", "L4", "L5"]
        if tier not in order:
            raise ProjectError(f"unknown tier {tier!r}; expected one of {order}")
        allowed = set(order[: order.index(tier) + 1])
        return [t for t in self.tests if t.tier in allowed]

    @property
    def build_path(self) -> Path:
        return self.root / self.build_dir

    @property
    def results_path(self) -> Path:
        return self.root / self.results_dir

    def to_dict(self) -> dict:
        d = asdict(self)
        d["root"] = str(self.root)
        return d
