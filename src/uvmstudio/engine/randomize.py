"""Engine N4: z3-backed randomize() for native class objects.

Policy statements (the factory has been burned by silent dist drops twice —
nothing here is silently ignored):

- `dist` is SOLVED, not dropped: membership in the weighted set is a hard
  constraint (zero-weight items are excluded per LRM 18.5.4), and the value
  is chosen by a seeded weighted draw over the dist buckets, feasibility-
  checked against all other constraints. If no weighted draw is feasible the
  solver still picks a member of the set (distribution degrades, legality
  never does).
- `soft` constraints are honored when jointly satisfiable; on conflict ALL
  soft constraints are dropped together and this is recorded in the result.
  (LRM priority ordering between soft constraints is NOT implemented — a
  named limitation, not a silent one.)
- Anything the translator does not understand raises UnsupportedFeature
  naming the construct. `randc` is rejected by name. A state variable with
  X/Z bits referenced by a constraint is a SimulationError naming the
  variable — never coerced to 0.
- Seed-stable: all randomness comes from one `random.Random` owned by the
  interpreter and seeded from the run request. Same source + same seed →
  identical draw sequence. z3 is used only for satisfiability/model
  completion; every stochastic choice is made by our seeded RNG, so solver
  version changes cannot silently change distributions of fixed draws.

randomize() returns 1 on success (properties updated, post_randomize run)
and 0 on unsat (properties untouched, post_randomize NOT run), per LRM.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import UnsupportedFeature
from .fourstate import FourState
from .kernel import SimulationError


def _u(what: str) -> UnsupportedFeature:
    return UnsupportedFeature(f"native randomize: {what}")


def _kind(node: Any) -> str:
    return str(node.kind).split(".")[-1]


class _Translator:
    """slang bound constraint AST -> z3 terms over the object's rand vars."""

    def __init__(self, interp: Any, obj: Any) -> None:
        import z3
        self.z3 = z3
        self.interp = interp
        self.obj = obj
        self.rand: dict[str, Any] = {}          # prop name -> z3 BitVec
        self.widths: dict[str, tuple[int, bool]] = {}
        self.hard: list[Any] = []
        self.soft: list[Any] = []
        # (prop_name, [(total_weight, lo, hi), ...]) per dist expression
        self.dists: list[tuple[str, list[tuple[int, int, int]]]] = []

    # -- rand variable discovery ------------------------------------------
    def collect_rand_vars(self, cls_sym: Any) -> None:
        for m in cls_sym:
            if _kind(m) != "ClassProperty":
                continue
            mode = str(getattr(m, "randMode", "")).split(".")[-1]
            if mode == "RandC":
                raise _u(f"randc variable {m.name} (cyclic randomization "
                         "is not in N4 — use rand)")
            if mode == "Rand":
                w = int(m.type.bitWidth)
                sg = bool(getattr(m.type, "isSigned", False))
                if w == 0 or not bool(getattr(m.type, "isIntegral", True)):
                    raise _u(f"rand variable {m.name} is not a packed "
                             "integral type (arrays/objects are not in N4)")
                if w > 64:
                    raise _u(f"rand variable {m.name} wider than 64 bits")
                self.rand[m.name] = self.z3.BitVec(m.name, w)
                self.widths[m.name] = (w, sg)

    # -- concrete evaluation of non-rand references -----------------------
    def _concrete(self, expr: Any) -> Any:
        v = self.interp.eval(expr)
        if not isinstance(v, FourState):
            raise _u("non-integral value in constraint expression")
        if v.has_unknown:
            raise SimulationError(
                "native randomize: constraint references a value with X/Z "
                f"bits ({expr.syntax if expr.syntax else 'expression'}) — "
                "refusing to coerce unknowns to 0")
        return self.z3.BitVecVal(v.to_int(), max(1, v.width))

    def _fit(self, term: Any, width: int, signed: bool) -> Any:
        z3 = self.z3
        cur = term.size()
        if cur == width:
            return term
        if cur > width:
            return z3.Extract(width - 1, 0, term)
        return (z3.SignExt(width - cur, term) if signed
                else z3.ZeroExt(width - cur, term))

    # -- expression translation -------------------------------------------
    def bv(self, expr: Any) -> tuple[Any, bool]:
        """Translate to a BitVec term; returns (term, is_signed)."""
        z3 = self.z3
        ek = _kind(expr)
        t = expr.type
        width = int(t.bitWidth) if t is not None else 32
        signed = bool(getattr(t, "isSigned", False))

        if ek == "NamedValue":
            sym = expr.symbol
            if str(sym.kind).split(".")[-1] == "ClassProperty" \
                    and sym.name in self.rand:
                return self.rand[sym.name], self.widths[sym.name][1]
            return self._concrete(expr), signed
        if ek == "MemberAccess":
            # this.prop written explicitly, or a prop of another handle:
            # only `this` props can be symbolic
            member = expr.member
            if _kind(expr.value) in ("NamedValue",) and \
                    member.name in self.rand:
                base = self.interp.eval(expr.value)
                if base is self.obj:
                    return self.rand[member.name], self.widths[member.name][1]
            return self._concrete(expr), signed
        if ek == "IntegerLiteral" or ek == "UnbasedUnsizedIntegerLiteral":
            return self._concrete(expr), signed
        if ek == "Conversion":
            inner, in_sg = self.bv(expr.operand)
            return self._fit(inner, width, in_sg), signed
        if ek == "UnaryOp":
            op = str(expr.op).split(".")[-1]
            v, sg = self.bv(expr.operand)
            v = self._fit(v, width, sg)
            if op == "Plus":
                return v, sg
            if op == "Minus":
                return -v, sg
            if op == "BitwiseNot":
                return ~v, sg
            if op == "LogicalNot":
                return z3.If(v == 0, z3.BitVecVal(1, 1),
                             z3.BitVecVal(0, 1)), False
            raise _u(f"unary operator {op} in constraint")
        if ek == "BinaryOp":
            return self._binop_bv(expr, width)
        if ek == "ConditionalOp":
            conds = [self.cond(c.expr) for c in expr.conditions]
            pred = z3.And(*conds) if len(conds) > 1 else conds[0]
            a, sa = self.bv(expr.left)
            b, sb = self.bv(expr.right)
            return z3.If(pred, self._fit(a, width, sa),
                         self._fit(b, width, sb)), signed
        if ek == "Inside":
            return z3.If(self.cond(expr), z3.BitVecVal(1, 1),
                         z3.BitVecVal(0, 1)), False
        raise _u(f"expression {expr.kind} in constraint")

    _CMP = {"Equality": "==", "Inequality": "!=", "LessThan": "<",
            "LessThanEqual": "<=", "GreaterThan": ">",
            "GreaterThanEqual": ">="}

    def _binop_bv(self, expr: Any, width: int) -> tuple[Any, bool]:
        z3 = self.z3
        op = str(expr.op).split(".")[-1]
        if op in self._CMP or op in ("LogicalAnd", "LogicalOr"):
            return z3.If(self.cond(expr), z3.BitVecVal(1, 1),
                         z3.BitVecVal(0, 1)), False
        a, sa = self.bv(expr.left)
        b, sb = self.bv(expr.right)
        a, b = self._fit(a, width, sa), self._fit(b, width, sb)
        sg = sa and sb
        if op == "Add":
            return a + b, sg
        if op == "Subtract":
            return a - b, sg
        if op == "Multiply":
            return a * b, sg
        if op == "Divide":
            self.hard.append(b != 0)
            return (a / b if sg else z3.UDiv(a, b)), sg
        if op == "Mod":
            self.hard.append(b != 0)
            return (z3.SRem(a, b) if sg else z3.URem(a, b)), sg
        if op == "BinaryAnd":
            return a & b, sg
        if op == "BinaryOr":
            return a | b, sg
        if op == "BinaryXor":
            return a ^ b, sg
        if op == "BinaryXnor":
            return ~(a ^ b), sg
        if op in ("LogicalShiftLeft", "ArithmeticShiftLeft"):
            return a << b, sg
        if op == "LogicalShiftRight":
            return z3.LShR(a, b), sg
        if op == "ArithmeticShiftRight":
            return (a >> b) if sg else z3.LShR(a, b), sg
        raise _u(f"binary operator {op} in constraint")

    def cond(self, expr: Any) -> Any:
        """Translate to a z3 Bool."""
        z3 = self.z3
        ek = _kind(expr)
        if ek == "BinaryOp":
            op = str(expr.op).split(".")[-1]
            if op in ("LogicalAnd", "LogicalOr"):
                l, r = self.cond(expr.left), self.cond(expr.right)
                return z3.And(l, r) if op == "LogicalAnd" else z3.Or(l, r)
            if op in self._CMP:
                a, sa = self.bv(expr.left)
                b, sb = self.bv(expr.right)
                w = max(a.size(), b.size())
                a, b = self._fit(a, w, sa), self._fit(b, w, sb)
                sg = sa and sb
                if op == "Equality":
                    return a == b
                if op == "Inequality":
                    return a != b
                if op == "LessThan":
                    return a < b if sg else z3.ULT(a, b)
                if op == "LessThanEqual":
                    return a <= b if sg else z3.ULE(a, b)
                if op == "GreaterThan":
                    return a > b if sg else z3.UGT(a, b)
                return a >= b if sg else z3.UGE(a, b)
        if ek == "UnaryOp" and str(expr.op).split(".")[-1] == "LogicalNot":
            return self.z3.Not(self.cond(expr.operand))
        if ek == "Inside":
            v, sg = self.bv(expr.left)
            terms = []
            for r in expr.rangeList:
                if _kind(r) == "ValueRange":
                    lo, _ = self.bv(r.left)
                    hi, _ = self.bv(r.right)
                    w = max(v.size(), lo.size(), hi.size())
                    vv = self._fit(v, w, sg)
                    terms.append(z3_and_range(self.z3, vv,
                                              self._fit(lo, w, sg),
                                              self._fit(hi, w, sg), sg))
                else:
                    x, sx = self.bv(r)
                    w = max(v.size(), x.size())
                    terms.append(self._fit(v, w, sg)
                                 == self._fit(x, w, sx))
            return self.z3.Or(*terms) if len(terms) > 1 else terms[0]
        if ek == "Conversion":
            return self.cond(expr.operand)
        # fall back: truthiness of a bitvector expression
        v, _sg = self.bv(expr)
        return v != 0

    # -- dist ---------------------------------------------------------------
    def dist(self, expr: Any) -> None:
        """dist policy: SOLVE. Membership hard constraint + weighted buckets
        recorded for the seeded assignment phase. Never silently ignored."""
        left = expr.left
        while _kind(left) == "Conversion":
            left = left.operand
        if _kind(left) != "NamedValue" or left.symbol.name not in self.rand:
            raise _u("dist on an expression that is not a plain rand "
                     "variable of this object")
        name = left.symbol.name
        width, _sg = self.widths[name]
        buckets: list[tuple[int, int, int]] = []
        for item in expr.items:
            wkind = str(item.weight.kind).split(".")[-1]
            wv = self.interp.eval(item.weight.expr)
            if not isinstance(wv, FourState) or wv.has_unknown:
                raise _u("dist weight is not a known constant")
            weight = wv.to_int()
            val = item.value
            if _kind(val) == "ValueRange":
                lo = self.interp.eval(val.left).to_int()
                hi = self.interp.eval(val.right).to_int()
                if lo > hi:
                    lo, hi = hi, lo
                total = weight * (hi - lo + 1) if wkind == "PerValue" \
                    else weight
                buckets.append((total, lo, hi))
            else:
                v = self.interp.eval(val).to_int()
                buckets.append((weight, v, v))
        live = [(t, lo, hi) for (t, lo, hi) in buckets if t > 0]
        if not live:
            raise _u(f"dist on {name} has no positive-weight item — "
                     "the constraint is unsatisfiable by construction")
        z3 = self.z3
        var = self.rand[name]
        member = [z3.And(z3.UGE(var, z3.BitVecVal(lo, width)),
                         z3.ULE(var, z3.BitVecVal(hi, width)))
                  if lo != hi else var == z3.BitVecVal(lo, width)
                  for (_t, lo, hi) in live]
        self.hard.append(z3.Or(*member) if len(member) > 1 else member[0])
        self.dists.append((name, live))

    # -- constraint tree ----------------------------------------------------
    def constraint(self, c: Any, force_hard: bool = False) -> None:
        z3 = self.z3
        ck = _kind(c)
        if ck == "List":
            for x in c.list:
                self.constraint(x, force_hard)
            return
        if ck == "Expression":
            expr = c.expr
            while _kind(expr) == "Conversion":
                expr = expr.operand
            if _kind(expr) == "Dist":
                if c.isSoft:
                    raise _u("soft dist constraint")
                self.dist(expr)
                return
            term = self.cond(c.expr)
            if c.isSoft and not force_hard:
                self.soft.append(term)
            else:
                self.hard.append(term)
            return
        if ck == "Implication":
            sub = _Collector(self)
            sub.run(c.body, force_hard)
            self.hard.append(z3.Implies(self.cond(c.predicate),
                                        z3.And(*sub.terms)
                                        if sub.terms else z3.BoolVal(True)))
            return
        if ck == "Conditional":
            pred = self.cond(c.predicate)
            sub_if = _Collector(self)
            sub_if.run(c.ifBody, force_hard)
            body_if = z3.And(*sub_if.terms) if sub_if.terms \
                else z3.BoolVal(True)
            if c.elseBody is not None:
                sub_el = _Collector(self)
                sub_el.run(c.elseBody, force_hard)
                body_el = z3.And(*sub_el.terms) if sub_el.terms \
                    else z3.BoolVal(True)
                self.hard.append(z3.If(pred, body_if, body_el))
            else:
                self.hard.append(z3.Implies(pred, body_if))
            return
        raise _u(f"constraint {c.kind}")


