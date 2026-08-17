"""SystemVerilog frontend conformance tests.

These assert *language semantics*, not implementation details: each test pins a
specific IEEE 1800 construct and the IR the platform must produce for it. They
are the regression net that lets the frontend be swapped (slang -> Surelog ->
native) without silently losing capability.

A construct that is not supported must FAIL these tests, not be skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uvmstudio.language.frontend import CompileRequest, get_frontend
from uvmstudio.language.ir import Direction, RandKind, UnitKind


@pytest.fixture(scope="module")
def fe():
    return get_frontend("slang")


def compile_src(fe, tmp_path: Path, src: str, top: str | None = None, **kw):
    f = tmp_path / "t.sv"
    f.write_text(src, encoding="utf-8")
    return fe.compile(CompileRequest(files=[f], top=top, **kw))


# --- basic structure ------------------------------------------------------
def test_module_ports_and_directions(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        module m(input logic clk, input logic [7:0] d, output logic q, inout wire z);
          always_ff @(posedge clk) q <= |d;
        endmodule
    """, top="m")
    assert res.ok, res.diagnostics.format()
    unit = res.design.units["m"]
    dirs = {p.name: p.direction for p in unit.ports}
    assert dirs["clk"] is Direction.INPUT
    assert dirs["d"] is Direction.INPUT
    assert dirs["q"] is Direction.OUTPUT
    assert dirs["z"] is Direction.INOUT


