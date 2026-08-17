"""Reproducibility record.

Every simulation writes one of these. It captures enough to re-create the run
byte-for-byte on another machine: source hash, Git state, tool versions,
defines, include paths, plusargs, seed, and the exact command lines used.

`uvmstudio reproduce <run.json>` consumes it. If a field is unknown it is
recorded as null and `complete` goes false — a record that cannot reproduce a
run must say so rather than look authoritative.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import socket
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..core.hashing import hash_sources
from ..core.platform import platform_report

SCHEMA_VERSION = "uvmstudio-repro/1"


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except Exception:
        return None


@dataclass
class GitState:
    commit: str | None = None
    branch: str | None = None
    dirty: bool | None = None
    remote: str | None = None
    describe: str | None = None

    @staticmethod
    def capture(root: Path) -> "GitState":
        commit = _git(["rev-parse", "HEAD"], root)
        if commit is None:
            return GitState()
        status = _git(["status", "--porcelain"], root)
        return GitState(
            commit=commit,
            branch=_git(["rev-parse", "--abbrev-ref", "HEAD"], root),
            dirty=bool(status),
            remote=_git(["config", "--get", "remote.origin.url"], root),
            describe=_git(["describe", "--tags", "--always", "--dirty"], root),
        )


@dataclass
class ToolVersions:
    frontend: str | None = None
    frontend_version: str | None = None
    simulator_backend: str | None = None
    simulator_version: str | None = None
    simulator_banner: str | None = None
    exec_host: str | None = None
    uvm_version: str | None = None
    uvm_home: str | None = None
    solver: str | None = None
    studio_version: str | None = None


@dataclass
class ReproRecord:
    schema: str = SCHEMA_VERSION
    project: str = ""
    project_root: str = ""
    test: str = ""
    uvm_testname: str | None = None
    seed: int = 0
    top: str = ""
    language_standard: str = "1800-2017"

    source_hash: str | None = None
    source_files: list[str] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    plusargs: list[str] = field(default_factory=list)

    git: GitState = field(default_factory=GitState)
    tools: ToolVersions = field(default_factory=ToolVersions)

    build_command: list[str] = field(default_factory=list)
    run_command: list[str] = field(default_factory=list)
    frontend_command: str | None = None

    environment: dict[str, str] = field(default_factory=dict)
    host: dict[str, Any] = field(default_factory=dict)

    result: str = "NOT_VERIFIED"
    duration_s: float = 0.0
    started_utc: str = ""
    artifacts: dict[str, str | None] = field(default_factory=dict)

    # Environment variables that genuinely change simulation behaviour.
    TRACKED_ENV = (
        "UVM_HOME",
        "VERILATOR_ROOT",
        "UVMSTUDIO_VERILATOR",
        "UVMSTUDIO_WSL_DISTRO",
        "SYSTEMC_HOME",
        "LM_LICENSE_FILE",
        "SNPSLMD_LICENSE_FILE",
        "PATH",
    )

    @staticmethod
    def begin(
        *,
        project_name: str,
        project_root: Path,
        test: str,
        seed: int,
        top: str,
        language_standard: str,
        source_files: list[Path],
        include_paths: list[Path],
        defines: list[str],
        plusargs: list[str],
        uvm_testname: str | None = None,
    ) -> "ReproRecord":
        try:
            src_hash = hash_sources(source_files)
        except Exception:
            src_hash = None
        return ReproRecord(
            project=project_name,
            project_root=str(project_root),
            test=test,
            uvm_testname=uvm_testname,
            seed=seed,
            top=top,
            language_standard=language_standard,
            source_hash=src_hash,
            source_files=[str(p) for p in source_files],
            include_paths=[str(p) for p in include_paths],
            defines=list(defines),
            plusargs=list(plusargs),
            git=GitState.capture(project_root),
            environment={
                k: os.environ[k] for k in ReproRecord.TRACKED_ENV if k in os.environ
            },
            host={
                **platform_report(),
                "hostname": socket.gethostname(),
                "cpu_count": os.cpu_count(),
            },
            started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )

    # -- completeness ------------------------------------------------------
    def missing_fields(self) -> list[str]:
        """Fields whose absence makes this record non-reproducible."""
        missing = []
        if not self.source_hash:
            missing.append("source_hash")
        if not self.source_files:
            missing.append("source_files")
        if self.git.commit is None:
            missing.append("git.commit")
        if not self.tools.simulator_version:
            missing.append("tools.simulator_version")
        if not self.tools.frontend_version:
            missing.append("tools.frontend_version")
        if not self.run_command:
            missing.append("run_command")
        return missing

    @property
    def complete(self) -> bool:
        return not self.missing_fields()

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("TRACKED_ENV", None)
        d["complete"] = self.complete
        d["missing_fields"] = self.missing_fields()
        return d

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False))
        return path

    @staticmethod
    def load(path: Path) -> dict:
        data = json.loads(Path(path).read_text())
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported reproducibility schema {data.get('schema')!r}; "
                f"expected {SCHEMA_VERSION}"
            )
        return data
