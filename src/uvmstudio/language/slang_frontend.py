"""slang-backed SystemVerilog frontend (via pyslang).

slang provides real IEEE 1800 lexing, preprocessing, parsing, type checking and
elaboration. This module's job is narrow and deliberate:

  1. drive slang with the project's file/include/define set
  2. normalise slang diagnostics into `Diagnostic`
  3. lower slang's AST into the platform's own `Design` IR

Nothing above this module may import pyslang. That is what makes the frontend
replaceable (Surelog/UHDM, or a native parser) without redesign.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path

from ..core.errors import FrontendError
from ..plugins.interfaces import FeatureStatus
from .diagnostics import Diagnostic, DiagnosticBag, DiagSeverity, SourceRef
from .frontend import CompileRequest, CompileResult, SVFrontend
from .ir import (
    ClassInfo,
    ConstraintBlock,
    CoverBin,
    Coverpoint,
    Covergroup,
    Design,
    DesignUnit,
    Direction,
    InstanceNode,
    Modport,
    Parameter,
    Port,
    Property,
    RandKind,
    Subroutine,
    UnitKind,
    Variable,
)

try:  # pragma: no cover - import guard
    import pyslang
    from pyslang import ast as sl_ast
    from pyslang import driver as sl_driver
    from pyslang import syntax as sl_syntax

    _PYSLANG_OK = True
    _IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    _PYSLANG_OK = False
    _IMPORT_ERROR = str(exc)


_SEVERITY_MAP = {
    "Ignored": DiagSeverity.IGNORED,
    "Note": DiagSeverity.HINT,
    "Warning": DiagSeverity.WARNING,
    "Error": DiagSeverity.ERROR,
    "Fatal": DiagSeverity.FATAL,
}

# slang SymbolKind -> IR UnitKind for definition-like symbols
_DEF_KIND = {
    "Module": UnitKind.MODULE,
    "Interface": UnitKind.INTERFACE,
    "Program": UnitKind.PROGRAM,
    "Package": UnitKind.PACKAGE,
    "Checker": UnitKind.CHECKER,
    "Primitive": UnitKind.PRIMITIVE,
}


def _kind_name(kind_enum) -> str:
    return str(kind_enum).rsplit(".", 1)[-1]


class SlangFrontend(SVFrontend):
    name = "slang"

    def __init__(self) -> None:
        self.version = (
            f"{pyslang.VersionInfo.getMajor()}.{pyslang.VersionInfo.getMinor()}."
            f"{pyslang.VersionInfo.getPatch()}"
            if _PYSLANG_OK
            else "unavailable"
        )

    # -- availability / capabilities --------------------------------------
    def is_available(self) -> bool:
        return _PYSLANG_OK

    def capabilities(self) -> dict[str, FeatureStatus]:
        """What this frontend genuinely does today.

        slang itself supports far more than the IR currently lowers; a feature is
        only SUPPORTED here if it is both parsed *and* represented in the IR.
        """
        S, P, PL = (
            FeatureStatus.SUPPORTED,
            FeatureStatus.PARTIALLY_SUPPORTED,
            FeatureStatus.PLANNED,
        )
        return {
            "preprocessor": S,
            "include_resolution": S,
            "parse": S,
            "type_check": S,
            "elaboration": S,
            "packages": S,
            "modules": S,
            "interfaces": S,
            "modports": S,
            "classes": S,
            "class_inheritance": S,
            "parameters": S,
            "generate_blocks": S,
            "constraint_blocks": P,     # declared + located; expressions not lowered
            "rand_randc": S,
            "covergroups": S,
            "coverpoints": S,
            "cover_bins": S,
            "cross_coverage": P,        # detected; cross target list is partial
            "assertions_sva": P,        # properties/sequences located, not analysed
            "virtual_interfaces": S,
            "hierarchy_elaboration": S,
            "dpi_declarations": P,
            "uvm_1_2_compile": P,       # see docs/FEATURE_STATUS.md for evidence
            "vpi": PL,
            "constraint_expression_ir": PL,
            "sva_temporal_ir": PL,
        }

    # -- main entry point --------------------------------------------------
    def compile(self, request: CompileRequest) -> CompileResult:
        if not _PYSLANG_OK:
            raise FrontendError(f"pyslang unavailable: {_IMPORT_ERROR}")

        t0 = time.monotonic()
        bag = DiagnosticBag()

        missing = [f for f in request.files if not Path(f).exists()]
        if missing:
            raise FrontendError(f"source files not found: {missing[:5]}")

        driver = sl_driver.Driver()
        driver.addStandardArgs()
        cmdline = self.build_command_line(request)

        if not driver.parseCommandLine(cmdline):
            bag.add(self._fatal("slang rejected the generated command line", request))
            return self._fail(bag, t0)
        if not driver.processOptions():
            bag.add(self._fatal("slang could not process options (bad include/define?)", request))
            return self._fail(bag, t0)
        if not driver.parseAllSources():
            # Parse errors are real diagnostics; report them rather than a bare failure.
            bag.extend(self._collect_parse_diags(driver, request))
            if not bag.has_errors:
                bag.add(self._fatal("slang failed to parse sources", request))
            return self._fail(bag, t0)

        compilation = driver.createCompilation()
        raw_diags = compilation.getAllDiagnostics()
        bag.extend(self._lower_diagnostics(raw_diags, compilation, request, driver))

        design: Design | None = None
        if request.build_ir:
            try:
                design = self._lower_design(compilation, request)
            except Exception as exc:  # IR lowering must never mask a real compile
                bag.add(
                    Diagnostic(
                        severity=DiagSeverity.WARNING,
                        message=f"IR lowering incomplete: {exc}",
                        code="uvmstudio:IRLowering",
                        producer="slang-adapter",
                    )
                )

        return CompileResult(
            ok=not bag.has_errors,
            diagnostics=bag,
            design=design,
            frontend=self.name,
            frontend_version=self.version,
            duration_s=time.monotonic() - t0,
        )

    def preprocess(self, request: CompileRequest) -> str:
        """Return fully preprocessed text for the request's sources."""
        if not _PYSLANG_OK:
            raise FrontendError(f"pyslang unavailable: {_IMPORT_ERROR}")
        driver = sl_driver.Driver()
        driver.addStandardArgs()
        if not driver.parseCommandLine(self.build_command_line(request)):
            raise FrontendError("slang rejected the generated command line")
        if not driver.processOptions():
            raise FrontendError("slang could not process options")
        out = driver.runPreprocessor(
            includeComments=False, includeDirectives=False, obfuscateIds=False
        )
        return out if isinstance(out, str) else str(out)

    # -- command line construction ----------------------------------------
    def build_command_line(self, request: CompileRequest) -> str:
        """Map a CompileRequest onto slang's own driver command line.

        Exposed (not private) because the reproducibility record stores this
        string verbatim: it is the exact invocation that produced the result.
        """
        parts: list[str] = ["slang"]
        parts.append(f"--std={request.language_standard}")
        parts.append(f"--error-limit={request.max_errors}")
        if request.timescale:
            parts.append(f"--timescale={shlex.quote(request.timescale)}")
        # Elaborating unused modules produces noise on VIP libraries; the top
        # is explicit in the project model, so honour it.
        if request.top:
            parts += ["--top", shlex.quote(request.top)]
        for inc in request.include_dirs:
            parts.append(f"+incdir+{shlex.quote(str(inc))}")
        for d in request.defines:
            parts.append(f"+define+{shlex.quote(d)}")
        for w in request.suppress_warnings:
            parts.append(f"-Wno-{w}")
        for lib in request.library_files:
            parts += ["-v", shlex.quote(str(lib))]
        for f in request.files:
            parts.append(shlex.quote(str(f)))
        return " ".join(parts)

    # -- failure helpers ---------------------------------------------------
    @staticmethod
    def _fatal(msg: str, request: CompileRequest) -> Diagnostic:
        loc = SourceRef(str(request.files[0]), 1, 1) if request.files else None
        return Diagnostic(
            severity=DiagSeverity.FATAL,
            message=msg,
            code="uvmstudio:FrontendInvocation",
            location=loc,
            producer="slang-adapter",
        )

    def _fail(self, bag: DiagnosticBag, t0: float) -> CompileResult:
        return CompileResult(
            ok=False,
            diagnostics=bag,
            frontend=self.name,
            frontend_version=self.version,
            duration_s=time.monotonic() - t0,
        )

    def _collect_parse_diags(self, driver, request: CompileRequest) -> list[Diagnostic]:
        out: list[Diagnostic] = []
        sm = driver.sourceManager
        try:
            for tree in driver.syntaxTrees:
                for d in tree.diagnostics:
                    out.append(self._one_diagnostic(d, sm, driver, request))
        except Exception:
            pass
        return [d for d in out if d is not None]

    # -- diagnostics -------------------------------------------------------
    def _lower_diagnostics(self, raw, compilation, request: CompileRequest, driver):
        out: list[Diagnostic] = []
        sm = compilation.sourceManager
        for d in raw:
            lowered = self._one_diagnostic(d, sm, driver, request)
            if lowered is not None:
                out.append(lowered)
        return out

    def _one_diagnostic(self, d, sm, driver, request: CompileRequest) -> Diagnostic | None:
        engine = driver.diagEngine
        try:
            sev_name = _kind_name(engine.getSeverity(d.code, d.location))
        except Exception:
            sev_name = "Error"
        severity = _SEVERITY_MAP.get(sev_name, DiagSeverity.ERROR)
        if severity is DiagSeverity.IGNORED:
            return None

        loc = self._source_ref(sm, d.location)
        text, w_option = self._format_one(sm, d)

        # Prefer slang's -W option name (that is what users pass to suppress it);
        # fall back to the internal DiagCode identifier.
        short = w_option
        if not short:
            try:
                short = pyslang.DiagnosticEngine.getOptionName(d.code) or ""
            except Exception:
                short = ""
        if not short:
            raw_code = str(d.code)
            if "DiagCode(" in raw_code:
                short = raw_code.split("DiagCode(", 1)[1].rstrip(")")
            else:
                short = raw_code.rsplit(".", 1)[-1]
        if short and short in set(request.suppress_warnings):
            return None
        if not text:
            try:
                text = pyslang.DiagnosticEngine.getMessage(d.code)
            except Exception:
                text = "diagnostic"

        return Diagnostic(
            severity=severity,
            message=text,
            code=f"slang:{short}" if short else "slang",
            location=loc,
            source_line=self._source_line(sm, d.location) if loc else "",
            producer="slang",
        )

    @staticmethod
    def _format_one(sm, diag) -> tuple[str, str]:
        """Render one diagnostic through slang's own text client.

        Returns (message, warning_option). A fresh engine/client pair per
        diagnostic keeps the output isolated so the message can be stored
        structurally rather than as a formatted blob.
        """
        try:
            client = pyslang.TextDiagnosticClient()
            local = pyslang.DiagnosticEngine(sm)
            local.addClient(client)
            local.issue(diag)
            text = client.getString().strip()
            for marker in (": error: ", ": warning: ", ": note: ", ": fatal: "):
                if marker in text:
                    text = text.split(marker, 1)[1]
                    break
            first = text.splitlines()[0].strip() if text else ""
            option = ""
            # slang appends the -Wname option in brackets, e.g. "[-Wwidth-trunc]"
            if first.endswith("]") and " [-W" in first:
                option = first[first.rindex(" [-W") + 4 : -1]
                first = first[: first.rindex(" [-W")]
            return first, option
        except Exception:
            return "", ""

    @staticmethod
    def _source_ref(sm, location) -> SourceRef | None:
        try:
            fname = sm.getFileName(location)
            if not fname:
                return None
            return SourceRef(
                file=str(Path(fname).resolve()) if Path(fname).exists() else fname,
                line=sm.getLineNumber(location),
                column=sm.getColumnNumber(location),
            )
        except Exception:
            return None

    @staticmethod
    def _source_line(sm, location) -> str:
        try:
            fname = sm.getFileName(location)
            line_no = sm.getLineNumber(location)
            p = Path(fname)
            if not p.exists():
                return ""
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, start=1):
                    if i == line_no:
                        return line.rstrip("\n")
        except Exception:
            pass
        return ""

    # -- IR lowering -------------------------------------------------------
    def _lower_design(self, compilation, request: CompileRequest) -> Design:
        sm = compilation.sourceManager
        root = compilation.getRoot()
        design = Design(
            source_files=[str(f) for f in request.files],
            language_standard=request.language_standard,
        )

        def ref(sym) -> SourceRef | None:
            try:
                return self._source_ref(sm, sym.location)
            except Exception:
                return None

        # --- definitions (modules / interfaces / programs) ---------------
        try:
            for d in compilation.getDefinitions():
                kind = _DEF_KIND.get(_kind_name(getattr(d, "definitionKind", "")), None)
                if kind is None:
                    kind = UnitKind.MODULE
                design.units.setdefault(
                    d.name, DesignUnit(name=d.name, kind=kind, location=ref(d))
                )
        except Exception:
            pass

        # --- packages ----------------------------------------------------
        try:
            for pkg in compilation.getPackages():
                if pkg.name in ("std", "$unit"):
                    continue
                unit = DesignUnit(
                    name=pkg.name, kind=UnitKind.PACKAGE, location=ref(pkg)
                )
                self._lower_scope_members(pkg, unit, design, ref, pkg.name)
                design.packages[pkg.name] = unit
        except Exception:
            pass

        # --- elaborated hierarchy + per-instance bodies ------------------
        for inst in root.topInstances:
            design.top_names.append(inst.name)
            node = self._lower_instance(inst, design, ref, path=inst.name, depth=0)
            design.hierarchy.append(node)

        # --- compilation-unit scope (classes declared outside packages) ---
        try:
            for member in root:
                if _kind_name(member.kind) == "CompilationUnit":
                    unit = DesignUnit(
                        name="$unit", kind=UnitKind.COMPILATION_UNIT, location=None
                    )
                    self._lower_scope_members(member, unit, design, ref, "")
                    if (
                        unit.classes
                        or unit.covergroups
                        or unit.subroutines
                        or unit.variables
                    ):
                        design.units["$unit"] = unit
        except Exception:
            pass

        return design

    def _lower_instance(
        self, inst, design: Design, ref, *, path: str, depth: int
    ) -> InstanceNode:
        kind = UnitKind.INTERFACE if inst.isInterface else UnitKind.MODULE
        defn = inst.definition.name if inst.definition else inst.name
        node = InstanceNode(
            name=inst.name,
            definition=defn,
            kind=kind,
            path=path,
            location=ref(inst),
        )

        unit = design.units.setdefault(
            defn, DesignUnit(name=defn, kind=kind, location=ref(inst))
        )
        if not (unit.ports or unit.variables or unit.classes or unit.covergroups):
            try:
                self._lower_scope_members(inst.body, unit, design, ref, "")
            except Exception:
                pass

        if depth > 64:  # guard against pathological recursion
            return node

        try:
            body = inst.body
        except Exception:
            return node

        for member in self._iter_scope(body):
            if _kind_name(member.kind) == "Instance":
                child_def = (
                    member.definition.name if member.definition else member.name
                )
                if child_def not in unit.instances:
                    unit.instances.append(child_def)
                node.children.append(
                    self._lower_instance(
                        member,
                        design,
                        ref,
                        path=f"{path}.{member.name}",
                        depth=depth + 1,
                    )
                )
        return node

    @staticmethod
    def _iter_scope(scope):
        try:
            return list(scope)
        except Exception:
            return []

    def _lower_scope_members(
        self, scope, unit: DesignUnit, design: Design, ref, package: str
    ) -> None:
        for sym in self._iter_scope(scope):
            k = _kind_name(sym.kind)
            name = getattr(sym, "name", "") or ""

            if k == "Port":
                unit.ports.append(
                    Port(
                        name=name,
                        location=ref(sym),
                        direction=self._direction(sym),
                        type_name=self._type_name(sym),
                    )
                )
            elif k == "InterfacePort":
                unit.ports.append(
                    Port(
                        name=name,
                        location=ref(sym),
                        direction=Direction.INTERFACE,
                        is_interface_port=True,
                        type_name=self._type_name(sym),
                    )
                )
            elif k == "Parameter":
                unit.parameters.append(
                    Parameter(
                        name=name,
                        location=ref(sym),
                        type_name=self._type_name(sym),
                        is_localparam=not getattr(sym, "isPortParam", False),
                    )
                )
            elif k in ("Variable", "Net"):
                unit.variables.append(self._lower_variable(sym, ref))
            elif k == "Subroutine":
                unit.subroutines.append(self._lower_subroutine(sym, ref))
            elif k == "ClassType":
                ci = self._lower_class(sym, ref, package)
                unit.classes.append(ci)
                key = f"{package}::{ci.name}" if package else ci.name
                design.classes[key] = ci
            elif k == "GenericClassDef":
                ci = ClassInfo(
                    name=name,
                    location=ref(sym),
                    is_parameterized=True,
                    package=package,
                )
                try:
                    spec = sym.getDefaultSpecialization()
                    if spec is not None:
                        ci = self._lower_class(spec, ref, package)
                        ci.is_parameterized = True
                except Exception:
                    pass
                unit.classes.append(ci)
                key = f"{package}::{ci.name}" if package else ci.name
                design.classes[key] = ci
            elif k == "CovergroupType":
                unit.covergroups.append(self._lower_covergroup(sym, ref, unit.name))
            elif k == "Modport":
                unit.modports.append(self._lower_modport(sym, ref))
            elif k in ("Property", "Sequence"):
                unit.properties.append(
                    Property(name=name, location=ref(sym), kind=k.lower())
                )
            elif k in ("GenerateBlock", "GenerateBlockArray", "StatementBlock"):
                self._lower_scope_members(sym, unit, design, ref, package)
            elif k == "TypeAlias":
                continue

    # -- member lowering helpers ------------------------------------------
    # slang's ArgumentDirection spelling -> IR Direction
    _DIRECTION_MAP = {
        "in": Direction.INPUT,
        "input": Direction.INPUT,
        "out": Direction.OUTPUT,
        "output": Direction.OUTPUT,
        "inout": Direction.INOUT,
        "ref": Direction.REF,
        "constref": Direction.REF,
    }

    @classmethod
    def _direction(cls, sym) -> Direction:
        try:
            d = _kind_name(sym.direction).lower()
            return cls._DIRECTION_MAP.get(d, Direction.UNKNOWN)
        except Exception:
            return Direction.UNKNOWN

    @staticmethod
    def _type_name(sym) -> str:
        try:
            t = sym.type
            return str(t) if t is not None else ""
        except Exception:
            return ""

    def _lower_variable(self, sym, ref) -> Variable:
        rand = RandKind.NONE
        try:
            rm = _kind_name(getattr(sym, "randMode", "")).lower()
            if rm == "rand":
                rand = RandKind.RAND
            elif rm == "randc":
                rand = RandKind.RANDC
        except Exception:
            pass
        tname = self._type_name(sym)
        # slang prints a virtual interface as the interface type itself
        # ("apb_if#(...)"), so the printed name cannot be used to detect one.
        # Ask the type directly; fall back to the spelling only if unavailable.
        is_vif = False
        try:
            is_vif = bool(sym.type.isVirtualInterface)
        except Exception:
            is_vif = tname.lower().startswith("virtual ")

        return Variable(
            name=getattr(sym, "name", ""),
            location=ref(sym),
            type_name=tname,
            rand_kind=rand,
            is_virtual_interface=is_vif,
        )

    def _lower_subroutine(self, sym, ref) -> Subroutine:
        args = []
        try:
            args = [a.name for a in sym.arguments]
        except Exception:
            pass
        is_func = True
        try:
            is_func = _kind_name(sym.subroutineKind) == "Function"
        except Exception:
            pass
        return Subroutine(
            name=getattr(sym, "name", ""),
            location=ref(sym),
            is_function=is_func,
            is_virtual=bool(getattr(sym, "isVirtual", False)),
            is_static=bool(getattr(sym, "isStatic", False)),
            return_type=str(getattr(sym, "returnType", "") or ""),
            args=args,
        )

    def _lower_class(self, sym, ref, package: str) -> ClassInfo:
        base = None
        try:
            bc = sym.baseClass
            if bc is not None:
                base = getattr(bc, "name", None) or str(bc)
        except Exception:
            pass

        ci = ClassInfo(
            name=getattr(sym, "name", ""),
            location=ref(sym),
            base_class=base,
            is_virtual=bool(getattr(sym, "isAbstract", False)),
            package=package,
        )

        # Methods slang synthesises for every class — not user code.
        synthetic = {
            "randomize",
            "pre_randomize",
            "post_randomize",
            "get_randstate",
            "set_randstate",
            "srandom",
            "rand_mode",
            "constraint_mode",
        }

        for m in self._iter_scope(sym):
            k = _kind_name(m.kind)
            name = getattr(m, "name", "") or ""
            if k == "ClassProperty":
                if name == "this":
                    continue
                # A covergroup declared inside a class appears twice: as an
                # anonymous CovergroupType, and as a property whose type is that
                # covergroup. The property carries the user-visible name, so it
                # is the one lowered — the anonymous type is skipped below.
                if _kind_name(getattr(getattr(m, "type", None), "kind", "")) == "CovergroupType":
                    cg = self._lower_covergroup(m.type, ref, ci.name)
                    cg.name = name
                    cg.location = ref(m)
                    ci.covergroups.append(cg)
                    continue
                ci.properties.append(self._lower_variable(m, ref))
            elif k == "Subroutine":
                if name in synthetic:
                    continue
                ci.methods.append(self._lower_subroutine(m, ref))
            elif k == "ConstraintBlock":
                ci.constraints.append(
                    ConstraintBlock(
                        name=name,
                        location=ref(m),
                        is_static=bool(getattr(m, "isStatic", False)),
                    )
                )
            elif k == "CovergroupType":
                # Anonymous in-class covergroup type — already lowered via the
                # named property above. Named ones (rare in classes) still land.
                if name:
                    ci.covergroups.append(self._lower_covergroup(m, ref, ci.name))
        return ci

    def _lower_covergroup(self, sym, ref, enclosing: str) -> Covergroup:
        cg = Covergroup(
            name=getattr(sym, "name", ""), location=ref(sym), enclosing=enclosing
        )
        body = None
        for m in self._iter_scope(sym):
            if _kind_name(m.kind) == "CovergroupBody":
                body = m
                break
        if body is None:
            body = sym
        for m in self._iter_scope(body):
            k = _kind_name(m.kind)
            if k == "Coverpoint":
                cg.coverpoints.append(self._lower_coverpoint(m, ref, is_cross=False))
            elif k == "CoverCross":
                cg.coverpoints.append(self._lower_coverpoint(m, ref, is_cross=True))

        # Sampling is explicit in one of two ways: an event control on the
        # covergroup (`covergroup cg @(posedge clk)`) or a user-defined
        # `sample()` argument list. Both are recorded; a covergroup with
        # neither is free-running and is flagged by lint rule COV003.
        try:
            cg.has_sampling_event = sym.coverageEvent is not None
        except Exception:
            cg.has_sampling_event = False
        # A `with function sample(...)` declaration shows up as a sample
        # subroutine carrying formal arguments; the implicit sample() has none.
        cg.sample_args = []
        for m in self._iter_scope(body):
            if _kind_name(m.kind) == "Subroutine" and getattr(m, "name", "") == "sample":
                try:
                    cg.sample_args = [a.name for a in m.arguments]
                except Exception:
                    cg.sample_args = []
                break
        return cg

    def _lower_coverpoint(self, sym, ref, *, is_cross: bool) -> Coverpoint:
        cp = Coverpoint(
            name=getattr(sym, "name", ""), location=ref(sym), is_cross=is_cross
        )
        if is_cross:
            try:
                cp.cross_targets = [t.name for t in sym.targets]
            except Exception:
                pass
        for m in self._iter_scope(sym):
            if _kind_name(m.kind) == "CoverageBin":
                cp.bins.append(self._lower_bin(m, ref))
        try:
            cp.has_iff = getattr(sym, "getIffExpr", lambda: None)() is not None
        except Exception:
            pass
        return cp

    @staticmethod
    def _lower_bin(sym, ref) -> CoverBin:
        kind = "bins"
        try:
            if getattr(sym, "isIllegal", False):
                kind = "illegal_bins"
            elif getattr(sym, "isIgnore", False):
                kind = "ignore_bins"
        except Exception:
            pass
        return CoverBin(
            name=getattr(sym, "name", ""),
            location=ref(sym),
            bin_kind=kind,
            is_wildcard=bool(getattr(sym, "isWildcard", False)),
        )

    def _lower_modport(self, sym, ref) -> Modport:
        mp = Modport(name=getattr(sym, "name", ""), location=ref(sym))
        for m in self._iter_scope(sym):
            if _kind_name(m.kind) == "ModportPort":
                mp.ports.append((getattr(m, "name", ""), self._direction(m).value))
        return mp
