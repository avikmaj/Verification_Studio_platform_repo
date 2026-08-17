"""Design IR — the platform's own semantic model.

This is deliberately *not* slang's AST and not UHDM. It is the stable,
serialisable layer that everything above the frontend talks to: the IDE
navigator, the lint engine, the UVM debugger, the coverage planner and the
verification graph. A different frontend (Surelog/UHDM, or a native parser)
must be able to produce the same IR.

Design rule: the IR records *what was written*, with source locations, plus the
minimum resolved semantics needed to navigate. It does not evaluate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Iterator

from .diagnostics import SourceRef


class UnitKind(str, Enum):
    MODULE = "module"
    INTERFACE = "interface"
    PROGRAM = "program"
    PACKAGE = "package"
    CLASS = "class"
    CHECKER = "checker"
    PRIMITIVE = "primitive"
    COMPILATION_UNIT = "compilation_unit"


class Direction(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"
    REF = "ref"
    INTERFACE = "interface"
    UNKNOWN = "unknown"


class RandKind(str, Enum):
    NONE = "none"
    RAND = "rand"
    RANDC = "randc"


@dataclass
class Node:
    name: str
    location: SourceRef | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Port(Node):
    direction: Direction = Direction.UNKNOWN
    type_name: str = ""
    width: int | None = None
    is_interface_port: bool = False


@dataclass
class Parameter(Node):
    type_name: str = ""
    default_text: str = ""
    is_localparam: bool = False


@dataclass
class Variable(Node):
    type_name: str = ""
    rand_kind: RandKind = RandKind.NONE
    is_static: bool = False
    is_virtual_interface: bool = False


@dataclass
class Subroutine(Node):
    is_function: bool = True
    is_virtual: bool = False
    is_static: bool = False
    return_type: str = ""
    args: list[str] = field(default_factory=list)


@dataclass
class ConstraintBlock(Node):
    is_static: bool = False
    is_extern: bool = False


@dataclass
class CoverBin(Node):
    bin_kind: str = "bins"          # bins | illegal_bins | ignore_bins
    is_wildcard: bool = False
    is_transition: bool = False


@dataclass
class Coverpoint(Node):
    expression: str = ""
    bins: list[CoverBin] = field(default_factory=list)
    is_cross: bool = False
    cross_targets: list[str] = field(default_factory=list)
    has_iff: bool = False


@dataclass
class Covergroup(Node):
    coverpoints: list[Coverpoint] = field(default_factory=list)
    sample_args: list[str] = field(default_factory=list)
    has_sampling_event: bool = False
    enclosing: str = ""


@dataclass
class Property(Node):
    kind: str = "property"          # property | sequence | assert | assume | cover
    is_concurrent: bool = True
    has_disable_iff: bool = False
    text: str = ""


@dataclass
class Modport(Node):
    ports: list[tuple[str, str]] = field(default_factory=list)   # (name, direction)


@dataclass
class ClassInfo(Node):
    base_class: str | None = None
    is_virtual: bool = False
    is_parameterized: bool = False
    package: str = ""
    properties: list[Variable] = field(default_factory=list)
    methods: list[Subroutine] = field(default_factory=list)
    constraints: list[ConstraintBlock] = field(default_factory=list)
    covergroups: list[Covergroup] = field(default_factory=list)

    # UVM overlay (filled by uvm.model, empty for non-UVM designs)
    uvm_role: str = ""              # component | object | sequence_item | driver | ...
    uvm_base_chain: list[str] = field(default_factory=list)
    factory_registered: bool = False

    @property
    def rand_fields(self) -> list[Variable]:
        return [p for p in self.properties if p.rand_kind is not RandKind.NONE]


@dataclass
class DesignUnit(Node):
    kind: UnitKind = UnitKind.MODULE
    ports: list[Port] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    subroutines: list[Subroutine] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    covergroups: list[Covergroup] = field(default_factory=list)
    properties: list[Property] = field(default_factory=list)
    modports: list[Modport] = field(default_factory=list)
    instances: list[str] = field(default_factory=list)   # instantiated definition names


@dataclass
class InstanceNode:
    """A node in the elaborated hierarchy."""

    name: str
    definition: str
    kind: UnitKind = UnitKind.MODULE
    path: str = ""
    location: SourceRef | None = None
    children: list["InstanceNode"] = field(default_factory=list)

    def walk(self) -> Iterator["InstanceNode"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def find(self, path: str) -> "InstanceNode | None":
        for n in self.walk():
            if n.path == path:
                return n
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "definition": self.definition,
            "kind": self.kind.value,
            "path": self.path,
            "location": asdict(self.location) if self.location else None,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Design:
    """Complete elaborated view of one compilation."""

    top_names: list[str] = field(default_factory=list)
    units: dict[str, DesignUnit] = field(default_factory=dict)
    packages: dict[str, DesignUnit] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    hierarchy: list[InstanceNode] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    language_standard: str = "1800-2017"

    # -- queries used by the IDE / lint / UVM debugger --------------------
    def all_instances(self) -> Iterator[InstanceNode]:
        for root in self.hierarchy:
            yield from root.walk()

    def find_class(self, name: str) -> ClassInfo | None:
        if name in self.classes:
            return self.classes[name]
        # allow lookup by unqualified name when stored as pkg::name
        for key, c in self.classes.items():
            if key.rsplit("::", 1)[-1] == name:
                return c
        return None

    def derived_from(self, base: str) -> list[ClassInfo]:
        """All classes whose resolved base chain contains `base`."""
        return [c for c in self.classes.values() if base in c.uvm_base_chain]

    def classes_with_rand(self) -> list[ClassInfo]:
        return [c for c in self.classes.values() if c.rand_fields]

    def all_covergroups(self) -> list[Covergroup]:
        out = list()
        for u in list(self.units.values()) + list(self.packages.values()):
            out.extend(u.covergroups)
        for c in self.classes.values():
            out.extend(c.covergroups)
        return out

    def all_constraints(self) -> list[tuple[str, ConstraintBlock]]:
        return [(c.name, cb) for c in self.classes.values() for cb in c.constraints]

    def stats(self) -> dict[str, int]:
        cps = sum(len(cg.coverpoints) for cg in self.all_covergroups())
        return {
            "source_files": len(self.source_files),
            "modules": sum(1 for u in self.units.values() if u.kind is UnitKind.MODULE),
            "interfaces": sum(
                1 for u in self.units.values() if u.kind is UnitKind.INTERFACE
            ),
            "packages": len(self.packages),
            "classes": len(self.classes),
            "covergroups": len(self.all_covergroups()),
            "coverpoints": cps,
            "constraint_blocks": len(self.all_constraints()),
            "instances": sum(1 for _ in self.all_instances()),
            "assertions": sum(len(u.properties) for u in self.units.values()),
        }

    def to_dict(self) -> dict:
        return {
            "top_names": self.top_names,
            "language_standard": self.language_standard,
            "source_files": self.source_files,
            "stats": self.stats(),
            "units": {k: v.to_dict() for k, v in self.units.items()},
            "packages": {k: v.to_dict() for k, v in self.packages.items()},
            "classes": {k: v.to_dict() for k, v in self.classes.items()},
            "hierarchy": [h.to_dict() for h in self.hierarchy],
        }
