"""Coverage model and Verilator coverage-database reader.

The model is UCIS-shaped in structure (scope tree -> coverpoints -> bins with
counts) so that a UCIS export can be added without reshaping the data. It is
deliberately backend-neutral: `VerilatorCoverageReader` is the only part that
knows what a Verilator `coverage.dat` looks like.

Measured behaviour on Verilator 5.050 that this module exists to work around:
covergroup bins ARE recorded in coverage.dat and reported by
`verilator_coverage`, but the in-language `covergroup::get_coverage()` returns
0.00. Reading the database is therefore the only trustworthy source of
functional coverage on this backend.
"""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..plugins.interfaces import FeatureStatus

# Verilator coverage.dat line:  C '<tagged record>' <count>
_LINE_RE = re.compile(r"^C\s+'(.*)'\s+(-?\d+)\s*$")

# Inside the quotes, Verilator writes a delimited key/value stream:
#
#   \x01<key>\x02<value>  repeated
#
# e.g. \x01t\x02covergroup \x01page\x02v_covergroup/cg_t \x01f\x02s.sv
#      \x01l\x025 \x01n\x0229 \x01bin\x02lo \x01h\x02cg_t.cp.lo
#
# The delimiters are non-printing, which is why the format looks ambiguous in a
# terminal but is not. Keys observed: f(ile) l(ine) n(column) t(ype) page
# o(comment) bin h(ierarchy). Key order differs between covergroup and
# code-coverage records, so parse into a dict and never rely on position.
_KEY_SEP = "\x01"
_VAL_SEP = "\x02"

# Pre-5.x releases wrote an undelimited form; kept as a fallback so older
# databases still load rather than silently yielding zero coverage.
_LEGACY_RE = re.compile(
    r"^f(?P<file>.*?)l(?P<line>\d+)n(?P<col>\d+)"
    r"t(?P<t>line|branch|expr|toggle|fsm_state|fsm_arc|covergroup|user)"
    r"page(?P<page>.*?)o(?P<comment>.*)h(?P<hier>[^h]*)$"
)


class CoverageKind:
    LINE = "line"
    BRANCH = "branch"
    EXPR = "expr"
    TOGGLE = "toggle"
    FSM_STATE = "fsm_state"
    FSM_ARC = "fsm_arc"
    COVERGROUP = "covergroup"
    UNKNOWN = "unknown"


@dataclass
class CoverBinResult:
    name: str
    count: int
    kind: str = CoverageKind.UNKNOWN
    file: str = ""
    line: int = 0
    hierarchy: str = ""
    comment: str = ""

    @property
    def covered(self) -> bool:
        return self.count > 0


@dataclass
class CoverageDB:
    """Flat bin list plus derived groupings. Merge-friendly by construction."""

    bins: list[CoverBinResult] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    # -- aggregation -------------------------------------------------------
    def by_kind(self) -> dict[str, list[CoverBinResult]]:
        out: dict[str, list[CoverBinResult]] = defaultdict(list)
        for b in self.bins:
            out[b.kind].append(b)
        return dict(out)

    def score(self, kind: str | None = None) -> tuple[int, int, float]:
        """(covered, total, percent) for one kind, or overall when kind is None."""
        items = self.bins if kind is None else self.by_kind().get(kind, [])
        total = len(items)
        covered = sum(1 for b in items if b.covered)
        pct = (100.0 * covered / total) if total else 0.0
        return covered, total, round(pct, 2)

    def functional_score(self) -> tuple[int, int, float]:
        """Functional coverage = covergroup bins only.

        Line/branch/expr coverage is code coverage and must never be reported
        as functional coverage; conflating them overstates closure.
        """
        return self.score(CoverageKind.COVERGROUP)

    def holes(self, kind: str = CoverageKind.COVERGROUP) -> list[CoverBinResult]:
        return [b for b in self.by_kind().get(kind, []) if not b.covered]

    def summary(self) -> dict:
        kinds = {}
        for kind in (
            CoverageKind.COVERGROUP, CoverageKind.LINE, CoverageKind.BRANCH,
            CoverageKind.EXPR, CoverageKind.TOGGLE, CoverageKind.FSM_STATE,
            CoverageKind.FSM_ARC,
        ):
            cov, tot, pct = self.score(kind)
            if tot:
                kinds[kind] = {"covered": cov, "total": tot, "percent": pct}
        fcov, ftot, fpct = self.functional_score()
        return {
            "sources": self.sources,
            "functional": {"covered": fcov, "total": ftot, "percent": fpct},
            "by_kind": kinds,
            "holes": [
                {
                    "name": h.name, "kind": h.kind, "file": h.file,
                    "line": h.line, "hierarchy": h.hierarchy,
                    "comment": h.comment,
                }
                for h in self.holes()[:200]
            ],
        }

    # -- merging -----------------------------------------------------------
    @staticmethod
    def merge(dbs: list["CoverageDB"]) -> "CoverageDB":
        """Union of bins with summed counts — the standard merge semantics.

        A bin covered in any run is covered in the merge; counts add so that
        seed-contribution analysis stays possible.
        """
        acc: dict[tuple, CoverBinResult] = {}
        sources: list[str] = []
        for db in dbs:
            sources.extend(db.sources)
            for b in db.bins:
                key = (b.kind, b.hierarchy, b.file, b.line, b.name, b.comment)
                if key in acc:
                    acc[key].count += b.count
                else:
                    acc[key] = CoverBinResult(**vars(b))
        return CoverageDB(bins=list(acc.values()), sources=sources)