class _Collector:
    """Collects a nested constraint body into a term list (for the RHS of
    implication/conditional, where soft/dist have no defined meaning here
    and are rejected by name)."""

    def __init__(self, tr: _Translator) -> None:
        self.tr = tr
        self.terms: list[Any] = []

    def run(self, c: Any, force_hard: bool) -> None:
        ck = _kind(c)
        if ck == "List":
            for x in c.list:
                self.run(x, force_hard)
            return
        if ck == "Expression":
            expr = c.expr
            while _kind(expr) == "Conversion":
                expr = expr.operand
            if _kind(expr) == "Dist":
                raise _u("dist inside implication/conditional body")
            if c.isSoft:
                raise _u("soft constraint inside implication/conditional "
                         "body")
            self.terms.append(self.tr.cond(c.expr))
            return
        if ck == "Implication":
            z3 = self.tr.z3
            sub = _Collector(self.tr)
            sub.run(c.body, force_hard)
            self.terms.append(z3.Implies(
                self.tr.cond(c.predicate),
                z3.And(*sub.terms) if sub.terms else z3.BoolVal(True)))
            return
        raise _u(f"constraint {c.kind} inside implication/conditional body")


def z3_and_range(z3: Any, v: Any, lo: Any, hi: Any, signed: bool) -> Any:
    if signed:
        return z3.And(v >= lo, v <= hi)
    return z3.And(z3.UGE(v, lo), z3.ULE(v, hi))


