"""Engine N5: functional covergroups on the native kernel.

Semantics pinned deterministically first: bin hit counts, ignore/illegal
handling, cross bins, X samples, auto bins, and the coverage percentage.
Then a portability differential: the same covergroup source must compile and
run to $finish on Verilator 5.050 (our covergroup subset is real, portable
SV — not an invented dialect).

A percentage-level differential vs `verilator_coverage` is NOT claimed: the
auto-bin partitioning and cross-bin construction are implementation-defined
between simulators, so only explicit-bin semantics are pinned here. Stated,
not hidden.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from uvmstudio.engine.kernel import Kernel, SimulationError


def _sim(src: str, seed: int = 1) -> Kernel:
    from pyslang import ast, syntax
    from uvmstudio.engine.interp import Interp

    comp = ast.Compilation()
    comp.addSyntaxTree(syntax.SyntaxTree.fromText(src))
    errs = [str(d) for d in comp.getAllDiagnostics() if d.isError]
    assert not errs, errs
    k = Kernel()
    Interp(comp, k, seed=seed).elaborate()
    k.run()
    return k


def _cg(k: Kernel):
    assert k.covergroups, "no covergroup instances registered"
    return k.covergroups[0].report()


# ---------------------------------------------------------------------------
# explicit bins, sampled by .sample()
# ---------------------------------------------------------------------------

def test_explicit_bins_hit_counts():
    k = _sim("""
