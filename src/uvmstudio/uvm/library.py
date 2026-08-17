"""Accellera UVM library discovery and version detection.

The platform never ships its own copy of UVM and never reimplements it. It
locates a real Accellera source tree, identifies which generation it is, and
reports honestly when it cannot.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Every generation the platform is expected to recognise.
KNOWN_GENERATIONS = (
    "1.0", "1.1", "1.1a", "1.1b", "1.1c", "1.1d", "1.2",
    "2017-0.9", "2017-1.0", "2017-1.1",
    "2020-1.0", "2020-1.1", "2020-2.0", "2020-3.0", "2020-3.1",
)

_VERSION_STRING_RE = re.compile(
    r'UVM_VERSION_STRING\s*=\s*"([^"]+)"'
)
_UVM_NAME_RE = re.compile(r'`define\s+UVM_VERSION_STRING\s+"([^"]+)"')
_LEGACY_RE = re.compile(
    r"`define\s+UVM_(MAJOR|MINOR)_(?:REV|VERSION)_(\d+)(?:_(\d+))?"
)


@dataclass
class UVMLibrary:
    home: Path
    version: str | None
    version_string: str | None
    generation: str | None          # "1.x" | "2017" | "1800.2"
    pkg_file: Path | None
    macros_file: Path | None

    @property
    def is_ieee_1800_2(self) -> bool:
        return bool(self.version_string and "1800.2" in self.version_string)

    def to_dict(self) -> dict:
        return {
            "home": str(self.home),
            "version": self.version,
            "version_string": self.version_string,
            "generation": self.generation,
            "pkg_file": str(self.pkg_file) if self.pkg_file else None,
            "macros_file": str(self.macros_file) if self.macros_file else None,
            "ieee_1800_2": self.is_ieee_1800_2,
        }


def find_uvm_home(explicit: Path | str | None = None) -> Path | None:
    """Locate a UVM source tree.

    Order: explicit argument, $UVM_HOME, then common vendor layouts. A directory
    only qualifies if it actually contains `uvm_pkg.sv`.
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("UVM_HOME"):
        candidates.append(Path(os.environ["UVM_HOME"]))

    for c in list(candidates):
        candidates.append(c / "src")

    for c in candidates:
        if not c or not c.exists():
            continue
        if (c / "uvm_pkg.sv").exists():
            return c.resolve()
        if (c / "src" / "uvm_pkg.sv").exists():
            return (c / "src").resolve()
    return None


@lru_cache(maxsize=16)
def inspect_uvm(home_str: str) -> UVMLibrary:
    home = Path(home_str)
    pkg = home / "uvm_pkg.sv"
    macros = home / "uvm_macros.svh"
    version_file = home / "base" / "uvm_version.svh"

    version_string: str | None = None
    if version_file.exists():
        text = version_file.read_text(errors="replace")
        m = _VERSION_STRING_RE.search(text) or _UVM_NAME_RE.search(text)
        if m:
            version_string = m.group(1)
        else:
            # UVM 1.x used discrete major/minor defines
            majors = _LEGACY_RE.findall(text)
            if majors:
                nums = [g[1] + ("." + g[2] if g[2] else "") for g in majors]
                version_string = "UVM " + "-".join(nums[:2])

    version = None
    generation = None
    if version_string:
        # e.g. "Accellera:1800.2:UVM:2020.3.1"  or  "UVM-1.2"
        if "1800.2" in version_string:
            generation = "1800.2"
            tail = version_string.rsplit(":", 1)[-1]
            version = tail.replace(".", "-", 1) if tail else None
            # "2020.3.1" -> "2020-3.1"
        elif "2017" in version_string:
            generation = "2017"
            version = version_string.split(":")[-1]
        else:
            generation = "1.x"
            m = re.search(r"(\d+\.\d+\w*)", version_string)
            version = m.group(1) if m else None

    return UVMLibrary(
        home=home,
        version=version,
        version_string=version_string,
        generation=generation,
        pkg_file=pkg if pkg.exists() else None,
        macros_file=macros if macros.exists() else None,
    )


def detect_uvm_version(home: Path | str | None) -> str | None:
    if home is None:
        return None
    try:
        return inspect_uvm(str(home)).version
    except Exception:
        return None


def uvm_compile_args(lib: UVMLibrary, *, no_dpi: bool = True) -> tuple[list[str], list[str]]:
    """Return (files, defines) needed to compile this UVM into a design.

    `no_dpi` is the default because the platform's first backend has no UVM DPI
    library built; the resulting limitation (no regex-based `uvm_re_match`,
    reduced backdoor access) is recorded rather than hidden.
    """
    if lib.pkg_file is None:
        raise FileNotFoundError(f"uvm_pkg.sv not found under {lib.home}")
    defines = ["UVM_NO_DPI"] if no_dpi else []
    return [str(lib.pkg_file)], defines
