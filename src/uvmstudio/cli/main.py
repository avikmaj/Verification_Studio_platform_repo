"""`uvmstudio` command-line interface.

This is the single automation surface: the IDE, CI pipelines and PowerShell all
drive the platform through these subcommands. It is designed to be scriptable —
`--json` on every command that produces data, stable exit codes, no interactive
prompts.

Exit codes follow `core.errors`, so CI can branch on failure class:
    0  success
    1  generic failure
    2  project error         5  simulator error
    3  frontend error        6  backend unavailable
    4  compile error         7  regression error
   20  regression completed but did not pass
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..core.errors import StudioError
from ..core.logging import Logger, Severity, get_logger, set_logger
from ..core.platform import ExecHost, platform_report
from ..core.project import PROJECT_FILENAME, Project
from ..language.frontend import CompileRequest, available_frontends, get_frontend
from ..plugins.interfaces import FeatureStatus
from ..regression.db import RegressionDB
from ..regression.report import build_report, render_markdown, write_all
from ..regression.runner import RegressionRunner
from ..repro.metadata import ReproRecord
from ..simulator.base import (
    RunStatus,
    WaveFormat,
    available_simulators,
    get_simulator,
    registered_simulators,
)
from ..uvm.library import find_uvm_home, inspect_uvm

EXIT_REGRESSION_NOT_PASSED = 20


# --------------------------------------------------------------------------
def _load_project(args) -> Project:
    return Project.load(Path(args.project))


def _make_simulator(args):
    host = None
    if getattr(args, "exec_host", None):
        host = ExecHost(args.exec_host)
    return get_simulator(
        args.backend,
        exec_host=host,
        wsl_distro=getattr(args, "wsl_distro", None),
        jobs=getattr(args, "jobs", None),
    )


def _emit(data: dict, as_json: bool, human: str = "") -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif human:
        print(human)


# --- commands -------------------------------------------------------------
def cmd_env(args) -> int:
    fes = {}
    for name in available_frontends():
        fe = get_frontend(name)
        fes[name] = {"version": fe.version}
    sims = {}
    for name in registered_simulators():
        try:
            s = get_simulator(name)
            ok = s.is_available()
            sims[name] = {
                "available": ok,
                "version": s.version() if ok else None,
                "exec_host": getattr(s, "exec_host", lambda: "native")(),
                "solver": (
                    getattr(s, "solver_available", lambda: None)() if ok else None
                ),
            }
        except Exception as exc:
            sims[name] = {"available": False, "error": str(exc)}

    uvm_home = find_uvm_home(getattr(args, "uvm_home", None))
    uvm = inspect_uvm(str(uvm_home)).to_dict() if uvm_home else None

    data = {
        "platform": platform_report(),
        "frontends": fes,
        "simulators": sims,
        "uvm": uvm,
        "studio_version": _version(),
    }
    if args.json:
        _emit(data, True)
        return 0

    p = data["platform"]
    print(f"UVM Verification Studio {data['studio_version']}")
    print(f"  host          : {p['os']} {p['machine']} (python {p['python']})")
    print(f"  exec host     : {p['default_exec_host']}"
          + ("  [WSL available]" if p["wsl_available"] else ""))
    for n, v in fes.items():
        print(f"  frontend      : {n} {v['version']}")
    for n, v in sims.items():
        state = f"{v.get('version')} on {v.get('exec_host')}" if v["available"] else "NOT AVAILABLE"
        solver = ""
        if v.get("solver") is not None:
            solver = "  solver=z3" if v["solver"] else "  solver=MISSING (constraints will fail)"
        print(f"  simulator     : {n:<10} {state}{solver}")
    if uvm:
        print(f"  UVM           : {uvm['version']} ({uvm['version_string']})")
        print(f"                  {uvm['home']}")
    else:
        print("  UVM           : not found (set UVM_HOME or pass --uvm-home)")
    return 0


def cmd_init(args) -> int:
    root = Path(args.directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / PROJECT_FILENAME
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 2

    from .scaffold import write_scaffold

    created = write_scaffold(root, name=args.name or root.name, with_uvm=args.uvm)
    print(f"Created project '{args.name or root.name}' in {root}")
    for p in created:
        print(f"  + {p.relative_to(root)}")
    print(f"\nNext:  uvmstudio build -p {root}")
    return 0


def cmd_compile(args) -> int:
    proj = _load_project(args)
    fe = get_frontend(args.frontend)
    req = CompileRequest(
        files=proj.source_files(),
        include_dirs=proj.include_dirs(),
        defines=proj.defines(),
        top=proj.top,
        language_standard=proj.language_standard,
        timescale=proj.timescale,
        suppress_warnings=list(proj.backend_options.get("suppress_warnings", [])),
    )
    res = fe.compile(req)

    if args.json:
        out = res.to_dict()
        out["command"] = fe.build_command_line(req) if hasattr(fe, "build_command_line") else None
        _emit(out, True)
    else:
        if len(res.diagnostics):
            print(res.diagnostics.format(root=proj.root, limit=args.max_diagnostics))
        print(
            f"\n{'STATUS: PASS' if res.ok else 'STATUS: FAIL'}  "
            f"({res.error_count} error(s), {res.warning_count} warning(s)) "
            f"via {res.frontend} {res.frontend_version} in {res.duration_s:.2f}s"
        )
        if res.design:
            st = res.design.stats()
            print("EVIDENCE: " + ", ".join(f"{k}={v}" for k, v in st.items()))
    return 0 if res.ok else 4


def cmd_elaborate(args) -> int:
    """Compile and dump the elaborated hierarchy / IR."""
    proj = _load_project(args)
    fe = get_frontend(args.frontend)
    res = fe.compile(
        CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
        )
    )
    if not res.ok or res.design is None:
        print(res.diagnostics.format(root=proj.root, limit=args.max_diagnostics))
        print("STATUS: FAIL — elaboration did not complete")
        return 4

    design = res.design
    if args.json:
        _emit(design.to_dict(), True)
        return 0

    print(f"Top: {', '.join(design.top_names)}")
    print("\nHierarchy:")

    def show(node, depth=0):
        print("  " * (depth + 1) + f"{node.name} : {node.definition} [{node.kind.value}]")
        for c in node.children:
            show(c, depth + 1)

    for root_node in design.hierarchy:
        show(root_node)

    if design.classes:
        print("\nClasses:")
        for key, c in sorted(design.classes.items()):
            base = f" extends {c.base_class}" if c.base_class else ""
            rnd = f"  rand:{len(c.rand_fields)}" if c.rand_fields else ""
            con = f"  constraints:{len(c.constraints)}" if c.constraints else ""
            print(f"  {key}{base}{rnd}{con}")

    cgs = design.all_covergroups()
    if cgs:
        print("\nCovergroups:")
        for cg in cgs:
            print(f"  {cg.name} ({len(cg.coverpoints)} coverpoint(s))")
            for cp in cg.coverpoints:
                kind = "cross" if cp.is_cross else "coverpoint"
                print(f"    {kind} {cp.name}: {len(cp.bins)} bin(s)")

    print("\nSTATUS: PASS")
    print("EVIDENCE: " + ", ".join(f"{k}={v}" for k, v in design.stats().items()))
    return 0


def cmd_lint(args) -> int:
    from ..lint.engine import LintEngine

    proj = _load_project(args)
    fe = get_frontend(args.frontend)
    res = fe.compile(
        CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
        )
    )
    engine = LintEngine()
    findings = engine.check(res.design) if res.design else []

    if args.json:
        _emit(
            {
                "frontend_ok": res.ok,
                "frontend_diagnostics": res.diagnostics.to_dict(),
                "lint": [f.to_dict() for f in findings],
                "rules": engine.rule_catalogue(),
            },
            True,
        )
    else:
        if len(res.diagnostics):
            print(res.diagnostics.format(root=proj.root, limit=args.max_diagnostics))
        for f in findings:
            print(f.format(root=proj.root))
        errs = sum(1 for f in findings if f.severity_is_error)
        print(
            f"\nSTATUS: {'FAIL' if (not res.ok or errs) else 'PASS'}  "
            f"({res.error_count} frontend error(s), {len(findings)} lint finding(s))"
        )
    return 0 if res.ok else 4


def cmd_build(args) -> int:
    proj = _load_project(args)
    sim = _make_simulator(args)
    runner = RegressionRunner(proj, sim, jobs=args.jobs or 1)
    waves = WaveFormat(args.waves) if args.waves else None
    result = runner.build(waves=waves)

    if args.json:
        _emit(result.to_dict(), True)
    else:
        if not result.ok:
            print(result.log[-8000:])
        print(f"\nSTATUS: {'PASS' if result.ok else 'BLOCKED'}")
        print(
            f"EVIDENCE: {sim.name} {result.backend_version}"
            f"{' (cache hit)' if result.cached else ''}, "
            f"{result.duration_s:.1f}s → {result.binary}"
        )
        if not result.ok:
            print("NEXT: fix the compile errors above, then re-run `uvmstudio build`")
    return 0 if result.ok else 5


def cmd_run(args) -> int:
    proj = _load_project(args)
    sim = _make_simulator(args)
    runner = RegressionRunner(proj, sim, frontend=get_frontend(args.frontend),
                              jobs=1)
    outcome = runner.run_regression(
        tests=[args.test], base_seed=args.seed, seeds_override=1,
        name=f"{proj.name}-{args.test}",
    )
    return _finish_regression(args, proj, runner, outcome)


def cmd_regress(args) -> int:
    proj = _load_project(args)
    sim = _make_simulator(args)
    runner = RegressionRunner(
        proj, sim, frontend=get_frontend(args.frontend), jobs=args.jobs or 1
    )
    outcome = runner.run_regression(
        tier=args.tier,
        tests=args.tests or None,
        base_seed=args.seed,
        seeds_override=args.seeds,
        name=args.name,
    )
    return _finish_regression(args, proj, runner, outcome)


def _finish_regression(args, proj, runner, outcome) -> int:
    out_dir = proj.results_path / f"regression_{outcome.regression_id}"
    paths = write_all(runner.db, outcome.regression_id, out_dir)
    report = build_report(runner.db, outcome.regression_id)

    if args.json:
        _emit(report, True)
    else:
        print()
        print(render_markdown(report))
        print(f"\nReports: {paths['json']}\n         {paths['html']}")
    return 0 if outcome.ok else EXIT_REGRESSION_NOT_PASSED


def cmd_report(args) -> int:
    proj = _load_project(args)
    db = RegressionDB(proj.results_path / "regression.db")
    reg_id = args.id
    if reg_id is None:
        hist = db.history(proj.name, limit=1)
        if not hist:
            print("no regressions recorded yet", file=sys.stderr)
            return 7
        reg_id = hist[0]["id"]
    report = build_report(db, reg_id)
    if args.json:
        _emit(report, True)
    else:
        print(render_markdown(report))
    return 0 if report["summary"]["status"] == "PASS" else EXIT_REGRESSION_NOT_PASSED


def cmd_history(args) -> int:
    proj = _load_project(args)
    db = RegressionDB(proj.results_path / "regression.db")
    hist = db.history(proj.name, limit=args.limit)
    if args.json:
        _emit({"history": hist, "clusters": db.clusters(),
               "seed_effectiveness": db.seed_effectiveness()}, True)
        return 0
    print(f"{'id':>5}  {'status':<13} {'tier':<5} {'P/F/NV/B':<16} started")
    for h in hist:
        pf = f"{h['passed']}/{h['failed']}/{h['not_verified']}/{h['blocked']}"
        print(f"{h['id']:>5}  {h['status']:<13} {h['tier']:<5} {pf:<16} {h['started_utc']}")
    clusters = db.clusters(limit=10)
    if clusters:
        print("\nTop failure clusters:")
        for c in clusters:
            print(f"  {c['occurrences']:>4}x  [{c['triage_state']}]  {c['signature'][:100]}")
    return 0


def cmd_reproduce(args) -> int:
    data = ReproRecord.load(Path(args.record))
    print(f"Reproducing {data['project']} / {data['test']} seed {data['seed']}")
    print(f"  recorded : {data['started_utc']}  result={data['result']}")
    print(f"  git      : {(data['git'].get('commit') or '-')[:12]}"
          f"{' (DIRTY — exact reproduction not guaranteed)' if data['git'].get('dirty') else ''}")
    print(f"  simulator: {data['tools'].get('simulator_backend')} "
          f"{data['tools'].get('simulator_version')}")
    print(f"  UVM      : {data['tools'].get('uvm_version') or '-'}")

    if not data.get("complete"):
        print(f"\nSTATUS: BLOCKED\nEVIDENCE: record is incomplete — missing "
              f"{data.get('missing_fields')}")
        print("NEXT: re-run with a complete environment; this record cannot reproduce.")
        return 9

    if args.show_only:
        print("\nBuild command:\n  " + " ".join(data["build_command"]))
        print("\nRun command:\n  " + " ".join(data["run_command"]))
        return 0

    # Verify the sources still hash the same before claiming reproduction.
    from ..core.hashing import hash_sources

    try:
        now = hash_sources([Path(f) for f in data["source_files"]])
    except Exception as exc:
        print(f"\nSTATUS: BLOCKED\nEVIDENCE: cannot hash sources: {exc}")
        return 9
    if now != data["source_hash"]:
        print("\nSTATUS: BLOCKED")
        print(f"EVIDENCE: source hash changed ({data['source_hash'][:12]} -> {now[:12]})")
        print("NEXT: check out the recorded git commit, then reproduce again.")
        return 9

    proj = Project.load(Path(data["project_root"]))
    sim = _make_simulator(args)
    runner = RegressionRunner(proj, sim, frontend=get_frontend(args.frontend), jobs=1)
    outcome = runner.run_regression(
        tests=[data["test"]], base_seed=data["seed"], seeds_override=1,
        name=f"reproduce-{data['test']}-{data['seed']}",
    )
    got = outcome.results[0][1].status.value if outcome.results else "NOT_VERIFIED"
    match = got == data["result"]
    print(f"\nSTATUS: {'PASS' if match else 'FAIL'}")
    print(f"EVIDENCE: recorded={data['result']}  reproduced={got}")
    print("NEXT: " + ("none — run reproduced exactly"
                      if match else "investigate non-determinism"))
    return 0 if match else 1


def cmd_capabilities(args) -> int:
    data: dict = {}
    for name in available_frontends():
        data[f"frontend:{name}"] = {
            k: v.value for k, v in get_frontend(name).capabilities().items()
        }
    for name in registered_simulators():
        try:
            data[f"simulator:{name}"] = {
                k: v.value for k, v in get_simulator(name).capabilities().items()
            }
        except Exception as exc:
            data[f"simulator:{name}"] = {"error": str(exc)}

    if args.json:
        _emit(data, True)
        return 0
    for comp, caps in data.items():
        print(f"\n{comp}")
        for k, v in sorted(caps.items(), key=lambda kv: (kv[1], kv[0])):
            print(f"  {v:<22} {k}")
    print("\nStatus values: SUPPORTED / PARTIALLY_SUPPORTED / EXPERIMENTAL / "
          "PLANNED / UNSUPPORTED")
    return 0


def cmd_coverage(args) -> int:
    """Merge and report the coverage databases produced by a regression."""
    from ..coverage.model import VerilatorCoverageReader, render_coverage_markdown

    proj = _load_project(args)
    if args.files:
        dats = [Path(f) for f in args.files]
    else:
        dats = sorted(proj.results_path.rglob("coverage.dat"))
    if not dats:
        print(
            f"STATUS: NOT_VERIFIED\nEVIDENCE: no coverage.dat under "
            f"{proj.results_path}\nNEXT: run a regression with coverage enabled",
            file=sys.stderr,
        )
        return 7

    reader = VerilatorCoverageReader()
    db = reader.load_many(dats)
    out_dir = proj.results_path / "coverage"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "coverage.json").write_text(
        json.dumps(db.summary(), indent=2), encoding="utf-8"
    )
    (out_dir / "coverage.md").write_text(
        render_coverage_markdown(db), encoding="utf-8"
    )

    if args.json:
        _emit(db.summary(), True)
        return 0

    print(render_coverage_markdown(db))
    print(f"\nMerged {len(dats)} database(s) -> {out_dir}/coverage.json")
    cov, tot, pct = db.functional_score()
    if args.threshold is not None:
        ok = pct >= args.threshold
        print(f"\nSTATUS: {'PASS' if ok else 'FAIL'}")
        print(f"EVIDENCE: functional coverage {pct:.2f}% "
              f"vs threshold {args.threshold:.2f}%")
        return 0 if ok else EXIT_REGRESSION_NOT_PASSED
    print(f"\nSTATUS: PASS\nEVIDENCE: functional {pct:.2f}% ({cov}/{tot} bins)")
    return 0


def cmd_ci(args) -> int:
    from ..ci.generate import generate

    proj = _load_project(args)
    written = generate(proj, args.system, Path(args.out or proj.root))
    for p in written:
        print(f"wrote {p}")
    return 0


def cmd_waves(args) -> int:
    from ..waveform.reader import open_waveform

    path = Path(args.file)
    wave, fmt = open_waveform(path)
    if args.json:
        data = wave.summary()
        data["format"] = fmt
        _emit(data, True)
        return 0

    print(f"{path.name}  [{fmt.upper()}]")
    print(f"  signals   : {wave.signal_count}")
    print(f"  timescale : {wave.timescale}")
    print(f"  end time  : {wave.end_time}")
    if wave.scopes():
        print("  scopes    :")
        for scope in wave.scopes()[:40]:
            print(f"    {scope}")

    if args.signal:
        for sig in args.signal:
            series = wave.series(sig)
            if not series:
                print(f"\n  {sig}: NOT FOUND in this waveform")
                continue
            print(f"\n  {sig}: {len(series)} value change(s)")
            for t, v in series[: args.limit]:
                print(f"    @{t:<10} {v}")
    return 0


# --- argument parser ------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="uvmstudio",
        description="UVM Verification Studio — SystemVerilog/UVM verification platform",
    )
    ap.add_argument("--version", action="version", version=f"uvmstudio {_version()}")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    ap.add_argument("-q", "--quiet", action="store_true")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, *, project=True, frontend=True, backend=False, json_out=True):
        if project:
            p.add_argument("-p", "--project", default=".",
                           help=f"path to {PROJECT_FILENAME} or its directory")
        if frontend:
            p.add_argument("--frontend", default="slang", help="SystemVerilog frontend")
            p.add_argument("--max-diagnostics", type=int, default=50)
        if backend:
            p.add_argument("-b", "--backend", default="verilator",
                           help="simulator backend")
            p.add_argument("--exec-host", choices=[h.value for h in ExecHost],
                           help="where the backend runs (Windows: wsl)")
            p.add_argument("--wsl-distro", help="WSL distro name (Windows only)")
        if json_out:
            p.add_argument("--json", action="store_true",
                           help="machine-readable output")

    p = sub.add_parser("env", help="show detected toolchain and platform")
    p.add_argument("--uvm-home")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("init", help="scaffold a new project")
    p.add_argument("directory")
    p.add_argument("--name")
    p.add_argument("--uvm", action="store_true",
                   help="reference $UVM_HOME in the generated project "
                        "(the scaffold testbench itself is plain SystemVerilog; "
                        "copy examples/golden_apb for a full UVM env)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("compile", help="parse, type-check and elaborate")
    common(p, backend=False)
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("elaborate", help="dump the elaborated hierarchy and IR")
    common(p, backend=False)
    p.set_defaults(func=cmd_elaborate)

    p = sub.add_parser("lint", help="run the layered lint engine")
    common(p, backend=False)
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("build", help="build a simulation image")
    common(p, backend=True)
    p.add_argument("-j", "--jobs", type=int, default=None)
    p.add_argument("--waves", choices=[w.value for w in WaveFormat])
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("run", help="run a single test with one seed")
    common(p, backend=True)
    p.add_argument("test")
    p.add_argument("-s", "--seed", type=int, default=1)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("regress", help="run a regression tier")
    common(p, backend=True)
    p.add_argument("-t", "--tier", default="L1",
                   choices=["L0", "L1", "L2", "L3", "L4", "L5"])
    p.add_argument("--tests", nargs="*", help="explicit test list (overrides tier)")
    p.add_argument("-s", "--seed", type=int, default=None,
                   help="base seed; omit for a random but recorded base")
    p.add_argument("--seeds", type=int, default=None,
                   help="override seed count per test")
    p.add_argument("-j", "--jobs", type=int, default=1)
    p.add_argument("--name")
    p.set_defaults(func=cmd_regress)

    p = sub.add_parser("report", help="render a report for a recorded regression")
    common(p, frontend=False)
    p.add_argument("--id", type=int, default=None, help="regression id (default: latest)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("history", help="regression history and failure clusters")
    common(p, frontend=False)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("reproduce", help="re-run from a repro.json record")
    p.add_argument("record")
    p.add_argument("--show-only", action="store_true",
                   help="print the recorded commands without running")
    p.add_argument("--frontend", default="slang")
    p.add_argument("-b", "--backend", default="verilator")
    p.add_argument("--exec-host", choices=[h.value for h in ExecHost])
    p.add_argument("--wsl-distro")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_reproduce)

    p = sub.add_parser("capabilities",
                       help="what every component actually supports")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("coverage", help="merge and report coverage databases")
    common(p, frontend=False)
    p.add_argument("--files", nargs="*", help="explicit coverage.dat paths")
    p.add_argument("--threshold", type=float,
                   help="fail if functional coverage is below this percentage")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("ci", help="generate CI pipeline files")
    common(p, frontend=False, json_out=False)
    p.add_argument("system", choices=["github", "gitlab", "jenkins", "cli"])
    p.add_argument("--out")
    p.set_defaults(func=cmd_ci)

    p = sub.add_parser("waves", help="inspect a waveform file (VCD or FST)")
    p.add_argument("file")
    p.add_argument("--signal", nargs="*", default=None,
                   help="dump value changes for these signal paths")
    p.add_argument("--limit", type=int, default=20,
                   help="max value changes to print per signal")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_waves)

    return ap


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("uvm-verification-studio")
    except Exception:
        return "0.0.0+dev"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    level = Severity.INFO
    if args.quiet:
        level = Severity.ERROR
    elif args.verbose >= 1:
        level = Severity.DEBUG
    set_logger(Logger(level=level))

    try:
        return args.func(args)
    except StudioError as exc:
        get_logger().error(str(exc))
        print(f"\nSTATUS: BLOCKED\nEVIDENCE: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