module tb;
  bit [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v {
      bins low  = {[0:3]};
      bins mid  = {[4:11]};
      bins high = {[12:15]};
    }
  endgroup
  cg u = new;
  initial begin
    m_v = 4'd1;  u.sample();
    m_v = 4'd2;  u.sample();
    m_v = 4'd7;  u.sample();
    m_v = 4'd14; u.sample();
    $finish;
  end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert cp["bins"] == {"low": 2, "mid": 1, "high": 1}
    assert (cp["covered"], cp["total"]) == (3, 3)
    assert cp["percent"] == 100.0


def test_partial_coverage_percent():
    k = _sim("""
module tb;
  bit [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v {
      bins low  = {[0:3]};
      bins mid  = {[4:11]};
      bins high = {[12:15]};
    }
  endgroup
  cg u = new;
  initial begin m_v = 4'd1; u.sample(); $finish; end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert (cp["covered"], cp["total"]) == (1, 3)
    assert cp["percent"] == pytest.approx(33.33, abs=0.01)


def test_ignore_bins_excluded_from_denominator():
    k = _sim("""
module tb;
  bit [2:0] m_v;
  covergroup cg;
    cp: coverpoint m_v {
      bins a = {0};
      bins b = {1};
      ignore_bins ig = {[2:7]};
    }
  endgroup
  cg u = new;
  initial begin
    m_v = 0; u.sample();
    m_v = 1; u.sample();
    m_v = 5; u.sample();     // ignored — must not change coverage
    $finish;
  end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert (cp["covered"], cp["total"]) == (2, 2)
    assert cp["percent"] == 100.0


def test_illegal_bin_hit_is_an_error():
    with pytest.raises(SimulationError, match="illegal_bins"):
        _sim("""
module tb;
  bit m_err;
  covergroup cg;
    cp: coverpoint m_err { bins ok = {0}; illegal_bins bad = {1}; }
  endgroup
  cg u = new;
  initial begin m_err = 1'b1; u.sample(); $finish; end
endmodule""")


def test_x_sample_hits_nothing():
    k = _sim("""
module tb;
  logic [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v { bins z = {0}; bins a = {5}; }
  endgroup
  cg u = new;
  initial begin
    u.sample();              // m_v is X
    m_v = 5; u.sample();
    $finish;
  end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert cp["unsampled_x"] == 1
    assert (cp["covered"], cp["total"]) == (1, 2)
    assert cp["bins"] == {"z": 0, "a": 1}


def test_auto_bins_one_per_value_small_width():
    k = _sim("""
module tb;
  bit [1:0] m_v;
  covergroup cg;
    cp: coverpoint m_v;      // no explicit bins -> 4 auto bins
  endgroup
  cg u = new;
  initial begin
    m_v = 0; u.sample();
    m_v = 1; u.sample();
    m_v = 3; u.sample();
    $finish;
  end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert cp["total"] == 4                 # 2**2 <= auto_bin_max
    assert cp["covered"] == 3               # value 2 never sampled
    assert cp["percent"] == 75.0


def test_auto_bins_capped_at_64_for_wide_points():
    k = _sim("""
module tb;
  bit [15:0] m_v;
  covergroup cg;
    cp: coverpoint m_v;      // 2**16 values -> capped at 64 auto bins
  endgroup
  cg u = new;
  initial begin m_v = 0; u.sample(); $finish; end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    assert cp["total"] == 64


def test_cross_coverage_counts_distinct_pairs():
    k = _sim("""
module tb;
  bit [1:0] m_a;
  bit m_b;
  covergroup cg;
    cp_a: coverpoint m_a { bins lo = {[0:1]}; bins hi = {[2:3]}; }
    cp_b: coverpoint m_b { bins z = {0}; bins o = {1}; }
    x: cross cp_a, cp_b;
  endgroup
  cg u = new;
  initial begin
    m_a = 0; m_b = 0; u.sample();    // (lo, z)
    m_a = 3; m_b = 1; u.sample();    // (hi, o)
    m_a = 1; m_b = 0; u.sample();    // (lo, z) again
    $finish;
  end
endmodule""")
    x = _cg(k)["crosses"]["x"]
    assert (x["covered"], x["total"]) == (2, 4)   # 2 distinct of 2x2
    assert x["percent"] == 50.0


def test_event_sampled_covergroup():
    k = _sim("""
module tb;
  bit clk = 0;
  bit [1:0] m_v;
  always #5 clk = ~clk;
  covergroup cg @(posedge clk);
    cp: coverpoint m_v { bins a = {0}; bins b = {1}; bins c = {2}; bins d = {3}; }
  endgroup
  cg u = new;
  initial begin
    @(negedge clk) m_v = 1; @(posedge clk);
    @(negedge clk) m_v = 2; @(posedge clk);
    #1 $finish;
  end
endmodule""")
    cp = _cg(k)["coverpoints"]["cp"]
    # m_v defaults to 0 at the first posedge, then 1, then 2 -> a,b,c hit
    assert cp["bins"]["b"] >= 1 and cp["bins"]["c"] >= 1
    assert cp["covered"] >= 3


def test_seed_independent_determinism():
    src = """
module tb;
  bit [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v { bins lo = {[0:7]}; bins hi = {[8:15]}; }
  endgroup
  cg u = new;
  initial begin m_v = 4'd3; u.sample(); m_v = 4'd12; u.sample(); $finish; end
endmodule"""
    a = _cg(_sim(src, seed=1))
    b = _cg(_sim(src, seed=999))
    assert a == b                # coverage is a function of stimulus, not seed


# ---------------------------------------------------------------------------
# Portability differential vs Verilator 5.050
# ---------------------------------------------------------------------------

from tests.unit._toolchain import find_verilator
_VERILATOR = find_verilator()

_CG_SRC = """
module tb;
  bit clk = 0;
  bit [3:0] m_v;
  always #5 clk = ~clk;
  covergroup cg @(posedge clk);
    cp: coverpoint m_v {
      bins low  = {[0:3]};
      bins mid  = {[4:11]};
      bins high = {[12:15]};
    }
  endgroup
  cg u = new;
  initial begin
    @(negedge clk) m_v = 4'd2;  @(posedge clk);
    @(negedge clk) m_v = 4'd7;  @(posedge clk);
    @(negedge clk) m_v = 4'd14; @(posedge clk);
    #1 $display("done"); $finish;
  end
endmodule"""


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_covergroup_source_runs_on_verilator(tmp_path):
    # our covergroup subset is portable SV: Verilator 5.050 compiles it with
    # --coverage-user and runs to $finish. (Percentage parity is not claimed —
    # bin/cross construction is implementation-defined; see module docstring.)
    native = "".join(_sim(_CG_SRC).stdout)
    assert "done" in native
    (tmp_path / "d.sv").write_text(_CG_SRC)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--coverage-user",
         "--timescale", "1ns/1ns", "-Wno-DECLFILENAME",
         "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp_path, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    out = subprocess.run([str(tmp_path / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    assert "done" in out.stdout


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_illegal_bin_flagged_on_both(tmp_path):
    src = """
module tb;
  bit clk = 0;
  bit [1:0] m_v;
  always #5 clk = ~clk;
  covergroup cg @(posedge clk);
    cp: coverpoint m_v { bins ok = {[0:2]}; illegal_bins bad = {3}; }
  endgroup
  cg u = new;
  initial begin
    @(negedge clk) m_v = 2'd3; @(posedge clk);   // hits illegal
    #1 $finish;
  end
endmodule"""
    # native: illegal hit is a diagnosed failure
    with pytest.raises(SimulationError, match="illegal"):
        _sim(src)
    # Verilator: the same illegal_bins hit is reported as an error at runtime
    (tmp_path / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--coverage-user",
         "--timescale", "1ns/1ns", "-Wno-DECLFILENAME",
         "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp_path, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        pytest.skip("Verilator rejected illegal_bins at compile: "
                    + r.stderr.splitlines()[0][:100])
    out = subprocess.run([str(tmp_path / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    assert "illegal" in (out.stdout + out.stderr).lower() or out.returncode != 0


# ---------------------------------------------------------------------------
# never-silent: unsupported bin kinds raise by name (not empty/wrong bins)
# ---------------------------------------------------------------------------

def test_wildcard_bins_rejected_by_name():
    from uvmstudio.core.errors import UnsupportedFeature
    with pytest.raises(UnsupportedFeature, match="wildcard"):
        _sim("""
module tb;
  bit [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v { wildcard bins w = {4'b10?0}; }
  endgroup
  cg u = new;
  initial begin m_v = 0; u.sample(); $finish; end
endmodule""")


def test_transition_bins_rejected_by_name():
    from uvmstudio.core.errors import UnsupportedFeature
    with pytest.raises(UnsupportedFeature, match="transition"):
        _sim("""
module tb;
  bit [3:0] m_v;
  covergroup cg;
    cp: coverpoint m_v { bins t = (0 => 1 => 2); }
  endgroup
  cg u = new;
  initial begin m_v = 0; u.sample(); $finish; end
endmodule""")
