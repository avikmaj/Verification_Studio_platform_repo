"""Event-driven simulation kernel — IEEE 1800 stratified scheduler (subset).

Regions modeled per time slot:

    Active     process resumptions and blocking assignments
    (deltas)   value changes wake sensitive processes in a new delta
    NBA        nonblocking assignment updates, applied after Active settles;
               their wakeups form the next delta
    time       advance to the next scheduled event time

Processes are Python generators. They yield scheduling requests:

    ("delay", ticks)             resume after #ticks
    ("edges", [(signal, kind)])  resume on a matching edge, kind in
                                 {"pos","neg","any"}
    ("finish",)                  $finish

A time slot that fails to settle within MAX_DELTAS deltas is a zero-time
loop; the kernel reports it as an error instead of hanging — a simulator
that hangs is worse than one that stops with a diagnosis.
"""

from __future__ import annotations

import heapq
from typing import Any, Callable, Generator, Iterable

from .fourstate import FourState

MAX_DELTAS = 10_000

Proc = Generator[tuple, Any, None]


class Signal:
    __slots__ = ("name", "width", "value", "kernel", "key", "signed", "_edge_waiters")

    def __init__(self, name: str, width: int, kernel: "Kernel",
                 signed: bool = False) -> None:
        self.name = name
        self.width = width
        self.signed = signed
        self.kernel = kernel
        self.key = id(self)
        self.value = FourState.all_x(width, signed)
        self._edge_waiters: list[tuple["Process", str]] = []

    # -- reads -------------------------------------------------------------
    def read(self) -> FourState:
        k = self.kernel
        if k.tracking_reads is not None:
            k.tracking_reads.add(self)
        return self.value

    # -- writes ------------------------------------------------------------
    def write(self, value: FourState) -> None:
        value = value.resize(self.width, self.signed)
        old = self.value
        if old.aval == value.aval and old.bval == value.bval:
            return
        self.value = value
        if self.kernel.tracking_writes is not None:
            self.kernel.tracking_writes.add(self)
        self.kernel.on_change(self, old, value)

    def nba_write(self, value: FourState) -> None:
        self.kernel.nba_queue.append((self, value))


def _edge_matches(kind: str, old: FourState, new: FourState) -> bool:
    if kind == "any":
        # write() only notifies on a real value change, and "any" means any
        # bit — comparing just bit 0 here silently missed changes like 3->9
        # on a vector (both odd), which broke @(*) sensitivity. Found by
        # test_combinational_process_reacts_to_inputs.
        return True
    o, n = old.bit_char(0), new.bit_char(0)
    if o == n:
        return False
    if kind == "pos":                    # LRM: to 1, or from 0 to x/z
        return n == "1" or (o == "0" and n in "xz")
    if kind == "neg":
        return n == "0" or (o == "1" and n in "xz")
    return False


class Process:
    __slots__ = ("gen", "name", "waiting_edges", "done")

    def __init__(self, gen: Proc, name: str) -> None:
        self.gen = gen
        self.name = name
        self.waiting_edges: list[tuple[Signal, str]] = []
        self.done = False


class SimulationError(Exception):
    pass


class Kernel:
    def __init__(self) -> None:
        self.time = 0
        self.finished = False
        self.finish_time: int | None = None
        self.nba_queue: list[tuple[Signal, FourState]] = []
        self._active: list[Process] = []
        self._timed: list[tuple[int, int, Process]] = []   # heap
        self._seq = 0
        self._procs: list[Process] = []
        self.signals: list[Signal] = []
        self.tracking_reads: set[Signal] | None = None
        self.tracking_writes: set[Signal] | None = None
        self.on_signal_change: Callable[[Signal, int], None] | None = None
        self.stdout: list[str] = []

    # -- construction ------------------------------------------------------
    def signal(self, name: str, width: int, signed: bool = False) -> Signal:
        s = Signal(name, width, self, signed)
        self.signals.append(s)
        return s

    def spawn(self, gen: Proc, name: str) -> None:
        p = Process(gen, name)
        self._procs.append(p)
        self._active.append(p)

    # -- change notification ----------------------------------------------
    def on_change(self, sig: Signal, old: FourState, new: FourState) -> None:
        if self.on_signal_change:
            self.on_signal_change(sig, self.time)
        woken: list[Process] = []
        for p, kind in list(sig._edge_waiters):
            if not p.done and _edge_matches(kind, old, new):
                woken.append(p)
        for p in woken:
            self._unsubscribe(p)
            self._active.append(p)

    def _unsubscribe(self, p: Process) -> None:
        for sig, _ in p.waiting_edges:
            sig._edge_waiters = [(q, k) for (q, k) in sig._edge_waiters if q is not p]
        p.waiting_edges = []

    # -- execution ---------------------------------------------------------
    def _resume(self, p: Process, send: Any = None) -> None:
        try:
            req = p.gen.send(send)
        except StopIteration:
            p.done = True
            return
        kind = req[0]
        if kind == "delay":
            self._seq += 1
            heapq.heappush(self._timed, (self.time + int(req[1]), self._seq, p))
        elif kind == "edges":
            for sig, ek in req[1]:
                sig._edge_waiters.append((p, ek))
                p.waiting_edges.append((sig, ek))
        elif kind == "finish":
            self.finished = True
            self.finish_time = self.time
            p.done = True
        else:  # pragma: no cover - defensive
            raise SimulationError(f"unknown scheduling request {req!r}")

    def run(self, time_limit: int = 10_000_000) -> None:
        while not self.finished:
            deltas = 0
            # settle the current time slot: Active until quiet, then NBA
            while self._active or self.nba_queue:
                deltas += 1
                if deltas > MAX_DELTAS:
                    raise SimulationError(
                        f"zero-time loop: time slot at t={self.time} did not "
                        f"settle within {MAX_DELTAS} deltas"
                    )
                batch, self._active = self._active, []
                for p in batch:
                    if not p.done:
                        self._resume(p)
                    if self.finished:
                        return
                if not self._active and self.nba_queue:
                    updates, self.nba_queue = self.nba_queue, []
                    for sig, val in updates:
                        sig.write(val)
            # advance time
            if not self._timed:
                return                     # event starvation: nothing left
            t, _, p = heapq.heappop(self._timed)
            if t > time_limit:
                raise SimulationError(
                    f"time limit {time_limit} exceeded (next event at {t})"
                )
            self.time = t
            self._active.append(p)
            while self._timed and self._timed[0][0] == t:
                _, _, q = heapq.heappop(self._timed)
                self._active.append(q)
