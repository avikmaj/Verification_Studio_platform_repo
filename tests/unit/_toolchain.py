"""Shared test toolchain resolution.

The differential tests need Verilator >= 5.050 (5.020 lacks ## cycle-delay
support among others). Resolution order: UVMSTUDIO_VERILATOR env var, the
known 5.050 install prefix, then PATH. None found -> tests skip with the
reason named (skip != pass; CI must run the toolchain image for full cover).
"""

from __future__ import annotations

import os
import shutil

_CANDIDATES = [
    os.environ.get("UVMSTUDIO_VERILATOR"),
    "/opt/verilator-5.050/bin/verilator",
]


def find_verilator() -> str | None:
    for c in _CANDIDATES:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return shutil.which("verilator")