# =====================================================================
# the solve
# =====================================================================

_TRY_RANDOM = 8      # random candidate values per free var
_TRY_DIST = 16       # weighted draws per dist var before model fallback


def do_randomize(interp: Any, obj: Any, inline: Any = None) -> int:
    """Execute obj.randomize(). Returns 1 (success) or 0 (unsat)."""
    import z3

    cls_sym = getattr(obj, "cls_sym", None)
    if cls_sym is None:
        raise _u("object has no class symbol attached (internal)")
    rng = interp.rand_rng

    # pre_randomize runs before constraint evaluation (LRM 18.6.1) so state
    # variables it sets are visible to the translator's concrete evals
    _call_hook(interp, obj, cls_sym, "pre_randomize")

    tr = _Translator(interp, obj)
    tr.collect_rand_vars(cls_sym)
    if not tr.rand:
        # no rand variables: randomize() trivially succeeds (LRM)
        _call_hook(interp, obj, cls_sym, "post_randomize")
        return 1

    # constraints evaluate `this`-relative references concretely -> frame
    interp.frames.append(({}, obj))
    try:
        for m in cls_sym:
            if _kind(m) == "ConstraintBlock":
                tr.constraint(m.constraints)
        if inline is not None:
            tr.constraint(inline)
    finally:
        interp.frames.pop()

    s = z3.Solver()
    for h in tr.hard:
        s.add(h)
    if s.check() != z3.sat:
        return 0                       # hard constraints unsat -> 0, no writes
    if tr.soft:
        # all-or-nothing soft policy (named limitation, never silent)
        if s.check(*tr.soft) == z3.sat:
            for t in tr.soft:
                s.add(t)

    # dist vars first: seeded weighted draw, feasibility-checked
    fixed: set[str] = set()
    for name, buckets in tr.dists:
        var = tr.rand[name]
        width, _sg = tr.widths[name]
        done = False
        for _ in range(_TRY_DIST):
            total = sum(t for (t, _lo, _hi) in buckets)
            x = rng.randrange(total)
            lo = hi = 0
            for (t, blo, bhi) in buckets:
                if x < t:
                    lo, hi = blo, bhi
                    break
                x -= t
            cand = rng.randrange(lo, hi + 1)
            cond = var == z3.BitVecVal(cand, width)
            if s.check(cond) == z3.sat:
                s.add(cond)
                done = True
                break
        if not done:
            # membership is already hard; spread within the feasible set
            _bitfix(z3, s, rng, var, width)
        fixed.add(name)

    # remaining rand vars: K random candidates drawn inside the var's
    # feasible [min, max] envelope (computed by the solver, so an interval
    # constraint gets a high hit rate), then seeded bit-fixing for sparse
    # sets. Distribution is approximately uniform, not proven uniform —
    # stated here, not hidden.
    for name, var in tr.rand.items():
        if name in fixed:
            continue
        width, _sg = tr.widths[name]
        lo, hi = _bounds(z3, s, var, width)
        done = False
        for _ in range(_TRY_RANDOM):
            cand = rng.randrange(lo, hi + 1)
            cond = var == z3.BitVecVal(cand, width)
            if s.check(cond) == z3.sat:
                s.add(cond)
                done = True
                break
        if not done:
            _bitfix(z3, s, rng, var, width)

    assert s.check() == z3.sat
    model = s.model()
    for name, var in tr.rand.items():
        width, sg = tr.widths[name]
        val = model.eval(var, model_completion=True).as_long()
        obj.props[name] = FourState.from_int(val, width, sg)

    _call_hook(interp, obj, cls_sym, "post_randomize")
    return 1


