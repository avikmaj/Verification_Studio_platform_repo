"""Format-dispatching waveform reader.

`open_waveform()` is what callers use. It picks a reader by content, never by
file extension alone, and it refuses to guess: if a format cannot be read on
this machine it raises rather than returning an empty database. An empty
waveform and an unreadable waveform are different facts, and conflating them
produces "0 signals" reports that look like successful reads.

FST support is provided by converting through GTKWave's `fst2vcd`. That is an
honest PARTIALLY_SUPPORTED: it works, but it costs a conversion pass and needs
an external tool. A native FST reader is planned.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core.errors import UnsupportedFeature
from ..core.process import ProcessManager
from ..plugins.interfaces import FeatureStatus
from .vcd import VCDReader, WaveDB

_VCD_MAGIC = b"$date"
_FST_MAGIC = b"\x00"          # FST starts with a block-type byte


def detect_format(path: Path) -> str:
    """Return 'vcd' | 'fst' | 'unknown' from content, falling back to suffix."""
    path = Path(path)
    try:
        head = path.open("rb").read(64)
    except OSError:
        head = b""

    if b"$date" in head or b"$timescale" in head or b"$version" in head:
        return "vcd"
    suffix = path.suffix.lower().lstrip(".")
    if suffix in ("vcd", "fst", "evcd"):
        # FST is binary with no stable printable magic; trust the suffix once we
        # know it is not VCD text.
        return suffix
    return "unknown"


class FSTReader:
    """FST reader implemented by converting to VCD with GTKWave's fst2vcd."""

    name = "fst"

    def __init__(self) -> None:
        self.proc = ProcessManager()
        self.converter = shutil.which("fst2vcd")

    def is_available(self) -> bool:
        return self.converter is not None

    def capabilities(self) -> dict[str, FeatureStatus]:
        return {
            "fst_read": (
                FeatureStatus.PARTIALLY_SUPPORTED
                if self.is_available()
                else FeatureStatus.UNSUPPORTED
            ),
            "fst_native_read": FeatureStatus.PLANNED,
            "conversion_via_fst2vcd": (
                FeatureStatus.SUPPORTED if self.is_available() else FeatureStatus.UNSUPPORTED
            ),
        }

    def open(self, path: Path) -> WaveDB:
        if not self.is_available():
            raise UnsupportedFeature(
                "FST waveforms require GTKWave's `fst2vcd` on PATH "
                "(a native FST reader is PLANNED). "
                "Install gtkwave, or dump VCD instead with `waves: vcd`."
            )
        path = Path(path)
        with tempfile.TemporaryDirectory(prefix="uvmstudio-fst-") as tmp:
            out = Path(tmp) / (path.stem + ".vcd")
            res = self.proc.run(
                [self.converter, "-f", str(path), "-o", str(out)],
                timeout_s=600.0,
            )
            if not out.exists() or out.stat().st_size == 0:
                raise UnsupportedFeature(
                    f"fst2vcd produced no output for {path} "
                    f"(rc={res.returncode}): {res.stderr[-500:]}"
                )
            db = VCDReader().open(out)
        return db


def open_waveform(path: Path) -> tuple[WaveDB, str]:
    """Open any supported waveform. Returns (db, format)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"waveform not found: {path}")
    fmt = detect_format(path)
    if fmt == "vcd":
        return VCDReader().open(path), "vcd"
    if fmt == "fst":
        return FSTReader().open(path), "fst"
    if fmt == "evcd":
        raise UnsupportedFeature(
            "EVCD reading is PLANNED — not implemented. "
            "Dump VCD or FST instead."
        )
    raise UnsupportedFeature(
        f"unrecognised waveform format for {path}; "
        f"supported: VCD (native), FST (via fst2vcd)"
    )
