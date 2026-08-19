"""Our own VCD writer (IEEE 1364 §18 / 1800 §21.7 subset).

The platform already has its own VCD *reader* (`uvmstudio.waveform.vcd`);
this is the other half. Writer output is deliberately round-trippable through
that reader — the engine's tests dump a wave and read it back with our own
parser, which keeps both honest.
"""

from __future__ import annotations

from pathlib import Path

from .fourstate import FourState

_ID_CHARS = "".join(chr(c) for c in range(33, 127))


def _ident(n: int) -> str:
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, len(_ID_CHARS))
        out += _ID_CHARS[r]
    return out


class VCDWriter:
    def __init__(self, path: Path, timescale: str = "1ns") -> None:
        self.path = Path(path)
        self._f = open(self.path, "w")
        self._ids: dict[int, str] = {}       # signal key -> identifier
        self._n_ids = 0
        self._scope_stack: list[str] = []
        self._last: dict[int, str] = {}      # identifier -> last emitted value
        self._time = -1
        self._header_done = False
        self._f.write("$date\n  uvmstudio native engine\n$end\n")
        self._f.write(f"$timescale {timescale} $end\n")

    # -- declaration phase -------------------------------------------------
    def begin_scope(self, name: str) -> None:
        self._f.write(f"$scope module {name} $end\n")

    def end_scope(self) -> None:
        self._f.write("$upscope $end\n")

    def enter_path(self, path: str) -> None:
        """Move the scope stack to a dotted hierarchical path, emitting only
        the transitions. Re-opening every path from the root duplicated
        scopes (RT-P-005)."""
        parts = path.split(".")
        common = 0
        while (common < len(parts) and common < len(self._scope_stack)
               and self._scope_stack[common] == parts[common]):
            common += 1
        for _ in range(len(self._scope_stack) - common):
            self.end_scope()
            self._scope_stack.pop()
        for part in parts[common:]:
            self.begin_scope(part)
            self._scope_stack.append(part)

    def close_scopes(self) -> None:
        while self._scope_stack:
            self.end_scope()
            self._scope_stack.pop()

    def add_signal(self, key: int, name: str, width: int) -> None:
        # A signal aliased across the hierarchy (port unification) keeps ONE
        # identifier and gets a second $var name — the VCD aliasing model.
        # Allocating a fresh ident per call collided identifiers instead
        # (red-team RT-P-005, caught by dumping a hierarchical design).
        ident = self._ids.get(key)
        if ident is None:
            ident = _ident(self._n_ids)
            self._n_ids += 1
            self._ids[key] = ident
        ref = name if width == 1 else f"{name} [{width - 1}:0]"
        self._f.write(f"$var wire {width} {ident} {ref} $end\n")

    def end_definitions(self) -> None:
        self._f.write("$enddefinitions $end\n")
        self._header_done = True

    # -- dump phase --------------------------------------------------------
    def change(self, key: int, value: FourState, time: int) -> None:
        ident = self._ids.get(key)
        if ident is None:
            return
        text = (
            value.bit_char(0) + ident
            if value.width == 1
            else f"b{value.to_bin()} {ident}"
        )
        if self._last.get(ident) == text:
            return
        if time != self._time:
            self._f.write(f"#{time}\n")
            self._time = time
        self._f.write(text + "\n")
        self._last[ident] = text

    def close(self, end_time: int | None = None) -> None:
        if end_time is not None and end_time != self._time:
            self._f.write(f"#{end_time}\n")
        self._f.close()
