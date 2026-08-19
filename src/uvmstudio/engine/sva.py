"""Concurrent SVA on the native kernel — engine phase N2.

Supported property shape (anything else raises UnsupportedFeature):

    [label:] assert|cover property (
        @(posedge|negedge clk) [disable iff (expr)]
        boolean_expr
      | boolean_expr |-> sequence
      | boolean_expr |=> sequence )
    [else action_stmt]

where sequence is a ##N-concatenation of boolean expressions (fixed delays).
Booleans may use $rose/$fell/$stable/$past/$sampled of 1-bit-testable
expressions, evaluated against values sampled at the previous tick of the
assertion clock.

Semantics notes, stated rather than hidden:
- Sampling happens when the assertion process wakes on the clock edge, after
  NBA updates for that edge have applied — the Observed-region view. A TB
  that drives with blocking assignments *at* the sampling edge can therefore
  race, exactly as it can in event simulators.
- Vacuity is tracked per assert: an implication whose antecedent never held
  is VACUOUS, per the GATE 7 rule that a vacuous assertion proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.errors import UnsupportedFeature
from .fourstate import FourState
from .kernel import Signal


def _u(what: str) -> UnsupportedFeature:
    return UnsupportedFeature(f"native SVA: unsupported {what}")


@dataclass
class Step:
    delay: int              # clock ticks after the previous step
    expr: Any               # bound boolean expression


@dataclass
class CompiledProperty:
    clock: Signal
    edge: str                       # "pos" | "neg"
    disable: Any | None             # boolean expression or None
    antecedent: Any | None          # None => plain boolean/cover property
    consequent: list[Step] = field(default_factory=list)


@dataclass
class Attempt:
    steps: list[Step]
    countdown: int


@dataclass
class SvaResult:
    name: str
    kind: str                       # "assert" | "cover"
    attempts: int = 0
    nonvacuous: int = 0
    passes: int = 0
    fails: int = 0
    covered: int = 0
    disabled_ticks: int = 0

    @property
    def vacuous(self) -> bool:
        return self.kind == "assert" and self.attempts > 0 \
            and self.nonvacuous == 0 and self.fails == 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "attempts": self.attempts,
            "nonvacuous": self.nonvacuous, "passes": self.passes,
            "fails": self.fails, "covered": self.covered,
            "vacuous": self.vacuous,
        }


class SvaAssertion:
    """One concurrent assertion: compiler + per-clock evaluator process."""

    def __init__(self, interp: Any, name: str, kind: str,
                 spec: Any, action_else: Any | None) -> None:
        self.interp = interp
        self.action_else = action_else
        self.result = SvaResult(name=name, kind=kind)
        self.prop = self._compile(spec)
        self._prev: dict[int, FourState] = {}      # signal key -> prev sample
        self._attempts: list[Attempt] = []

    # -- compile -----------------------------------------------------------
    def _compile(self, spec: Any) -> CompiledProperty:
        k = str(spec.kind).split(".")[-1]
        if k != "Clocking":
            raise _u(f"property without a clocking event ({k})")
        timing = spec.clocking
        tk = str(timing.kind).split(".")[-1]
        if tk != "SignalEvent":
            raise _u(f"clocking control {tk}")
        clock_sig, edge = self.interp._edge_of(timing)
        if edge == "any":
            raise _u("clocking without an explicit edge")

        inner = spec.expr
        disable = None
        if str(inner.kind).endswith("DisableIff"):
            disable = inner.condition
            inner = inner.expr

        ik = str(inner.kind).split(".")[-1]
        if ik == "Simple":
            self._check_simple(inner)
            return CompiledProperty(clock_sig, edge, disable,
                                    antecedent=None,
                                    consequent=[Step(0, inner.expr)])
        if ik == "Binary":
            op = str(inner.op).split(".")[-1]
            if op not in ("OverlappedImplication", "NonOverlappedImplication"):
                raise _u(f"property operator {op}")
            lk = str(inner.left.kind).split(".")[-1]
            if lk != "Simple":
                raise _u(f"antecedent kind {lk} (sequences on the left of an "
                         "implication are not supported yet)")
            self._check_simple(inner.left)
            steps = self._compile_seq(inner.right)
            if op == "NonOverlappedImplication":
                steps[0] = Step(steps[0].delay + 1, steps[0].expr)
            return CompiledProperty(clock_sig, edge, disable,
                                    antecedent=inner.left.expr,
                                    consequent=steps)
        raise _u(f"assertion expression {ik}")

    @staticmethod
    def _check_simple(node: Any) -> None:
        rep = getattr(node, "repetition", None)
        if rep is not None:
            raise _u("sequence repetition ([*n] and friends)")

    def _compile_seq(self, node: Any) -> list[Step]:
        k = str(node.kind).split(".")[-1]
        if k == "Simple":
            self._check_simple(node)
            return [Step(0, node.expr)]
        if k == "SequenceConcat":
            steps: list[Step] = []
            for el in node.elements:
                if el.delay.min != el.delay.max:
                    raise _u(f"delay range ##[{el.delay.min}:{el.delay.max}] "
                             "(only fixed ##N delays)")
                sk = str(el.sequence.kind).split(".")[-1]
                if sk != "Simple":
                    raise _u(f"sequence element {sk}")
                self._check_simple(el.sequence)
                steps.append(Step(int(el.delay.min), el.sequence.expr))
            return steps
        raise _u(f"sequence {k}")

    # -- evaluation --------------------------------------------------------
    def _eval_bool(self, expr: Any) -> bool | None:
        self.interp.sva_prev = self._prev
        try:
            return self.interp.eval(expr).is_true()
        finally:
            self.interp.sva_prev = None

    def _snapshot(self) -> None:
        for sig in self.interp.kernel.signals:
            self._prev[sig.key] = sig.value

    def _complete(self) -> None:
        if self.result.kind == "cover":
            self.result.covered += 1
        else:
            self.result.passes += 1

    def _miss(self) -> None:
        if self.result.kind != "assert":
            return                         # an unmatched cover is just not covered
        self.result.fails += 1
        if self.action_else is not None:
            for _ in self.interp.exec_stmt(self.action_else):
                break                      # action blocks must not block

    def process(self):
        """Kernel process: evaluate on every matching clock edge."""
        prop = self.prop
        r = self.result
        # First-tick sampled values are the signals' initial values, not X:
        # $stable at the first clock must compare against the value the
        # signal held before any edge (found by test_stable_and_past).
        self._snapshot()
        while True:
            yield ("edges", [(prop.clock, prop.edge)])

            if prop.disable is not None and self._eval_bool(prop.disable):
                r.disabled_ticks += 1
                self._attempts.clear()
                self._snapshot()
                continue

            # advance outstanding attempts (obligations from earlier ticks)
            still: list[Attempt] = []
            for at in self._attempts:
                at.countdown -= 1
                failed = False
                while at.countdown == 0 and at.steps:
                    step = at.steps.pop(0)
                    ok = self._eval_bool(step.expr)
                    if ok is not True:
                        self._miss()
                        failed = True
                        break
                    if at.steps:
                        at.countdown = at.steps[0].delay
                        if at.countdown == 0:
                            continue       # overlapping ##0 step, same tick
                    else:
                        self._complete()
                if not failed and at.steps and at.countdown > 0:
                    still.append(at)
            self._attempts = still

            # start a new attempt at this tick
            r.attempts += 1
            if prop.antecedent is None:
                ok = self._eval_bool(prop.consequent[0].expr)
                if self.result.kind == "cover":
                    if ok is True:
                        r.covered += 1
                else:
                    r.nonvacuous += 1
                    if ok is True:
                        r.passes += 1
                    else:
                        self._miss()
            else:
                ante = self._eval_bool(prop.antecedent)
                if ante is True:
                    r.nonvacuous += 1
                    steps = [Step(s.delay, s.expr) for s in prop.consequent]
                    if steps[0].delay == 0:
                        # overlapped: first obligation due this very tick
                        at = Attempt(steps, 0)
                        failed = False
                        while at.steps and at.countdown == 0:
                            step = at.steps.pop(0)
                            ok = self._eval_bool(step.expr)
                            if ok is not True:
                                self._miss()
                                failed = True
                                break
                            if at.steps:
                                at.countdown = at.steps[0].delay
                            else:
                                self._complete()
                        if not failed and at.steps:
                            self._attempts.append(at)
                    else:
                        self._attempts.append(Attempt(steps, steps[0].delay))

            self._snapshot()
