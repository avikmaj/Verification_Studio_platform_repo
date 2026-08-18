"""Unit tests for the platform core: project model, paths, process, hashing,
reproducibility, coverage parsing, waveform parsing and the regression DB.

These run without a simulator so they gate every commit, including on Windows.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from uvmstudio.core.errors import ProjectError
from uvmstudio.core.hashing import hash_sources, hash_text
from uvmstudio.core.platform import (
    ExecContext,
    ExecHost,
    windows_to_wsl_path,
    wsl_to_windows_path,
)
from uvmstudio.core.process import ProcessManager
from uvmstudio.core.project import Project, TestSpec
from uvmstudio.coverage.model import CoverageDB, CoverageKind, VerilatorCoverageReader
from uvmstudio.regression.db import RegressionDB
from uvmstudio.simulator.base import RunResult, RunStatus
from uvmstudio.waveform.vcd import VCDReader


# --- project model --------------------------------------------------------
def _write_project(tmp_path: Path, extra: str = "") -> Path:
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "a.sv").write_text("module a; endmodule\n")
    (tmp_path / "rtl" / "b.sv").write_text("module b; endmodule\n")
    (tmp_path / "uvmstudio.yaml").write_text(f"""
name: p
top: a
filesets:
  - name: rtl
    files: [rtl/a.sv, rtl/b.sv]
    include_dirs: [rtl]
    defines: [FOO, BAR=2]
tests:
  - name: t_smoke
    tier: L0
  - name: t_rand
    tier: L1
    seeds: 3
  - name: t_neg
    tier: L2
    expect: FAIL
{extra}
""")
    return tmp_path


def test_project_loads_and_resolves_sources(tmp_path, monkeypatch):
    # Hermetic: a UVM_HOME in the ambient environment would otherwise prepend
    # uvm_pkg.sv to the source list and make this test machine-dependent.
    monkeypatch.delenv("UVM_HOME", raising=False)
    root = _write_project(tmp_path)
    p = Project.load(root)
    assert p.name == "p" and p.top == "a"
    files = p.source_files()
    assert [f.name for f in files] == ["a.sv", "b.sv"]      # order preserved
    assert p.defines() == ["FOO", "BAR=2"]
    assert p.include_dirs()[0].name == "rtl"


def test_missing_source_is_an_error_not_a_warning(tmp_path):
    root = _write_project(tmp_path)
    (root / "rtl" / "b.sv").unlink()
    with pytest.raises(ProjectError, match="source file not found"):
        Project.load(root).source_files()


def test_unset_env_var_in_project_is_rejected(tmp_path):
    os.environ.pop("UVMSTUDIO_TEST_UNSET", None)
    (tmp_path / "uvmstudio.yaml").write_text("""
name: p
top: a
filesets:
  - name: rtl
    files: ["${UVMSTUDIO_TEST_UNSET}/x.sv"]
