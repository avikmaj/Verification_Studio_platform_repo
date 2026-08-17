"""Layered lint engine.

Layers, in the order they are meaningful:

  1. syntax      — owned by the frontend (slang); not re-implemented here
  2. semantic    — owned by the frontend
  3. structural  — design-level checks over the IR
  4. UVM         — methodology checks over the IR's UVM overlay
  5. constraints — randomisation/stimulus quality
  6. coverage    — covergroup completeness
  7. methodology — project-level conventions

Only layers 3-7 live here. Every rule declares its own status, and
`rule_catalogue()` publishes that map, so an unimplemented check can never be
mistaken for a passing one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Iterable

from ..language.diagnostics import DiagSeverity, SourceRef
from ..language.ir import ClassInfo, Design, RandKind
from ..plugins.interfaces import FeatureStatus

# UVM base classes that make a class a component vs an object.
UVM_COMPONENT_BASES = {
    "uvm_component", "uvm_driver", "uvm_monitor", "uvm_agent", "uvm_env",
    "uvm_test", "uvm_scoreboard", "uvm_subscriber", "uvm_sequencer",
    "uvm_sequencer_base", "uvm_push_driver",
}
UVM_OBJECT_BASES = {
    "uvm_object", "uvm_transaction", "uvm_sequence_item", "uvm_sequence",
    "uvm_sequence_base", "uvm_reg_item",
}


@dataclass
class LintFinding:
    rule: str
    severity: DiagSeverity
    message: str
    location: SourceRef | None = None
    subject: str = ""
    hint: str = ""

    @property
    def severity_is_error(self) -> bool:
        return self.severity >= DiagSeverity.ERROR

    def format(self, *, root: Path | None = None) -> str:
        loc = ""
        if self.location:
            f = self.location.file
            if root:
                try:
                    f = str(Path(f).relative_to(root))
                except ValueError:
                    pass
            loc = f"{f}:{self.location.line}:{self.location.column}: "
        out = f"{loc}{self.severity.label}: {self.message} [{self.rule}]"
        if self.hint:
            out += f"\n    hint: {self.hint}"
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.name
        return d


@dataclass
class Rule:
    id: str
    layer: str
    status: FeatureStatus
    description: str
    check: Callable[[Design], Iterable[LintFinding]] | None = None


class LintEngine:
    """Runs every implemented rule over the IR."""

    def __init__(self, disabled: set[str] | None = None) -> None:
        self.disabled = disabled or set()
        self.rules: list[Rule] = _build_rules()

    def check(self, design: Design | None) -> list[LintFinding]:
        if design is None:
            return []
        out: list[LintFinding] = []
        for rule in self.rules:
            if rule.check is None or rule.id in self.disabled:
                continue
            if rule.status in (FeatureStatus.PLANNED, FeatureStatus.UNSUPPORTED):
                continue
            try:
                out.extend(rule.check(design))
            except Exception as exc:  # a broken rule must not fail the run
                out.append(
                    LintFinding(
                        rule=rule.id,
                        severity=DiagSeverity.WARNING,
                        message=f"lint rule raised an exception: {exc}",
                    )
                )
        out.sort(key=lambda f: (-f.severity, f.rule))
        return out

    def rule_catalogue(self) -> list[dict]:
        return [
            {
                "id": r.id,
                "layer": r.layer,
                "status": r.status.value,
                "implemented": r.check is not None,
                "description": r.description,
            }
            for r in self.rules
        ]


# --- helpers --------------------------------------------------------------
def _base_chain(design: Design, cls: ClassInfo, *, limit: int = 32) -> list[str]:
    """Walk the inheritance chain by name, tolerating unresolved bases."""
    chain: list[str] = []
    seen: set[str] = set()
    cur = cls
    while cur is not None and cur.base_class and len(chain) < limit:
        base = cur.base_class
        if base in seen:
            break
        seen.add(base)
        chain.append(base)
        cur = design.find_class(base)
    return chain


def _is_uvm_component(design: Design, cls: ClassInfo) -> bool:
    return bool(UVM_COMPONENT_BASES & set(_base_chain(design, cls)))


def _is_uvm_object(design: Design, cls: ClassInfo) -> bool:
    chain = set(_base_chain(design, cls))
    return bool(UVM_OBJECT_BASES & chain) and not _is_uvm_component(design, cls)


# --- rules ----------------------------------------------------------------
def _r_unconstrained_rand(design: Design) -> Iterable[LintFinding]:
    """rand fields in a class with no constraint block at all."""
    for key, cls in design.classes.items():
        if not cls.rand_fields:
            continue
        chain = _base_chain(design, cls)
        has_con = bool(cls.constraints) or any(
            (design.find_class(b).constraints if design.find_class(b) else [])
            for b in chain
        )
        if not has_con:
            yield LintFinding(
                rule="CRV001",
                severity=DiagSeverity.WARNING,
                message=(
                    f"class '{cls.name}' declares {len(cls.rand_fields)} rand "
                    f"field(s) but no constraint block"
                ),
                location=cls.location,
                subject=key,
                hint="Unconstrained rand fields cover the full type range, "
                     "including protocol-illegal values.",
            )


def _r_randc_wide(design: Design) -> Iterable[LintFinding]:
    """randc on a wide field — cyclic state explodes."""
    for key, cls in design.classes.items():
        for v in cls.properties:
            if v.rand_kind is not RandKind.RANDC:
                continue
            width = _width_of(v.type_name)
            if width and width > 16:
                yield LintFinding(
                    rule="CRV002",
                    severity=DiagSeverity.WARNING,
                    message=(
                        f"'{cls.name}.{v.name}' is randc with {width} bits "
                        f"({2**width} cyclic states)"
                    ),
                    location=v.location,
                    subject=f"{key}.{v.name}",
                    hint="randc keeps per-object cyclic state; prefer rand plus "
                         "a coverage-driven constraint for wide fields.",
                )


def _r_covergroup_without_bins(design: Design) -> Iterable[LintFinding]:
    for cg in design.all_covergroups():
        for cp in cg.coverpoints:
            if cp.is_cross:
                continue
            if not cp.bins:
                yield LintFinding(
                    rule="COV001",
                    severity=DiagSeverity.WARNING,
                    message=(
                        f"coverpoint '{cg.name}.{cp.name}' declares no explicit bins"
                    ),
                    location=cp.location,
                    subject=f"{cg.name}.{cp.name}",
                    hint="Auto-bins do not encode intent; a spec-derived bin set "
                         "is required for closure arguments.",
                )


def _r_no_illegal_bins(design: Design) -> Iterable[LintFinding]:
    for cg in design.all_covergroups():
        if not cg.coverpoints:
            continue
        has_illegal = any(
            b.bin_kind == "illegal_bins" for cp in cg.coverpoints for b in cp.bins
        )
        if not has_illegal:
            yield LintFinding(
                rule="COV002",
                severity=DiagSeverity.HINT,
                message=f"covergroup '{cg.name}' declares no illegal_bins",
                location=cg.location,
                subject=cg.name,
                hint="Protocol-illegal encodings should be illegal_bins so the "
                     "simulator flags them rather than silently covering them.",
            )


def _r_covergroup_no_sampling(design: Design) -> Iterable[LintFinding]:
    for cg in design.all_covergroups():
        if not cg.has_sampling_event and not cg.sample_args:
            yield LintFinding(
                rule="COV003",
                severity=DiagSeverity.HINT,
                message=(
                    f"covergroup '{cg.name}' has neither a sampling event nor a "
                    f"sample() argument list"
                ),
                location=cg.location,
                subject=cg.name,
                hint="Sampling must be explicit; free-running covergroups "
                     "produce unreproducible coverage.",
            )


def _r_component_missing_new(design: Design) -> Iterable[LintFinding]:
    for key, cls in design.classes.items():
        if not _is_uvm_component(design, cls):
            continue
        if not any(m.name == "new" for m in cls.methods):
            yield LintFinding(
                rule="UVM001",
                severity=DiagSeverity.ERROR,
                message=(
                    f"uvm_component '{cls.name}' does not declare a constructor"
                ),
                location=cls.location,
                subject=key,
                hint="Every uvm_component needs "
                     "`function new(string name, uvm_component parent);` "
                     "calling super.new(name, parent).",
            )


def _r_object_rand_without_constraint(design: Design) -> Iterable[LintFinding]:
    for key, cls in design.classes.items():
        if not _is_uvm_object(design, cls):
            continue
        if cls.rand_fields and not cls.constraints:
            yield LintFinding(
                rule="UVM002",
                severity=DiagSeverity.WARNING,
                message=(
                    f"sequence item '{cls.name}' has rand fields but declares no "
                    f"constraints"
                ),
                location=cls.location,
                subject=key,
                hint="Protocol legality belongs in the item's constraints, not "
                     "in the sequence body.",
            )


def _r_virtual_interface_in_component(design: Design) -> Iterable[LintFinding]:
    """A component holding a virtual interface should be a driver/monitor."""
    for key, cls in design.classes.items():
        vifs = [v for v in cls.properties if v.is_virtual_interface]
        if not vifs or not _is_uvm_component(design, cls):
            continue
        chain = set(_base_chain(design, cls))
        if chain & {"uvm_env", "uvm_test", "uvm_scoreboard"}:
            yield LintFinding(
                rule="UVM003",
                severity=DiagSeverity.HINT,
                message=(
                    f"'{cls.name}' holds a virtual interface but is an "
                    f"env/test/scoreboard-level component"
                ),
                location=vifs[0].location,
                subject=key,
                hint="Pin-level access normally belongs in the driver/monitor; "
                     "higher layers should use config_db and analysis ports.",
            )


def _r_module_no_timeunit(design: Design) -> Iterable[LintFinding]:
    # Structural placeholder that is genuinely checkable from the IR today:
    # a top-level module with no instances and no ports is usually a stray file.
    for name, unit in design.units.items():
        if name == "$unit":
            continue
        if (
            unit.kind.value == "module"
            and not unit.ports
            and not unit.instances
            and not unit.variables
            and name not in design.top_names
        ):
            yield LintFinding(
                rule="RTL001",
                severity=DiagSeverity.HINT,
                message=f"module '{name}' has no ports, variables or instances",
                location=unit.location,
                subject=name,
                hint="Dead module — remove it or confirm it is intentional.",
            )


def _width_of(type_name: str) -> int | None:
    """Extract a bit width from a printed type like 'bit[31:0]'."""
    import re

    m = re.search(r"\[(\d+):(\d+)\]", type_name or "")
    if not m:
        return None
    hi, lo = int(m.group(1)), int(m.group(2))
    return abs(hi - lo) + 1


def _build_rules() -> list[Rule]:
    S, P, PL = (
        FeatureStatus.SUPPORTED,
        FeatureStatus.PARTIALLY_SUPPORTED,
        FeatureStatus.PLANNED,
    )
    return [
        # --- implemented -------------------------------------------------
        Rule("CRV001", "constraints", S,
             "rand fields with no constraint block anywhere in the hierarchy",
             _r_unconstrained_rand),
        Rule("CRV002", "constraints", S,
             "randc on a wide field (cyclic state explosion)", _r_randc_wide),
        Rule("COV001", "coverage", S,
             "coverpoint without explicit bins", _r_covergroup_without_bins),
        Rule("COV002", "coverage", S,
             "covergroup without illegal_bins", _r_no_illegal_bins),
        Rule("COV003", "coverage", S,
             "covergroup without an explicit sampling event",
             _r_covergroup_no_sampling),
        Rule("UVM001", "uvm", S,
             "uvm_component without a constructor", _r_component_missing_new),
        Rule("UVM002", "uvm", S,
             "sequence item with rand fields but no constraints",
             _r_object_rand_without_constraint),
        Rule("UVM003", "uvm", P,
             "virtual interface held above the driver/monitor layer",
             _r_virtual_interface_in_component),
        Rule("RTL001", "structural", S,
             "module with no ports, variables or instances", _r_module_no_timeunit),

        # --- declared, not yet implemented --------------------------------
        # Listed so the catalogue is honest about coverage of the rule space.
        Rule("UVM010", "uvm", PL,
             "missing super.build_phase()/connect_phase() call", None),
        Rule("UVM011", "uvm", PL,
             "objection raised without a matching drop (leak)", None),
        Rule("UVM012", "uvm", PL,
             "`uvm_component_utils on a uvm_object (wrong factory macro)", None),
        Rule("UVM013", "uvm", PL,
             "uvm_config_db set/get type or key mismatch", None),
        Rule("UVM014", "uvm", PL,
             "analysis port connected to an incompatible imp type", None),
        Rule("UVM015", "uvm", PL,
             "sequence started on a sequencer of a different item type", None),
        Rule("SVA001", "assertions", PL,
             "concurrent assertion with no cover property (vacuity risk)", None),
        Rule("SVA002", "assertions", PL,
             "assertion without disable iff (reset) qualification", None),
        Rule("CRV010", "constraints", PL,
             "solve...before cycle in a constraint set", None),
        Rule("CRV011", "constraints", PL,
             "hardcoded literal in a sequence body (stimulus gap)", None),
        Rule("COV010", "coverage", PL,
             "cross coverage with unreachable bin combinations", None),
    ]
