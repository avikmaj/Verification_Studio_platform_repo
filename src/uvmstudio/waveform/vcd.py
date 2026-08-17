"""VCD waveform reader.

A real IEEE 1364 VCD parser — header, scopes, variable declarations and value
changes — producing a signal database the viewer and cross-probing layer can
query by time range.

FST is the default dump format for size/speed, but VCD is the interoperable one
and is what the conformance tests assert against. The `IWaveformReader`
interface is what the rest of the platform depends on, so an FST reader can be
added without touching any caller.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from ..plugins.interfaces import FeatureStatus

_TIMESCALE_RE = re.compile(r"(\d+)\s*([munpf]?s)")


@dataclass
class Signal:
    identifier: str          # VCD id code, e.g. "!"
    name: str
    width: int
    var_type: str
    scope: str
    bit_range: str = ""      # "[7:0]" for vectors; empty for scalars

    @property
    def path(self) -> str:
        return f"{self.scope}.{self.name}" if self.scope else self.name


@dataclass
class WaveDB:
    """Parsed VCD: hierarchy plus per-identifier value-change series."""

    timescale: str = "1ns"
    date: str = ""
    version: str = ""
    signals: dict[str, Signal] = field(default_factory=dict)      # id -> Signal
    by_path: dict[str, str] = field(default_factory=dict)         # path -> id
    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    end_time: int = 0

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    def scopes(self) -> list[str]:
        return sorted({s.scope for s in self.signals.values() if s.scope})

    def find(self, path: str) -> Signal | None:
        sid = self.by_path.get(path)
        return self.signals.get(sid) if sid else None

    def series(self, path: str) -> list[tuple[int, str]]:
        sid = self.by_path.get(path)
        return self.changes.get(sid, []) if sid else []

    def value_at(self, path: str, time: int) -> str | None:
        """Last value at or before `time` — the semantics a waveform cursor needs."""
        series = self.series(path)
        if not series:
            return None
        times = [t for t, _ in series]
        idx = bisect_right(times, time) - 1
        return series[idx][1] if idx >= 0 else None

    def window(self, path: str, t0: int, t1: int) -> list[tuple[int, str]]:
        """Value changes in [t0, t1], prefixed with the value entering the window."""
        series = self.series(path)
        if not series:
            return []
        out = []
        entering = self.value_at(path, t0)
        if entering is not None:
            out.append((t0, entering))
        out.extend((t, v) for t, v in series if t0 < t <= t1)
        return out

    def summary(self) -> dict:
        return {
            "timescale": self.timescale,
            "date": self.date,
            "version": self.version,
            "signal_count": self.signal_count,
            "end_time": self.end_time,
            "scopes": self.scopes(),
            "signals": [
                {"path": s.path, "width": s.width, "type": s.var_type}
                for s in sorted(self.signals.values(), key=lambda s: s.path)
            ][:500],
        }


class VCDReader:
    """Streaming VCD parser. Memory scales with change count, not file size."""

    name = "vcd"

    def capabilities(self) -> dict[str, FeatureStatus]:
        return {
            "vcd_read": FeatureStatus.SUPPORTED,
            "scopes": FeatureStatus.SUPPORTED,
            "scalar_values": FeatureStatus.SUPPORTED,
            "vector_values": FeatureStatus.SUPPORTED,
            "real_values": FeatureStatus.SUPPORTED,
            "four_state_xz": FeatureStatus.SUPPORTED,
            "time_window_query": FeatureStatus.SUPPORTED,
            "fst_read": FeatureStatus.PLANNED,
            "evcd_read": FeatureStatus.PLANNED,
            "transaction_overlay": FeatureStatus.PLANNED,
        }

    def open(self, path: Path) -> WaveDB:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"waveform not found: {path}")
        db = WaveDB()
        scope_stack: list[str] = []
        time = 0
        in_header = True

        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for token in _tokenize(fh):
                if in_header:
                    kw = token[0]
                    if kw == "$timescale":
                        db.timescale = " ".join(token[1:-1]).strip() or db.timescale
                    elif kw == "$date":
                        db.date = " ".join(token[1:-1]).strip()
                    elif kw == "$version":
                        db.version = " ".join(token[1:-1]).strip()
                    elif kw == "$scope":
                        if len(token) >= 3:
                            scope_stack.append(token[2])
                    elif kw == "$upscope":
                        if scope_stack:
                            scope_stack.pop()
                    elif kw == "$var":
                        # $var wire 8 ! data [7:0] $end
                        if len(token) >= 5:
                            var_type, width, ident = token[1], int(token[2]), token[3]
                            name = token[4]
                            bit_range = ""
                            if len(token) > 5 and token[5].startswith("["):
                                bit_range = token[5]
                            # A full range ("[7:0]") describes the vector and is
                            # not part of its name; a single index ("[3]") does
                            # identify a distinct signal and is kept.
                            if bit_range and ":" not in bit_range:
                                name = f"{name}{bit_range}"
                                bit_range = ""
                            sig = Signal(
                                identifier=ident,
                                name=name,
                                width=width,
                                var_type=var_type,
                                scope=".".join(scope_stack),
                                bit_range=bit_range,
                            )
                            db.signals[ident] = sig
                            db.by_path[sig.path] = ident
                            # Allow lookup with the range suffix too, so paths
                            # copied out of a viewer still resolve.
                            if bit_range:
                                db.by_path[f"{sig.path}{bit_range}"] = ident
                            db.changes.setdefault(ident, [])
                    elif kw in ("$enddefinitions",):
                        in_header = False
                    continue

                # --- value change section --------------------------------
                for item in token:
                    if not item:
                        continue
                    if item[0] == "#":
                        try:
                            time = int(item[1:])
                            db.end_time = max(db.end_time, time)
                        except ValueError:
                            pass
                    elif item[0] in "bB":
                        continue  # handled with its identifier below
                    elif item[0] in "rR":
                        continue
                    else:
                        pass
                # re-walk with pair awareness (vectors are "b1010 <id>")
                i = 0
                while i < len(token):
                    item = token[i]
                    if not item:
                        i += 1
                        continue
                    c = item[0]
                    if c == "#":
                        i += 1
                        continue
                    if c in "bBrR" and i + 1 < len(token):
                        ident = token[i + 1]
                        if ident in db.changes:
                            db.changes[ident].append((time, item[1:]))
                        i += 2
                        continue
                    if c in "01xXzZ" and len(item) >= 2:
                        ident = item[1:]
                        if ident in db.changes:
                            db.changes[ident].append((time, c))
                        i += 1
                        continue
                    i += 1
        return db

    def hierarchy(self, db: WaveDB) -> dict:
        tree: dict = {}
        for sig in db.signals.values():
            node = tree
            for part in (sig.scope.split(".") if sig.scope else []):
                node = node.setdefault(part, {})
            node.setdefault("__signals__", []).append(sig.name)
        return tree


def _tokenize(fh) -> Iterator[list[str]]:
    """Yield header commands ($...$end) as one list, and each data line as a list."""
    buf: list[str] = []
    collecting = False
    for line in fh:
        parts = line.split()
        if not parts:
            continue
        if collecting:
            buf.extend(parts)
            if "$end" in parts:
                collecting = False
                yield buf
                buf = []
            continue
        if parts[0].startswith("$") and parts[0] not in ("$end", "$dumpvars",
                                                         "$dumpall", "$dumpon",
                                                         "$dumpoff"):
            if "$end" in parts:
                yield parts
            else:
                collecting = True
                buf = list(parts)
            continue
        yield parts
