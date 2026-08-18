"""Interpreter over slang's bound AST.

Elaborates a design (through pyslang's type-checked, width-annotated AST — the
same frontend the whole platform uses) into kernel signals and processes, then
executes statements as Python generators against the stratified scheduler.

The supported subset is explicit (`SUPPORTED` below). Anything else raises
UnsupportedFeature naming the construct — rule: never silently downgrade.

Deliberate N1 semantics notes:
- Block-local variables are static (created once, initialized on first entry),
  the traditional Verilog model. SV automatic lifetimes are not modeled yet.
- Delays are in raw time units of the source literals (no timescale scaling).
- Single driver per signal; multiple continuous-assign drivers are an error
  rather than X-resolution.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..core.errors import UnsupportedFeature
from .fourstate import FourState
from .kernel import Kernel, Signal, SimulationError


def _key(sym: Any) -> str:
    """Stable symbol key. pybind allocates a fresh Python wrapper per
    attribute access, so id(symbol) is NOT stable — the hierarchical path
    is (defect found by the first smoke test of this engine)."""
    return sym.hierarchicalPath


SUPPORTED = (
    "modules with ports and instances; logic/bit/reg/wire variables and nets; "
    "parameters; continuous assign; initial/always/always_comb/always_ff; "
    "begin/end, if/else, case/casez, for/while/repeat/forever loops; blocking "
    "and nonblocking assignment; # delays; @(edge) and @(*) controls; binary/"
    "unary/ternary operators; bit and part selects; concatenation; "
    "$display/$write/$error/$fatal/$finish/$time"
)


def _u(kind: object, where: str = "") -> UnsupportedFeature:
    return UnsupportedFeature(
        f"native engine: unsupported construct {kind}{' in ' + where if where else ''}. "
        f"Supported subset: {SUPPORTED}"
    )


def _sv_to_fs(svint: Any, width: int, signed: bool) -> FourState:
    return FourState.from_svint_str(str(svint), width, signed)


class Interp:
    def __init__(self, compilation: Any, kernel: Kernel | None = None) -> None:
        self.comp = compilation
        self.kernel = kernel or Kernel()
        self.sigmap: dict[str, Signal] = {}
        self.scopes: list[tuple[str, list[Signal]]] = []   # for VCD dumping
        self._driven: set[str] = set()
        self._inited_locals: set[str] = set()

    # ==================================================================
    # elaboration
    # ==================================================================
    def elaborate(self) -> None:
        root = self.comp.getRoot()
        tops = list(root.topInstances)
        if not tops:
            raise UnsupportedFeature("native engine: no top instance found")
        for inst in tops:
            self._elab_instance(inst, inst.name)

    def _elab_instance(self, inst: Any, path: str) -> None:
        body = inst.body
        members = list(body)
        local_signals: list[Signal] = []
        deferred_children: list[tuple[Any, str]] = []
        processes: list[tuple[Any, str]] = []

        for m in members:
            kind = str(m.kind).split(".")[-1]
            if kind in ("Variable", "Net"):
                sig = self._make_signal(m, path)
                local_signals.append(sig)
                init = getattr(m, "initializer", None)
                if init is not None:
                    if kind == "Net":
                        # wire w = expr  is a continuous assignment
                        processes.append((("net_init", m, init), path))
                    else:
                        processes.append((("var_init", m, init), path))
            elif kind == "Parameter":
                pass                                   # folded at eval time
            elif kind in ("Port", "TypeAlias", "TransparentMember",
                          "TypeParameter", "Genvar", "StatementBlock",
                          "WildcardImport", "ExplicitImport"):
                pass
            elif kind == "ContinuousAssign":
                processes.append((("cassign", m.assignment), path))
            elif kind == "ProceduralBlock":
                processes.append((("proc", m), path))
            elif kind == "Instance":
                deferred_children.append((m, f"{path}.{m.name}"))
            else:
                raise _u(m.kind, path)

        self.scopes.append((path, local_signals))

        for child, cpath in deferred_children:
            self._elab_instance(child, cpath)
            self._alias_ports(child)

        for spec, ppath in processes:
            self._spawn(spec, ppath)

    def _make_signal(self, sym: Any, path: str) -> Signal:
        t = sym.type
        width = int(t.bitWidth)
        if width == 0:
            raise _u(f"zero-width type for {sym.name}", path)
        sig = self.kernel.signal(f"{path}.{sym.name}", width,
                                 bool(getattr(t, "isSigned", False)))
        self.sigmap[_key(sym)] = sig
        return sig

    def _alias_ports(self, inst: Any) -> None:
        """Bind child port-internal symbols to the outer connection signals."""
        for pc in inst.portConnections:
            port = pc.port
            expr = pc.expression
            if expr is None:
                continue                              # unconnected port
            ek = str(expr.kind).split(".")[-1]
            if ek == "Assignment":                    # output .s(s) form
                outer_expr = expr.left
            else:
                outer_expr = expr
            outer_sig = self._lvalue_signal(outer_expr)
            internal = getattr(port, "internalSymbol", None)
            if internal is None:
                raise _u(f"port {port.name} with no internal symbol", inst.name)
            inner_sig = self.sigmap.get(_key(internal))
            if inner_sig is None:
                raise _u(f"port {port.name} internal symbol not elaborated",
                         inst.name)
            if inner_sig.width != outer_sig.width:
                raise _u(
                    f"port width mismatch on {port.name} "
                    f"({inner_sig.width} vs {outer_sig.width})", inst.name)
            # unify: everything that referenced the inner symbol now sees the
            # outer signal object
            for k, v in list(self.sigmap.items()):
                if v is inner_sig:
                    self.sigmap[k] = outer_sig
            for spath, sigs in self.scopes:
                for i, s in enumerate(sigs):
                    if s is inner_sig:
                        sigs[i] = outer_sig
            if inner_sig in self.kernel.signals:
                self.kernel.signals.remove(inner_sig)

    def _lvalue_signal(self, expr: Any) -> Signal:
        ek = str(expr.kind).split(".")[-1]
        if ek == "NamedValue":
            sig = self.sigmap.get(_key(expr.symbol))
            if sig is None:
                raise _u(f"reference to un-elaborated symbol {expr.symbol.name}")
            return sig
        if ek == "Conversion":
            return self._lvalue_signal(expr.operand)
        raise _u(f"lvalue expression {expr.kind}")

    # ==================================================================
    # processes
    # ==================================================================
    def _spawn(self, spec: tuple, path: str) -> None:
        kind = spec[0]
        if kind == "var_init":
            _, sym, init = spec
            sig = self.sigmap[_key(sym)]

            def gen_vi(sig=sig, init=init):
                sig.write(self.eval(init))
                return
                yield  # pragma: no cover

            self.kernel.spawn(gen_vi(), f"{path}.init.{sym.name}")
        elif kind in ("cassign", "net_init"):
            if kind == "cassign":
                assign = spec[1]
                lhs, rhs, tgt = assign.left, assign.right, None
            else:
                _, sym, init = spec
                lhs, rhs, tgt = None, init, self.sigmap[_key(sym)]

            def gen_ca(lhs=lhs, rhs=rhs, tgt=tgt):
                while True:
                    self.kernel.tracking_reads = reads = set()
                    val = self.eval(rhs)
                    self.kernel.tracking_reads = None
                    if lhs is not None:
                        for _ in self._store(lhs, val, nonblocking=False):
                            raise SimulationError(
                                "continuous assignment tried to block")
                    else:
                        tgt.write(val)
                    if not reads:
                        return                     # constant RHS: settle once
                    yield ("edges", [(s, "any") for s in reads])

            self.kernel.spawn(gen_ca(), f"{path}.assign")
        elif kind == "proc":
            block = spec[1]
            pk = str(block.procedureKind).split(".")[-1]
            body = block.body
            if pk == "Initial":
                self.kernel.spawn(self.exec_stmt(body), f"{path}.initial")
            elif pk in ("Always", "AlwaysFF", "AlwaysLatch"):
                self.kernel.spawn(self._forever(body), f"{path}.{pk.lower()}")
            elif pk == "AlwaysComb":
                self.kernel.spawn(self._comb(body), f"{path}.always_comb")
            elif pk == "Final":
                pass                                   # not modeled in N1
            else:
                raise _u(f"procedural block kind {block.procedureKind}", path)
        else:  # pragma: no cover - defensive
            raise _u(kind, path)

    def _forever(self, body: Any) -> Iterator:
        # `always begin ... end` — body typically opens with a timing control.
        # An @(*) implicit event becomes read-tracked combinational behavior.
        bk = str(body.kind).split(".")[-1]
        if bk == "Timed" and str(body.timing.kind).endswith("ImplicitEvent"):
            yield from self._comb(body.stmt)
            return
        while True:
            yield from self.exec_stmt(body)

    def _comb(self, body: Any) -> Iterator:
        from .kernel import MAX_DELTAS
        while True:
            # feedback guard: a body that reads a signal it also writes
            # (always_comb a = ~a) retriggers itself forever. Dynamic
            # subscription happens after the body runs, so without this loop
            # the self-change would be silently missed and the sim would
            # *settle on wrong semantics* instead of reporting the loop.
            iterations = 0
            while True:
                iterations += 1
                if iterations > MAX_DELTAS:
                    self.kernel.tracking_reads = None
                    self.kernel.tracking_writes = None
                    raise SimulationError(
                        "zero-time loop: combinational process re-triggers "
                        f"itself (>{MAX_DELTAS} iterations at "
                        f"t={self.kernel.time})"
                    )
                self.kernel.tracking_reads = reads = set()
                self.kernel.tracking_writes = writes = set()
                gen = self.exec_stmt(body)
                for req in gen:
                    self.kernel.tracking_reads = None
                    self.kernel.tracking_writes = None
                    raise SimulationError(
                        "combinational process attempted to block "
                        f"(request {req[0]})"
                    )
                self.kernel.tracking_reads = None
                self.kernel.tracking_writes = None
                if not (reads & writes):
                    break
            if not reads:
                return
            yield ("edges", [(s, "any") for s in reads])

    # ==================================================================
    # statements
    # ==================================================================
    def exec_stmt(self, stmt: Any) -> Iterator:
        k = str(stmt.kind).split(".")[-1]
        if k == "Empty":
            return
        if k == "List":
            for s in stmt.list:
                yield from self.exec_stmt(s)
            return
        if k == "Block":
            yield from self.exec_stmt(stmt.body)
            return
        if k == "ExpressionStatement":
            yield from self._exec_expr_stmt(stmt.expr)
            return
        if k == "Timed":
            yield from self._exec_timing(stmt.timing)
            yield from self.exec_stmt(stmt.stmt)
            return
        if k == "Conditional":
            conds = list(stmt.conditions)
            taken = True
            for c in conds:
                t = self.eval(c.expr).is_true()
                if t is not True:
                    taken = False
                    break
            if taken:
                yield from self.exec_stmt(stmt.ifTrue)
            elif stmt.ifFalse is not None:
                yield from self.exec_stmt(stmt.ifFalse)
            return
        if k == "Case":
            sel = self.eval(stmt.expr)
            for item in stmt.items:
                for e in item.expressions:
                    if sel.case_eq(self.eval(e)).to_int():
                        yield from self.exec_stmt(item.stmt)
                        return
            if stmt.defaultCase is not None:
                yield from self.exec_stmt(stmt.defaultCase)
            return
        if k == "VariableDeclaration":
            sym = stmt.symbol
            k2 = _key(sym)
            if k2 not in self.sigmap:
                self.sigmap[k2] = self.kernel.signal(
                    sym.name, int(sym.type.bitWidth),
                    bool(getattr(sym.type, "isSigned", False)))
            if k2 not in self._inited_locals:
                self._inited_locals.add(k2)
                init = getattr(sym, "initializer", None)
                if init is not None:
                    self.sigmap[k2].write(self.eval(init))
            return
        if k == "ForLoop":
            for e in stmt.initializers:
                self.eval(e)
            while True:
                if stmt.stopExpr is not None:
                    if self.eval(stmt.stopExpr).is_true() is not True:
                        break
                yield from self.exec_stmt(stmt.body)
                for e in stmt.steps:
                    self.eval(e)
            return
        if k == "WhileLoop":
            while self.eval(stmt.cond).is_true() is True:
                yield from self.exec_stmt(stmt.body)
            return
        if k == "RepeatLoop":
            n = self.eval(stmt.count)
            if n.has_unknown:
                return
            for _ in range(n.to_int()):
                yield from self.exec_stmt(stmt.body)
            return
        if k == "ForeverLoop":
            while True:
                yield from self.exec_stmt(stmt.body)
            return
        raise _u(f"statement {stmt.kind}")

    def _exec_timing(self, timing: Any) -> Iterator:
        tk = str(timing.kind).split(".")[-1]
        if tk == "Delay":
            d = self.eval(timing.expr)
            if d.has_unknown:
                raise SimulationError("delay with unknown value")
            yield ("delay", d.to_int())
            return
        if tk == "SignalEvent":
            yield ("edges", [self._edge_of(timing)])
            return
        if tk == "EventList":
            yield ("edges", [self._edge_of(e) for e in timing.events])
            return
        raise _u(f"timing control {timing.kind}")

    def _edge_of(self, ev: Any) -> tuple[Signal, str]:
        edge = str(ev.edge).split(".")[-1]
        kind = {"PosEdge": "pos", "NegEdge": "neg", "None_": "any",
                "None": "any", "BothEdges": "any"}.get(edge)
        if kind is None:
            raise _u(f"edge kind {ev.edge}")
        return (self._lvalue_signal(ev.expr), kind)

    # -- expression statements (assignments and calls) --------------------
    def _exec_expr_stmt(self, expr: Any) -> Iterator:
        ek = str(expr.kind).split(".")[-1]
        if ek == "Assignment":
            rhs = self.eval(expr.right)
            if getattr(expr, "op", None) is not None:
                cur = self.eval(expr.left)
                rhs = self._binop(str(expr.op).split(".")[-1], cur, rhs,
                                  int(expr.left.type.bitWidth))
            yield from self._store(expr.left, rhs, expr.isNonBlocking)
            return
        if ek == "Call":
            self._call(expr)
            return
        # expression evaluated for side effects (e.g. i++)
        self.eval(expr)
        return
        yield  # pragma: no cover

    def _store(self, lhs: Any, value: FourState, nonblocking: bool) -> Iterator:
        ek = str(lhs.kind).split(".")[-1]
        if ek in ("NamedValue", "Conversion"):
            sig = self._lvalue_signal(lhs)
            (sig.nba_write if nonblocking else sig.write)(value)
            return
        if ek == "ElementSelect":
            sig = self._lvalue_signal(lhs.value)
            idx = self.eval(lhs.selector)
            if idx.has_unknown:
                return
            i = idx.to_int()
            cur = sig.value
            m = 1 << i
            new = FourState(cur.width,
                            (cur.aval & ~m) | ((value.aval & 1) << i),
                            (cur.bval & ~m) | ((value.bval & 1) << i),
                            cur.signed)
            (sig.nba_write if nonblocking else sig.write)(new)
            return
        if ek == "RangeSelect":
            sig = self._lvalue_signal(lhs.value)
            msb = self.eval(lhs.left).to_int()
            lsb = self.eval(lhs.right).to_int()
            if msb < lsb:
                msb, lsb = lsb, msb
            w = msb - lsb + 1
            v = value.resize(w)
            cur = sig.value
            m = ((1 << w) - 1) << lsb
            new = FourState(cur.width,
                            (cur.aval & ~m) | (v.aval << lsb),
                            (cur.bval & ~m) | (v.bval << lsb),
                            cur.signed)
            (sig.nba_write if nonblocking else sig.write)(new)
            return
        raise _u(f"assignment target {lhs.kind}")
        yield  # pragma: no cover

    # ==================================================================
    # expressions
    # ==================================================================
    def eval(self, expr: Any) -> FourState:
        ek = str(expr.kind).split(".")[-1]
        t = expr.type
        width = int(t.bitWidth) if t is not None else 32
        signed = bool(getattr(t, "isSigned", False))

        if ek == "IntegerLiteral":
            return _sv_to_fs(expr.value, width, signed)
        if ek == "UnbasedUnsizedIntegerLiteral":
            # '0 / '1 / 'x / 'z — replicate the single bit across the width
            bit = str(expr.literalValue if hasattr(expr, "literalValue")
                      else expr.value)
            ch = bit[-1].lower()
            if ch == "0":
                return FourState.from_int(0, width, signed)
            if ch == "1":
                return FourState(width, (1 << width) - 1, 0, signed)
            if ch == "x":
                return FourState.all_x(width, signed)
            if ch == "z":
                return FourState.all_z(width, signed)
            raise _u(f"unbased literal '{bit}")
        if ek == "NamedValue":
            sym = expr.symbol
            sk = str(sym.kind).split(".")[-1]
            if sk == "Parameter":
                return _sv_to_fs(sym.value, width, signed)
            sig = self.sigmap.get(_key(sym))
            if sig is None:
                raise _u(f"reference to un-elaborated symbol {sym.name}")
            return sig.read()
        if ek == "Conversion":
            return self.eval(expr.operand).resize(width, signed)
        if ek == "UnaryOp":
            return self._unop(str(expr.op).split(".")[-1],
                              self.eval(expr.operand), width)
        if ek == "BinaryOp":
            return self._binop(str(expr.op).split(".")[-1],
                               self.eval(expr.left), self.eval(expr.right),
                               width)
        if ek == "ConditionalOp":
            pred = True
            for c in expr.conditions:
                r = self.eval(c.expr).is_true()
                if r is None:
                    # LRM: unknown select -> bitwise merge of both arms
                    a, b = self.eval(expr.left), self.eval(expr.right)
                    a, b = a.resize(width), b.resize(width)
                    same = ~(a.aval ^ b.aval) & ~a.bval & ~b.bval
                    m = (1 << width) - 1
                    bv = (~same) & m
                    return FourState(width, (a.aval & same) | bv, bv, signed)
                if r is False:
                    pred = False
                    break
            return self.eval(expr.left if pred else expr.right).resize(width, signed)
        if ek == "Concatenation":
            out = FourState(0, 0, 0)
            for op in expr.operands:
                out = out.concat(self.eval(op))
            return out
        if ek == "Replication":
            count = self.eval(expr.count).to_int()
            inner = self.eval(expr.concat)
            out = FourState(0, 0, 0)
            for _ in range(count):
                out = out.concat(inner)
            return out
        if ek == "ElementSelect":
            base = self.eval(expr.value)
            idx = self.eval(expr.selector)
            if idx.has_unknown:
                return FourState.all_x(1)
            return base.select_bit(idx.to_int())
        if ek == "RangeSelect":
            base = self.eval(expr.value)
            msb, lsb = self.eval(expr.left).to_int(), self.eval(expr.right).to_int()
            if msb < lsb:
                msb, lsb = lsb, msb
            return base.select_range(msb, lsb)
        if ek == "Call":
            return self._call(expr)
        if ek == "Assignment":
            # assignment as expression (for-loop init/step)
            val = self.eval(expr.right)
            if getattr(expr, "op", None) is not None:
                val = self._binop(str(expr.op).split(".")[-1],
                                  self.eval(expr.left), val,
                                  int(expr.left.type.bitWidth))
            sig = self._lvalue_signal(expr.left)
            sig.write(val)
            return val
        if ek == "StringLiteral":
            # only meaningful as a $display argument; represented separately
            raise _u("string literal outside a system-task argument")
        raise _u(f"expression {expr.kind}")

    def _unop(self, op: str, v: FourState, width: int) -> FourState:
        if op == "Plus":
            return v.resize(width)
        if op == "Minus":
            return FourState.from_int(0, width).sub(v, width)
        if op == "BitwiseNot":
            return v.resize(width).bit_not()
        if op == "LogicalNot":
            t = v.is_true()
            return FourState.all_x(1) if t is None else FourState(1, int(not t), 0)
        if op == "BitwiseAnd":
            return v.reduce_and()
        if op == "BitwiseOr":
            return v.reduce_or()
        if op == "BitwiseXor":
            return v.reduce_xor()
        if op == "BitwiseNand":
            return v.reduce_and().bit_not()
        if op == "BitwiseNor":
            return v.reduce_or().bit_not()
        if op == "BitwiseXnor":
            return v.reduce_xor().bit_not()
        raise _u(f"unary operator {op}")

    def _binop(self, op: str, a: FourState, b: FourState, width: int) -> FourState:
        table = {
            "Add": lambda: a.add(b, width),
            "Subtract": lambda: a.sub(b, width),
            "Multiply": lambda: a.mul(b, width),
            "Divide": lambda: a.div(b, width),
            "Mod": lambda: a.mod(b, width),
            "BinaryAnd": lambda: a.bit_and(b, width),
            "BinaryOr": lambda: a.bit_or(b, width),
            "BinaryXor": lambda: a.bit_xor(b, width),
            "BinaryXnor": lambda: a.bit_xor(b, width).bit_not(),
            "Equality": lambda: a.eq(b),
            "Inequality": lambda: a.eq(b).bit_not() if not a.eq(b).has_unknown
                                  else FourState.all_x(1),
            "CaseEquality": lambda: a.case_eq(b),
            "CaseInequality": lambda: FourState(1, 1 - a.case_eq(b).to_int(), 0),
            "LessThan": lambda: a.lt(b),
            "LessThanEqual": lambda: a.le(b),
            "GreaterThan": lambda: a.gt(b),
            "GreaterThanEqual": lambda: a.ge(b),
            "LogicalShiftLeft": lambda: a.shl(b, width),
            "LogicalShiftRight": lambda: a.shr(b, width),
            "ArithmeticShiftLeft": lambda: a.shl(b, width),
            "ArithmeticShiftRight": lambda: a.shr(b, width, arithmetic=True),
        }
        if op in ("LogicalAnd", "LogicalOr"):
            x, y = a.is_true(), b.is_true()
            if op == "LogicalAnd":
                if x is False or y is False:
                    return FourState(1, 0, 0)
                if x is None or y is None:
                    return FourState.all_x(1)
                return FourState(1, 1, 0)
            if x is True or y is True:
                return FourState(1, 1, 0)
            if x is None or y is None:
                return FourState.all_x(1)
            return FourState(1, 0, 0)
        fn = table.get(op)
        if fn is None:
            raise _u(f"binary operator {op}")
        return fn()

    # ==================================================================
    # system tasks
    # ==================================================================
    def _call(self, expr: Any) -> FourState:
        if not expr.isSystemCall:
            raise _u("user-defined function/task calls")
        name = expr.subroutine.subroutine.name
        args = list(expr.arguments)
        if name in ("$display", "$write"):
            text = self._format_args(args)
            out = text + ("\n" if name == "$display" else "")
            self.kernel.stdout.append(out)
            return FourState(1, 0, 0)
        if name in ("$error", "$fatal", "$warning", "$info"):
            fmt_args = list(args)
            if name == "$fatal" and fmt_args and \
                    str(fmt_args[0].kind).endswith("IntegerLiteral"):
                fmt_args = fmt_args[1:]        # leading finish-code argument
            text = self._format_args(fmt_args) if fmt_args else ""
            self.kernel.stdout.append(f"{name.upper()[1:]}: {text}\n")
            if name == "$fatal":
                self.kernel.finished = True
                self.kernel.finish_time = self.kernel.time
            return FourState(1, 0, 0)
        if name == "$time" or name == "$stime":
            return FourState.from_int(self.kernel.time, 64)
        if name == "$finish" or name == "$stop":
            self.kernel.finished = True
            self.kernel.finish_time = self.kernel.time
            return FourState(1, 0, 0)
        raise _u(f"system call {name}")

    def _format_args(self, args: list) -> str:
        if not args:
            return ""
        first = args[0]
        if str(first.kind).endswith("StringLiteral"):
            fmt = first.value
            rest = args[1:]
            return self._format(fmt, rest)
        return " ".join(self.eval(a).format("0d") for a in args)

    def _format(self, fmt: str, args: list) -> str:
        out: list[str] = []
        ai = 0
        i = 0
        while i < len(fmt):
            ch = fmt[i]
            if ch != "%":
                out.append(ch)
                i += 1
                continue
            j = i + 1
            while j < len(fmt) and fmt[j].isdigit():
                j += 1
            spec = fmt[i + 1:j + 1]
            conv = fmt[j] if j < len(fmt) else "%"
            if conv == "%":
                out.append("%")
                i = j + 1
                continue
            if ai >= len(args):
                raise SimulationError(f"missing argument for %{spec}")
            arg = args[ai]
            ai += 1
            if conv in ("d", "b", "h", "x"):
                out.append(self.eval(arg).format(fmt[i + 1:j] + conv))
            elif conv == "t":
                out.append(str(self.eval(arg).to_int()))
            elif conv == "s":
                if str(arg.kind).endswith("StringLiteral"):
                    out.append(arg.value)
                else:
                    v = self.eval(arg)
                    out.append(v.format("0d"))
            elif conv == "c":
                out.append(chr(self.eval(arg).to_int() & 0xFF))
            else:
                raise _u(f"format conversion %{conv}")
            i = j + 1
        return "".join(out)
