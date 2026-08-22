"""Engine N5: functional covergroups on the native kernel.

Samples the bound slang covergroup AST: coverpoints with automatic or explicit
bins, `illegal_bins` / `ignore_bins`, and cross coverage, sampled on the
covergroup's clocking event or by an explicit `.sample()` call.

Policies (nothing silently dropped — the platform's rule):
- an X/Z sample hits no bin (LRM 19.5) and is counted as an unsampled value,
  never coerced to 0.
- `illegal_bins` hit is a runtime error surfaced as a named failure, not a
  silent coverage bin.
- `ignore_bins` are excluded from the coverage denominator.
- automatic bins follow `auto_bin_max` (default 64): a coverpoint of width w
  gets min(2**w, 64) auto bins partitioning its range.
- coverage% is covered/total bins per coverpoint, and the covergroup number
  is the unweighted mean of its coverpoints and crosses (option.weight and
  at_least>1 are NOT modeled — a named limitation, stated, not silent).
"""

from __future__ import annotations

from typing import Any

from .fourstate import FourState
from .kernel import SimulationError

AUTO_BIN_MAX = 64


def _kind(node: Any) -> str:
    return str(node.kind).split(".")[-1]


class Bin:
    __slots__ = ("name", "kind", "ranges", "hits")

    def __init__(self, name: str, kind: str,
                 ranges: list[tuple[int, int]]) -> None:
        self.name = name
        self.kind = kind                 # "bins" | "illegal" | "ignore"
        self.ranges = ranges             # inclusive (lo, hi)
        self.hits = 0

    def contains(self, v: int) -> bool:
        return any(lo <= v <= hi for lo, hi in self.ranges)


class Coverpoint:
    def __init__(self, name: str, expr: Any, width: int) -> None:
        self.name = name
        self.expr = expr                 # slang coverageExpr, eval'd at sample
        self.width = width
        self.bins: list[Bin] = []
        self.unsampled_x = 0

    # -- coverage accounting ----------------------------------------------
    def _countable(self) -> list[Bin]:
        return [b for b in self.bins if b.kind == "bins"]

    def coverage(self) -> tuple[int, int]:
        countable = self._countable()
        covered = sum(1 for b in countable if b.hits > 0)
        return covered, len(countable)

    def percent(self) -> float:
        cov, tot = self.coverage()
        return 100.0 * cov / tot if tot else 100.0

    # -- sampling ----------------------------------------------------------
    def sample(self, v: int) -> str | None:
        """Record a hit for value v. Returns the name of the first matching
        non-ignore bin (for crosses), or None. Raises on an illegal hit."""
        matched = None
        for b in self.bins:
            if b.contains(v):
                if b.kind == "illegal":
                    raise SimulationError(
                        f"illegal_bins '{self.name}.{b.name}' hit by value "
                        f"{v} — illegal coverage bin reached")
                b.hits += 1
                if b.kind == "bins" and matched is None:
                    matched = b.name
        return matched


class Cross:
    def __init__(self, name: str, points: list[str]) -> None:
        self.name = name
        self.points = points             # coverpoint names
        self.hits: dict[tuple, int] = {}
        self._total: int | None = None

    def set_total(self, total: int) -> None:
        self._total = total

    def sample(self, bin_names: list[str | None]) -> None:
        if any(b is None for b in bin_names):
            return                        # every axis must land in a real bin
        key = tuple(bin_names)
        self.hits[key] = self.hits.get(key, 0) + 1

    def coverage(self) -> tuple[int, int]:
        covered = len(self.hits)
        return covered, (self._total or covered)

    def percent(self) -> float:
        cov, tot = self.coverage()
        return 100.0 * cov / tot if tot else 100.0


