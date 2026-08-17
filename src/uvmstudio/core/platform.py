"""Host platform abstraction — Windows, WSL and native Linux.

Why this exists: the platform must be drivable from Windows PowerShell, but
several simulator backends (Verilator among them) have no first-class native
Windows build. Rather than pretend otherwise, UVM Verification Studio splits
the problem:

  * Everything that is pure Python — project model, slang frontend, lint,
    regression orchestration, coverage/transaction DB, reports, CI generation —
    runs natively on Windows. pyslang ships Windows wheels.

  * Simulation is *dispatched*. A backend declares an `ExecHost`: native, WSL,
    or remote. On Windows the Verilator backend defaults to a WSL host and
    translates paths at the boundary.

Path translation is the whole game. A Windows path `C:\\work\\vip\\tb.sv` must
become `/mnt/c/work/vip/tb.sv` before it crosses into WSL, and results coming
back must be translated in reverse. Getting this wrong silently produces
"file not found" that looks like a project error.
"""

from __future__ import annotations

import os
import platform as _platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path, PureWindowsPath


class HostOS(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    UNKNOWN = "unknown"


class ExecHost(str, Enum):
    """Where a backend's executable actually runs."""

    NATIVE = "native"
    WSL = "wsl"
    REMOTE = "remote"


@lru_cache(maxsize=1)
def host_os() -> HostOS:
    s = _platform.system().lower()
    if s == "linux":
        return HostOS.LINUX
    if s == "windows":
        return HostOS.WINDOWS
    if s == "darwin":
        return HostOS.MACOS
    return HostOS.UNKNOWN


def is_windows() -> bool:
    return host_os() is HostOS.WINDOWS


@lru_cache(maxsize=1)
def inside_wsl() -> bool:
    """True when this Python process is itself running inside WSL."""
    if host_os() is not HostOS.LINUX:
        return False
    if "microsoft" in _platform.release().lower():
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


@lru_cache(maxsize=1)
def wsl_available() -> bool:
    """True when a usable `wsl.exe` with at least one installed distro exists."""
    if not is_windows():
        return False
    if shutil.which("wsl") is None:
        return False
    try:
        out = subprocess.run(
            ["wsl", "--list", "--quiet"],
            capture_output=True,
            timeout=30,
        )
        text = out.stdout.decode("utf-16-le", errors="replace")
        if not text.strip():
            text = out.stdout.decode("utf-8", errors="replace")
        return bool(text.strip())
    except Exception:
        return False


@lru_cache(maxsize=1)
def default_wsl_distro() -> str | None:
    if not wsl_available():
        return None
    return os.environ.get("UVMSTUDIO_WSL_DISTRO") or None


# --- path translation ----------------------------------------------------
_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")
_MNT_RE = re.compile(r"^/mnt/([a-z])/(.*)$")


def windows_to_wsl_path(path: str | Path) -> str:
    r"""C:\work\vip\tb.sv -> /mnt/c/work/vip/tb.sv

    UNC paths (\\server\share) have no /mnt/ equivalent and are rejected
    explicitly rather than silently mangled.
    """
    s = str(path)
    if s.startswith("\\\\"):
        raise ValueError(
            f"UNC path cannot be used from a WSL execution host: {s}. "
            "Map it to a drive letter, or copy the sources to a local path."
        )
    m = _DRIVE_RE.match(s)
    if not m:
        # already POSIX-ish; just normalise separators
        return s.replace("\\", "/")
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def wsl_to_windows_path(path: str | Path) -> str:
    """/mnt/c/work/vip/tb.sv -> C:\\work\\vip\\tb.sv"""
    s = str(path).replace("\\", "/")
    m = _MNT_RE.match(s)
    if not m:
        return s
    drive, rest = m.group(1).upper(), m.group(2)
    return str(PureWindowsPath(f"{drive}:/{rest}"))


def to_exec_host_path(path: str | Path, host: ExecHost) -> str:
    """Translate a host-native path into the execution host's namespace."""
    if host is ExecHost.WSL and is_windows():
        return windows_to_wsl_path(path)
    return str(path)


def from_exec_host_path(path: str | Path, host: ExecHost) -> str:
    if host is ExecHost.WSL and is_windows():
        return wsl_to_windows_path(path)
    return str(path)


@dataclass
class ExecContext:
    """How to launch a command for a given backend on this machine."""

    host: ExecHost = ExecHost.NATIVE
    wsl_distro: str | None = None

    @staticmethod
    def detect(preferred: ExecHost | None = None) -> "ExecContext":
        """Pick the sensible execution host for this machine.

        On Linux (including inside WSL) that is always native. On Windows it is
        WSL when available, because Verilator has no supported native Windows
        build; if WSL is absent the caller gets a NATIVE context and the backend
        will report itself unavailable rather than failing obscurely later.
        """
        if preferred is not None:
            return ExecContext(host=preferred, wsl_distro=default_wsl_distro())
        if is_windows():
            if wsl_available():
                return ExecContext(host=ExecHost.WSL, wsl_distro=default_wsl_distro())
            return ExecContext(host=ExecHost.NATIVE)
        return ExecContext(host=ExecHost.NATIVE)

    def wrap(self, argv: list[str]) -> list[str]:
        """Prefix argv with the launcher for this execution host."""
        if self.host is ExecHost.WSL:
            prefix = ["wsl.exe"]
            if self.wsl_distro:
                prefix += ["-d", self.wsl_distro]
            prefix += ["--"]
            return prefix + argv
        return list(argv)

    def path(self, p: str | Path) -> str:
        return to_exec_host_path(p, self.host)

    def unpath(self, p: str | Path) -> str:
        return from_exec_host_path(p, self.host)

    def describe(self) -> str:
        if self.host is ExecHost.WSL:
            return f"wsl({self.wsl_distro or 'default'})"
        return self.host.value


def platform_report() -> dict:
    """Everything the reproducibility record needs about this machine."""
    return {
        "os": host_os().value,
        "os_release": _platform.release(),
        "machine": _platform.machine(),
        "python": _platform.python_version(),
        "inside_wsl": inside_wsl(),
        "wsl_available": wsl_available(),
        "default_exec_host": ExecContext.detect().describe(),
    }