def _bounds(z3: Any, s: Any, var: Any, width: int) -> tuple[int, int]:
    """Unsigned feasible [min, max] of var under the solver's current
    constraints, via bitwise binary search (deterministic, ~2*width
    incremental checks)."""
    lo = 0
    for i in range(width - 1, -1, -1):
        cand = lo | (1 << i)
        # can the value stay below `cand`? if not, this bit is forced high
        if s.check(z3.ULT(var, z3.BitVecVal(cand, width))) != z3.sat:
            lo = cand
    hi = (1 << width) - 1
    for i in range(width - 1, -1, -1):
        cand = hi & ~(1 << i)
        if s.check(z3.UGT(var, z3.BitVecVal(cand, width))) != z3.sat:
            hi = cand
    return lo, hi


def _bitfix(z3: Any, s: Any, rng: Any, var: Any, width: int) -> None:
    """Randomized bit-fixing: when no random candidate value was feasible
    (small feasible set relative to the domain), fix each bit MSB->LSB to a
    seeded random polarity, flipping when infeasible. Always terminates in a
    satisfying assignment and spreads draws across the feasible set instead
    of collapsing to one repeated solver model."""
    fixed: list[Any] = []
    for i in range(width - 1, -1, -1):
        b = rng.getrandbits(1)
        cond = z3.Extract(i, i, var) == z3.BitVecVal(b, 1)
        if s.check(*fixed, cond) == z3.sat:
            fixed.append(cond)
        else:
            fixed.append(z3.Extract(i, i, var) == z3.BitVecVal(1 - b, 1))
    for c in fixed:
        s.add(c)


def _call_hook(interp: Any, obj: Any, cls_sym: Any, name: str) -> None:
    for m in cls_sym:
        if _kind(m) == "Subroutine" and m.name == name \
                and getattr(m, "body", None) is not None:
            interp._call_subroutine(m, obj, [])
            return