class VerilatorCoverageReader:
    """Parses Verilator's `coverage.dat` into the neutral CoverageDB model."""

    name = "verilator"

    def capabilities(self) -> dict[str, FeatureStatus]:
        return {
            "read_verilator_dat": FeatureStatus.SUPPORTED,
            "covergroup_bins": FeatureStatus.SUPPORTED,
            "line_coverage": FeatureStatus.SUPPORTED,
            "branch_coverage": FeatureStatus.SUPPORTED,
            "expression_coverage": FeatureStatus.SUPPORTED,
            "toggle_coverage": FeatureStatus.SUPPORTED,
            "merge": FeatureStatus.SUPPORTED,
            "hole_report": FeatureStatus.SUPPORTED,
            "exclusions_waivers": FeatureStatus.PLANNED,
            "ucis_export": FeatureStatus.PLANNED,
            "cross_coverage_grouping": FeatureStatus.PARTIALLY_SUPPORTED,
        }

    def load(self, path: Path) -> CoverageDB:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"coverage database not found: {path}")
        db = CoverageDB(sources=[str(path)])
        for raw in path.read_text(errors="replace").splitlines():
            m = _LINE_RE.match(raw.strip())
            if not m:
                continue
            record, count = m.group(1), int(m.group(2))
            db.bins.append(self._parse_record(record, count))
        return db

    def load_many(self, paths: list[Path]) -> CoverageDB:
        return CoverageDB.merge([self.load(p) for p in paths if Path(p).exists()])

    # -- record parsing ----------------------------------------------------
    @staticmethod
    def _parse_fields(record: str) -> dict[str, str]:
        """Split the \\x01key\\x02value stream into a dict."""
        fields: dict[str, str] = {}
        for chunk in record.split(_KEY_SEP):
            if not chunk:
                continue
            key, _, value = chunk.partition(_VAL_SEP)
            if key:
                fields[key] = value
        return fields

    @classmethod
    def _parse_record(cls, record: str, count: int) -> CoverBinResult:
        """Decode one Verilator coverage record into a neutral bin result."""
        fields = cls._parse_fields(record) if _KEY_SEP in record else {}

        if not fields:
            m = _LEGACY_RE.match(record)
            if m:
                fields = {
                    "f": m.group("file"), "l": m.group("line"),
                    "n": m.group("col"), "t": m.group("t"),
                    "page": m.group("page"), "o": m.group("comment"),
                    "h": m.group("hier"),
                }
            else:
                # Unrecognised shape: keep it, flagged, rather than drop data.
                return CoverBinResult(
                    name=record[:120], count=count, kind=CoverageKind.UNKNOWN
                )

        kind = fields.get("t", "") or CoverageKind.UNKNOWN
        try:
            line = int(fields.get("l", "0") or 0)
        except ValueError:
            line = 0

        file = fields.get("f", "")
        hier = fields.get("h", "")
        bin_name = fields.get("bin", "")
        comment = fields.get("o", "") or bin_name

        if kind == CoverageKind.COVERGROUP:
            # Hierarchy is the fully-qualified bin path: cg.coverpoint.bin
            name = hier or f"{fields.get('page', '')}.{bin_name}"
        else:
            name = f"{kind}:{Path(file).name}:{line}" if file else (comment or kind)

        return CoverBinResult(
            name=name,
            count=count,
            kind=kind,
            file=file,
            line=line,
            hierarchy=hier,
            comment=comment,
        )


def render_coverage_markdown(db: CoverageDB) -> str:
    s = db.summary()
    f = s["functional"]
    lines = [
        "# Coverage report",
        "",
        f"**Functional (covergroup bins): {f['percent']:.2f}%  "
        f"({f['covered']}/{f['total']})**",
        "",
        "| kind | covered | total | % |",
        "|---|---:|---:|---:|",
    ]
    for kind, v in s["by_kind"].items():
        lines.append(
            f"| {kind} | {v['covered']} | {v['total']} | {v['percent']:.2f} |"
        )
    holes = s["holes"]
    if holes:
        lines += ["", f"## Functional holes ({len(db.holes())})", "",
                  "| bin | location |", "|---|---|"]
        for h in holes[:50]:
            loc = f"{Path(h['file']).name}:{h['line']}" if h["file"] else "-"
            lines.append(f"| `{h['name'][:90]}` | {loc} |")
    lines += ["", "---",
              "_Functional coverage counts covergroup bins only. "
              "Line/branch/expression coverage is code coverage and is reported "
              "separately._", ""]
    return "\n".join(lines)