""")
    with pytest.raises(ProjectError, match="is not set"):
        Project.load(tmp_path)


def test_tiers_are_cumulative(tmp_path):
    p = Project.load(_write_project(tmp_path))
    assert {t.name for t in p.tests_in_tier("L0")} == {"t_smoke"}
    assert {t.name for t in p.tests_in_tier("L1")} == {"t_smoke", "t_rand"}
    assert {t.name for t in p.tests_in_tier("L2")} == {"t_smoke", "t_rand", "t_neg"}


def test_negative_test_expect_is_validated():
    with pytest.raises(ProjectError):
        TestSpec(name="x", expect="MAYBE")


# --- hashing --------------------------------------------------------------
def test_source_hash_is_content_and_order_sensitive(tmp_path):
    a = tmp_path / "a.sv"; a.write_text("module a; endmodule\n")
    b = tmp_path / "b.sv"; b.write_text("module b; endmodule\n")
    h1 = hash_sources([a, b])
    assert h1 == hash_sources([a, b])            # stable
    assert h1 != hash_sources([b, a])            # order matters
    b.write_text("module b; wire x; endmodule\n")
    assert h1 != hash_sources([a, b])            # content matters


def test_hash_text_is_stable():
    assert hash_text("abc") == hash_text("abc")
    assert hash_text("abc") != hash_text("abd")


# --- platform / path translation -----------------------------------------
@pytest.mark.parametrize("win,posix", [
    (r"C:\work\vip\tb.sv", "/mnt/c/work/vip/tb.sv"),
    (r"D:\a b\c.sv",       "/mnt/d/a b/c.sv"),
    ("/already/posix",      "/already/posix"),
])
def test_windows_to_wsl_path(win, posix):
    assert windows_to_wsl_path(win) == posix


def test_wsl_to_windows_roundtrip():
    assert wsl_to_windows_path("/mnt/c/work/x.sv") == str(Path(r"C:/work/x.sv").as_posix()).replace("/", "\\") \
        or wsl_to_windows_path("/mnt/c/work/x.sv").lower().startswith("c:")


def test_unc_paths_are_rejected_not_mangled():
    with pytest.raises(ValueError, match="UNC"):
        windows_to_wsl_path(r"\\server\share\x.sv")


def test_exec_context_wraps_argv_for_wsl():
    ctx = ExecContext(host=ExecHost.WSL, wsl_distro="Ubuntu")
    assert ctx.wrap(["verilator", "--version"])[:3] == ["wsl.exe", "-d", "Ubuntu"]
    assert ExecContext(host=ExecHost.NATIVE).wrap(["x"]) == ["x"]


# --- process manager ------------------------------------------------------
def test_process_captures_output_and_returncode():
    pm = ProcessManager()
    r = pm.run([sys.executable, "-c", "print('hello'); raise SystemExit(3)"])
    assert r.returncode == 3
    assert "hello" in r.stdout
    assert not r.ok


def test_process_timeout_kills_and_reports():
    pm = ProcessManager()
    r = pm.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=2)
    assert r.timed_out
    assert not r.ok
    assert r.duration_s < 20


# --- coverage parsing -----------------------------------------------------
def test_verilator_covergroup_record_is_parsed():
    reader = VerilatorCoverageReader()
    rec = ("\x01t\x02covergroup\x01page\x02v_covergroup/cg_t\x01f\x02s.sv"
           "\x01l\x025\x01n\x0229\x01bin\x02lo\x01h\x02cg_t.cp.lo")
    b = reader._parse_record(rec, 7)
    assert b.kind == CoverageKind.COVERGROUP
    assert b.hierarchy == "cg_t.cp.lo"
    assert b.comment == "lo"
    assert b.file == "s.sv" and b.line == 5 and b.count == 7 and b.covered


def test_verilator_line_record_is_parsed():
    reader = VerilatorCoverageReader()
    rec = ("\x01f\x02s.sv\x01l\x023\x01n\x023\x01t\x02line"
           "\x01page\x02v_line/item\x01o\x02blockS3\x01h\x02tb_top")
    b = reader._parse_record(rec, 0)
    assert b.kind == CoverageKind.LINE and b.line == 3 and not b.covered


def test_coverage_merge_unions_bins_and_sums_counts():
    reader = VerilatorCoverageReader()
    mk = lambda name, n: reader._parse_record(
        f"\x01t\x02covergroup\x01page\x02v_covergroup/cg\x01f\x02s.sv"
        f"\x01l\x021\x01n\x021\x01bin\x02{name}\x01h\x02cg.cp.{name}", n)
    a = CoverageDB(bins=[mk("lo", 1), mk("hi", 0)], sources=["a"])
    b = CoverageDB(bins=[mk("lo", 2), mk("hi", 5)], sources=["b"])
    m = CoverageDB.merge([a, b])
    assert len(m.bins) == 2
    counts = {x.comment: x.count for x in m.bins}
    assert counts == {"lo": 3, "hi": 5}
    assert m.functional_score() == (2, 2, 100.0)


def test_functional_score_excludes_code_coverage():
    reader = VerilatorCoverageReader()
    cg = reader._parse_record(
        "\x01t\x02covergroup\x01page\x02v_covergroup/cg\x01f\x02s.sv"
        "\x01l\x021\x01n\x021\x01bin\x02b\x01h\x02cg.cp.b", 1)
    line = reader._parse_record(
        "\x01f\x02s.sv\x01l\x021\x01n\x021\x01t\x02line\x01page\x02v_line/x"
        "\x01o\x02blk\x01h\x02top", 0)
    db = CoverageDB(bins=[cg, line])
    assert db.functional_score() == (1, 1, 100.0)     # not 1/2 = 50%


# --- waveform -------------------------------------------------------------
_VCD = """$date Mon $end
$version test $end
$timescale 1ns $end
$scope module tb $end
$var wire 1 ! clk $end
$var wire 8 " data [7:0] $end
$upscope $end
$enddefinitions $end
#0
0!
b00000000 "
#5
1!
b00001010 "
#10
0!
#15
1!
b11111111 "
"""


def test_vcd_parses_scopes_signals_and_values(tmp_path):
    f = tmp_path / "w.vcd"
    f.write_text(_VCD)
    db = VCDReader().open(f)
    assert db.timescale.startswith("1ns")
    assert db.signal_count == 2
    assert db.scopes() == ["tb"]
    assert db.end_time == 15
    assert db.find("tb.data").width == 8


def test_vcd_value_at_uses_last_change_semantics(tmp_path):
    f = tmp_path / "w.vcd"; f.write_text(_VCD)
    db = VCDReader().open(f)
    assert db.value_at("tb.clk", 0) == "0"
    assert db.value_at("tb.clk", 7) == "1"      # last change at t=5
    assert db.value_at("tb.clk", 12) == "0"     # last change at t=10
    assert db.value_at("tb.data", 6) == "00001010"


def test_vcd_window_includes_entering_value(tmp_path):
    f = tmp_path / "w.vcd"; f.write_text(_VCD)
    db = VCDReader().open(f)
    win = db.window("tb.clk", 6, 12)
    assert win[0] == (6, "1")                    # value entering the window
    assert (10, "0") in win


# --- regression DB --------------------------------------------------------
def _result(status, seed, sig=None):
    return RunResult(status=status, seed=seed, returncode=0, duration_s=0.1,
                     failure_signature=sig or "", reasons=["r"], counters={})


def test_regression_db_records_and_summarises(tmp_path):
    db = RegressionDB(tmp_path / "r.db")
    rid = db.start_regression(name="n", project="p", tier="L1")
    db.record_run(rid, _result(RunStatus.PASS, 1), test="t", uvm_testname="t", tier="L1")
    db.record_run(rid, _result(RunStatus.PASS, 2), test="t", uvm_testname="t", tier="L1")
    s = db.finish_regression(rid)
    assert s == {"total": 2, "passed": 2, "failed": 0, "not_verified": 0,
                 "blocked": 0, "status": "PASS"}


def test_not_verified_is_never_pass(tmp_path):
    db = RegressionDB(tmp_path / "r.db")
    rid = db.start_regression(name="n", project="p", tier="L1")
    db.record_run(rid, _result(RunStatus.PASS, 1), test="t", uvm_testname="t", tier="L1")
    db.record_run(rid, _result(RunStatus.NOT_VERIFIED, 2), test="t",
                  uvm_testname="t", tier="L1")
    s = db.finish_regression(rid)
    assert s["status"] == "NOT_VERIFIED"
    assert s["status"] != "PASS"


def test_failure_signatures_cluster(tmp_path):
    db = RegressionDB(tmp_path / "r.db")
    rid = db.start_regression(name="n", project="p", tier="L1")
    for seed in (1, 2, 3):
        db.record_run(rid, _result(RunStatus.FAIL, seed, "SAME SIG"),
                      test="t", uvm_testname="t", tier="L1")
    db.record_run(rid, _result(RunStatus.FAIL, 4, "OTHER SIG"),
                  test="t", uvm_testname="t", tier="L1")
    db.finish_regression(rid)
    clusters = {c["signature"]: c["occurrences"] for c in db.clusters()}
    assert clusters == {"SAME SIG": 3, "OTHER SIG": 1}


# ---------------------------------------------------------------------------
# Verilator compile-memory split
#
# Verilator concatenates its generated .cpp files into `--build-jobs` buckets,
# so that flag decides peak compiler memory while make's -j decides how many
# compiles run at once. Tying them together (a single `-j`) means a
# low-parallelism build produces the largest possible translation unit — the
# exact opposite of what an operator on a small container intends. Measured on
# UVM 2020.3.1 + a full APB agent stack: 4 buckets -> 2620 MB peak cc1plus,
# 31 buckets -> 1096 MB, and the 31-bucket build was also 7% faster.
# These tests exist so the two knobs are never re-merged.
# ---------------------------------------------------------------------------

def _argv_for(jobs: int, split=None, tmp_path=None):
    from uvmstudio.simulator.base import BuildRequest
    from uvmstudio.simulator.verilator import VerilatorSimulator

    sim = VerilatorSimulator(executable="verilator", jobs=jobs)
    req = BuildRequest(
        files=[Path("a.sv")], top="tb_top",
        build_dir=Path(tmp_path or "/tmp/x"), threads=jobs,
        compile_split=split,
    )
    return sim._build_argv(req, Path(tmp_path or "/tmp/x") / "obj_dir")


def test_translation_unit_split_is_decoupled_from_make_parallelism(tmp_path):
    argv = _argv_for(jobs=2, tmp_path=tmp_path)
    assert "--build-jobs" in argv
    build_jobs = int(argv[argv.index("--build-jobs") + 1])
    makeflags = argv[argv.index("-MAKEFLAGS") + 1]

    # The split must NOT collapse to the job count: that is the memory trap.
    assert build_jobs > 2, argv
    assert makeflags == "-j2", argv


def test_make_parallelism_follows_the_requested_job_count(tmp_path):
    for jobs in (1, 2, 4):
        argv = _argv_for(jobs=jobs, tmp_path=tmp_path)
        assert argv[argv.index("-MAKEFLAGS") + 1] == f"-j{jobs}"
        # ...while the split stays high regardless of parallelism.
        assert int(argv[argv.index("--build-jobs") + 1]) >= 16


def test_explicit_compile_split_overrides_the_default(tmp_path):
    argv = _argv_for(jobs=2, split=48, tmp_path=tmp_path)
    assert argv[argv.index("--build-jobs") + 1] == "48"


def test_compile_split_is_part_of_the_build_cache_key(tmp_path):
    from uvmstudio.simulator.base import BuildRequest

    def key(split):
        return BuildRequest(files=[Path("a.sv")], top="t",
                            build_dir=tmp_path, compile_split=split
                            ).cache_key_parts()

    # Changing the split changes the generated C++, so it must bust the cache.
    assert key(16) != key(32)


def test_oom_reason_names_the_cause_and_a_number():
    from uvmstudio.simulator.verilator import VerilatorSimulator

    log = "g++: fatal error: Killed signal terminated program cc1plus\n"
    reasons = VerilatorSimulator.classify_build_failure(log)
    assert any("OOM" in r for r in reasons), reasons
    assert any(str(VerilatorSimulator.UVM_COMPILE_PEAK_RSS_MB) in r
               for r in reasons), reasons


def test_build_result_carries_reasons_through_to_dict():
    from uvmstudio.simulator.base import BuildResult

    r = BuildResult(ok=False, binary=None, log="", duration_s=0.0,
                    reasons=["compiler killed by the OOM killer"])
    assert r.to_dict()["reasons"] == ["compiler killed by the OOM killer"]


# ---------------------------------------------------------------------------
# CLI routing for the remote backend (defect 23)
#
# `uvmstudio regress --backend remote` used to construct a RegressionRunner,
# which called sim.build() and died on UnsupportedFeature — printing an error
# that told the user to run the exact command they had just run. The CLI must
# route the remote backend to regress_remote() instead.
# ---------------------------------------------------------------------------

def test_cli_routes_remote_backend_to_regress_remote(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uvmstudio.cli import main as cli
    from uvmstudio.simulator.base import RunStatus

    calls = {}

    class FakeRemote:
        name = "remote"
        def exec_host(self):
            return "remote(https://example)"
        def regress_remote(self, project, **kw):
            calls["project"] = project
            calls.update(kw)
            return RunStatus.PASS, {"id": "j1", "result": {
                "regression_id": 9,
                "summary": {"passed": 4, "total": 4, "blocked": 0}}}

    monkeypatch.setattr(cli, "_make_simulator", lambda a: FakeRemote())
    monkeypatch.setattr(cli, "_load_project",
                        lambda a: SimpleNamespace(root=tmp_path / "golden_apb"))

    args = SimpleNamespace(tier="L1", tests=None, seed=7, seeds=None,
                           jobs=2, name=None, json=False)
    rc = cli.cmd_regress(args)

    assert rc == 0
    assert calls["project"] == "golden_apb"   # server resolves by dir name
    assert calls["tier"] == "L1" and calls["seed"] == 7 and calls["jobs"] == 2


def test_cli_remote_non_pass_returns_failure_exit_code(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from uvmstudio.cli import main as cli
    from uvmstudio.simulator.base import RunStatus

    class FakeRemote:
        name = "remote"
        def exec_host(self):
            return "remote(https://example)"
        def regress_remote(self, project, **kw):
            return RunStatus.NOT_VERIFIED, {"id": "j2", "result": {
                "regression_id": 10,
                "summary": {"passed": 0, "total": 4, "blocked": 4}}}

    monkeypatch.setattr(cli, "_make_simulator", lambda a: FakeRemote())
    monkeypatch.setattr(cli, "_load_project",
                        lambda a: SimpleNamespace(root=tmp_path / "p"))

    args = SimpleNamespace(tier="L1", tests=None, seed=None, seeds=None,
                           jobs=1, name=None, json=False)
    assert cli.cmd_regress(args) == cli.EXIT_REGRESSION_NOT_PASSED