class NativeCovergroup:
    """One covergroup instance built from a slang CovergroupType."""

    def __init__(self, interp: Any, cg_type: Any, inst_name: str) -> None:
        self.interp = interp
        self.name = inst_name
        self.coverpoints: list[Coverpoint] = []
        self.crosses: list[Cross] = []
        self.event: tuple[Any, str] | None = None
        self._build(cg_type)

    # -- build -------------------------------------------------------------
    def _build(self, cg_type: Any) -> None:
        interp = self.interp
        # sampling event (may be absent -> sample-only covergroup)
        ev = getattr(cg_type, "coverageEvent", None)
        if ev is not None and _kind(ev) == "SignalEvent":
            self.event = interp._edge_of(ev)
        elif ev is not None:
            from ..core.errors import UnsupportedFeature
            raise UnsupportedFeature(
                f"native covergroup: sampling control {ev.kind} "
                "(only @(edge signal) or explicit .sample())")

        cps_by_name: dict[str, Coverpoint] = {}
        for m in cg_type.body:
            mk = _kind(m)
            if mk == "Coverpoint":
                cp = self._build_coverpoint(m)
                self.coverpoints.append(cp)
                cps_by_name[cp.name] = cp
            elif mk == "CoverCross":
                names = [t.name for t in m.targets]
                self.crosses.append(Cross(m.name or "x", names))
        # cross totals = product of each axis's countable bins
        for cx in self.crosses:
            total = 1
            for pname in cx.points:
                cp = cps_by_name.get(pname)
                if cp is None:
                    total = 0
                    break
                total *= max(1, len(cp._countable()))
            cx.set_total(total)

    def _build_coverpoint(self, cp_sym: Any) -> Coverpoint:
        width = int(cp_sym.coverageExpr.type.bitWidth)
        cp = Coverpoint(cp_sym.name, cp_sym.coverageExpr, width)
        explicit = [m for m in cp_sym if _kind(m) == "CoverageBin"]
        if explicit:
            for b in explicit:
                cp.bins.extend(self._build_bins(b))
        else:
            cp.bins = self._auto_bins(width)
        return cp

    def _build_bins(self, b_sym: Any) -> list[Bin]:
        from ..core.errors import UnsupportedFeature
        if bool(getattr(b_sym, "isWildcard", False)):
            raise UnsupportedFeature(
                f"native covergroup: wildcard bins ('{b_sym.name}') — X/Z "
                "don't-care matching is not modeled in N5")
        values = list(b_sym.values)
        if not values:
            # a transition bin `(a => b)` has no value set — never silently
            # treat it as an empty (unhittable) bin
            raise UnsupportedFeature(
                f"native covergroup: transition bins ('{b_sym.name}', "
                "`a => b`) are not modeled in N5")
        kind = {"Bins": "bins", "IllegalBins": "illegal",
                "IgnoreBins": "ignore"}.get(
            str(b_sym.binsKind).split(".")[-1], "bins")
        ranges: list[tuple[int, int]] = []
        for expr in values:
            if _kind(expr) == "ValueRange":
                lo = self.interp.eval(expr.left).to_int()
                hi = self.interp.eval(expr.right).to_int()
                if lo > hi:
                    lo, hi = hi, lo
                ranges.append((lo, hi))
            else:
                v = self.interp.eval(expr).to_int()
                ranges.append((v, v))
        if b_sym.isArray:
            # `bins x[] = {...}` : one bin per distinct value in the set
            out: list[Bin] = []
            seen: set[int] = set()
            for lo, hi in ranges:
                for val in range(lo, hi + 1):
                    if val not in seen:
                        seen.add(val)
                        out.append(Bin(f"{b_sym.name}[{val}]", kind,
                                       [(val, val)]))
            return out
        return [Bin(b_sym.name, kind, ranges)]

    @staticmethod
    def _auto_bins(width: int) -> list[Bin]:
        domain = 1 << width
        n = min(domain, AUTO_BIN_MAX)
        bins: list[Bin] = []
        # partition [0, domain) into n contiguous bins (LRM auto_bin_max)
        step = domain // n
        rem = domain % n
        lo = 0
        for i in range(n):
            size = step + (1 if i < rem else 0)
            hi = lo + size - 1
            bins.append(Bin(f"auto[{lo}:{hi}]" if size > 1 else f"auto[{lo}]",
                            "bins", [(lo, hi)]))
            lo = hi + 1
        return bins

    # -- sample ------------------------------------------------------------
    def sample(self) -> None:
        matched: list[str | None] = []
        cp_hit: dict[str, str | None] = {}
        for cp in self.coverpoints:
            fs = self.interp.eval(cp.expr)
            if not isinstance(fs, FourState) or fs.has_unknown:
                cp.unsampled_x += 1
                cp_hit[cp.name] = None
                continue
            cp_hit[cp.name] = cp.sample(fs.to_int())
        for cx in self.crosses:
            cx.sample([cp_hit.get(p) for p in cx.points])

    # -- report ------------------------------------------------------------
    def report(self) -> dict:
        cps = {cp.name: {"percent": round(cp.percent(), 2),
                         "covered": cp.coverage()[0],
                         "total": cp.coverage()[1],
                         "bins": {b.name: b.hits for b in cp.bins},
                         "unsampled_x": cp.unsampled_x}
               for cp in self.coverpoints}
        crosses = {cx.name: {"percent": round(cx.percent(), 2),
                             "covered": cx.coverage()[0],
                             "total": cx.coverage()[1]}
                   for cx in self.crosses}
        parts = [cp.percent() for cp in self.coverpoints] + \
                [cx.percent() for cx in self.crosses]
        overall = sum(parts) / len(parts) if parts else 100.0
        return {"name": self.name, "overall": round(overall, 2),
                "coverpoints": cps, "crosses": crosses}