def test_package_and_class_extraction(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        package p;
          class base_c;
            int m_x;
            function new(); endfunction
          endclass
          class derived_c extends base_c;
            rand  bit [7:0] m_a;
            randc bit [3:0] m_b;
            constraint c_a { m_a > 8'd10; }
            constraint c_b { m_b inside {[0:9]}; }
          endclass
        endpackage
        module top; import p::*; endmodule
    """, top="top")
    assert res.ok, res.diagnostics.format()
    d = res.design
    assert "p" in d.packages

    derived = d.find_class("derived_c")
    assert derived is not None
    assert derived.base_class == "base_c"

    rands = {v.name: v.rand_kind for v in derived.properties if v.rand_kind is not RandKind.NONE}
    assert rands == {"m_a": RandKind.RAND, "m_b": RandKind.RANDC}
    assert {c.name for c in derived.constraints} == {"c_a", "c_b"}


def test_interface_and_modport(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        interface bus_if(input logic clk);
          logic vld, rdy;
          modport mst(output vld, input rdy);
          modport slv(input vld, output rdy);
        endinterface
        module top;
          logic clk = 0;
          bus_if u_if(clk);
        endmodule
    """, top="top")
    assert res.ok, res.diagnostics.format()
    unit = res.design.units["bus_if"]
    assert unit.kind is UnitKind.INTERFACE
    mp = {m.name: dict(m.ports) for m in unit.modports}
    assert mp["mst"]["vld"] == "output"
    assert mp["mst"]["rdy"] == "input"
    assert mp["slv"]["vld"] == "input"


def test_elaborated_hierarchy_paths(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        module leaf(input logic a, output logic b); assign b = ~a; endmodule
        module mid(input logic a, output logic b); leaf u_leaf(.a(a), .b(b)); endmodule
        module top; logic x = 0, y; mid u_mid(.a(x), .b(y)); endmodule
    """, top="top")
    assert res.ok, res.diagnostics.format()
    paths = {n.path for n in res.design.all_instances()}
    assert paths == {"top", "top.u_mid", "top.u_mid.u_leaf"}


# --- coverage constructs --------------------------------------------------
def test_covergroup_coverpoints_and_bins(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        module top;
          logic clk = 0;
          logic [3:0] v;
          covergroup cg @(posedge clk);
            cp_v: coverpoint v {
              bins low   = {[0:3]};
              bins mid   = {[4:11]};
              bins high  = {[12:15]};
              illegal_bins bad = {4'hF};
            }
          endgroup
          cg u_cg = new();
        endmodule
    """, top="top")
    assert res.ok, res.diagnostics.format()
    cgs = res.design.all_covergroups()
    assert len(cgs) == 1
    cp = cgs[0].coverpoints[0]
    assert cp.name == "cp_v"
    names = {b.name for b in cp.bins}
    assert {"low", "mid", "high", "bad"} <= names
    assert cgs[0].has_sampling_event is True


def test_cross_coverage_is_detected(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        module top;
          logic clk = 0;
          logic [1:0] a, b;
          covergroup cg @(posedge clk);
            cp_a: coverpoint a { bins a0 = {0}; bins a1 = {1}; }
            cp_b: coverpoint b { bins b0 = {0}; bins b1 = {1}; }
            x_ab: cross cp_a, cp_b;
          endgroup
          cg u_cg = new();
        endmodule
    """, top="top")
    assert res.ok, res.diagnostics.format()
    cg = res.design.all_covergroups()[0]
    crosses = [cp for cp in cg.coverpoints if cp.is_cross]
    assert len(crosses) == 1 and crosses[0].name == "x_ab"


# --- error detection (must NOT silently pass) -----------------------------
def test_undeclared_identifier_is_an_error(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        module m; logic a; initial no_such_signal = 1; endmodule
    """, top="m")
    assert not res.ok
    assert res.error_count >= 1
    assert any("undeclared" in d.message.lower() for d in res.diagnostics.errors)


def test_type_mismatch_is_reported(fe, tmp_path):
    res = compile_src(fe, tmp_path, """
        package p; typedef enum {A, B} e_t; endpackage
        module m;
          import p::*;
          e_t e;
          initial e = 3.5;      // real to enum
        endmodule
    """, top="m")
    assert not res.ok or res.warning_count >= 1


def test_diagnostic_carries_source_location(fe, tmp_path):
    res = compile_src(fe, tmp_path, "module m;\n  logic a;\n  initial b = 1;\nendmodule\n", top="m")
    assert not res.ok
    d = res.diagnostics.errors[0]
    assert d.location is not None
    assert d.location.line == 3
    assert d.location.column > 0
    assert d.source_line.strip().startswith("initial")


def test_warning_suppression_is_explicit(fe, tmp_path):
    src = "module m;\n  initial begin int x = 1; end\nendmodule\n"
    loud = compile_src(fe, tmp_path, src, top="m")
    quiet = compile_src(fe, tmp_path, src, top="m",
                        suppress_warnings=["explicit-static"])
    assert loud.warning_count >= 1
    assert quiet.warning_count < loud.warning_count


# --- language standard selection -----------------------------------------
@pytest.mark.parametrize("std", ["1800-2017", "1800-2023"])
def test_language_standards_are_accepted(fe, tmp_path, std):
    res = compile_src(fe, tmp_path,
                      "module m; initial $display(\"hi\"); endmodule\n",
                      top="m", language_standard=std)
    assert res.ok, res.diagnostics.format()
    assert res.design.language_standard == std


def test_command_line_is_recorded_for_reproducibility(fe, tmp_path):
    f = tmp_path / "t.sv"
    f.write_text("module m; endmodule\n")
    req = CompileRequest(files=[f], top="m", defines=["FOO=1"],
                         include_dirs=[tmp_path])
    cmd = fe.build_command_line(req)
    assert "+define+FOO=1" in cmd
    assert f"+incdir+{tmp_path}" in cmd
    assert "--top m" in cmd
    assert "--std=1800-2017" in cmd


def test_capabilities_are_declared_not_assumed(fe):
    caps = fe.capabilities()
    assert caps["parse"].value == "SUPPORTED"
    assert caps["elaboration"].value == "SUPPORTED"
    # Anything not implemented must be explicitly PLANNED, never absent.
    assert caps["vpi"].value == "PLANNED"
    assert caps["constraint_expression_ir"].value == "PLANNED"
