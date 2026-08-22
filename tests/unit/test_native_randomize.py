"""Engine N4: z3-backed randomize() — policies pinned, then differential.

The policies under test (stated in engine/randomize.py):
- dist is SOLVED (membership hard + seeded weighted draw) — never ignored
- soft honored when feasible, dropped all-or-nothing on conflict — recorded
- unsat -> randomize() returns 0 and touches nothing
- same seed -> identical draw sequence; different seed -> different
- X/Z in a referenced state variable is an error, never coerced
- randc and every unsupported construct rejected BY NAME
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from uvmstudio.core.errors import UnsupportedFeature
from uvmstudio.engine.kernel import Kernel, SimulationError


def _sim(src: str, seed: int = 7) -> Kernel:
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


def _out(src: str, seed: int = 7) -> str:
    return "".join(_sim(src, seed).stdout)


# ---------------------------------------------------------------------------
# constraint semantics
# ---------------------------------------------------------------------------

CONSTRAINED = """
module tb;
  class c_pkt;
    rand bit [7:0] m_addr;
    rand bit [7:0] m_len;
    rand bit [3:0] m_burst;
    bit [7:0] m_max;
    constraint c_addr { m_addr inside {[8'h10:8'h20], 8'h55}; }
    constraint c_len  { m_len > 8'd2; m_len < m_max; }
    constraint c_rel  { (m_burst == 4'd1) -> (m_len == 8'd4); }
    function void pre_randomize(); m_max = 8'd50; endfunction
  endclass
  int unsigned bad, n55, distinct_prev;
  bit [7:0] prev_addr;
  initial begin
    c_pkt p;
    p = new;
    repeat (200) begin
      if (p.randomize() != 1) bad = bad + 1;
      if (!(p.m_addr inside {[8'h10:8'h20], 8'h55})) bad = bad + 1;
      if (!(p.m_len > 8'd2 && p.m_len < 8'd50)) bad = bad + 1;
      if (p.m_burst == 4'd1 && p.m_len != 8'd4) bad = bad + 1;
      if (p.m_addr == 8'h55) n55 = n55 + 1;
      if (p.m_addr != prev_addr) distinct_prev = distinct_prev + 1;
      prev_addr = p.m_addr;
    end
    $display("bad=%0d moved=%b", bad, distinct_prev > 20);
    $finish;
  end
endmodule"""


def test_inside_relational_and_implication_honored_over_200_draws():
    # every draw legal AND the values actually move (not one fixed model)
    assert _out(CONSTRAINED) == "bad=0 moved=1\n"


def test_unsat_returns_0_and_touches_nothing():
    assert _out("""
module tb;
  class c_u;
    rand bit [3:0] m_v;
    constraint a { m_v > 4'd8; }
    constraint b { m_v < 4'd4; }
  endclass
  initial begin
    c_u u; u = new;
    u.m_v = 4'd5;
    $display("ret=%0d v=%0d", u.randomize(), u.m_v);
    $finish;
  end
endmodule""") == "ret=0 v=5\n"


def test_dist_solved_membership_and_weights():
    # policy pin: dist is SOLVED. 8:2 weighting over 400 draws must land
    # near 320:80 (loose 3-sigma bounds), the zero-weight item must NEVER
    # be drawn, and no draw may leave the dist set.
    out = _out("""
module tb;
  class c_d;
    rand bit [3:0] m_v;
    constraint c { m_v dist { 4'd0 := 8, [4'd1:4'd3] :/ 2, 4'd9 := 0 }; }
  endclass
  int unsigned n0, n123, nbad;
  initial begin
    c_d d; d = new;
    repeat (400) begin
      void'(d.randomize());
      if (d.m_v == 4'd0) n0 = n0 + 1;
      else if (d.m_v >= 4'd1 && d.m_v <= 4'd3) n123 = n123 + 1;
      else nbad = nbad + 1;
    end
    $display("n0=%0d n123=%0d nbad=%0d", n0, n123, nbad);
    $finish;
  end
endmodule""")
    parts = dict(kv.split("=") for kv in out.split())
    n0, n123, nbad = int(parts["n0"]), int(parts["n123"]), int(parts["nbad"])
    assert nbad == 0, "value outside the dist set (or zero-weight) drawn"
    assert n0 + n123 == 400
    assert 270 <= n0 <= 370, f"8:2 weighting not honored (n0={n0})"


def test_soft_honored_when_feasible_dropped_on_conflict():
    assert _out("""
module tb;
  class c_s;
    rand bit [7:0] m_v;
    constraint s { soft m_v == 8'd9; }
  endclass
  class c_s2;
    rand bit [7:0] m_v;
    constraint s { soft m_v == 8'd9; }
    constraint h { m_v > 8'd100; }
  endclass
  initial begin
    c_s a; c_s2 b;
    a = new; b = new;
    void'(a.randomize());
    void'(b.randomize());
    $display("soft=%0d over=%b", a.m_v, b.m_v > 8'd100);
    $finish;
  end
endmodule""") == "soft=9 over=1\n"


def test_if_else_constraint():
    assert _out("""
module tb;
  class c_i;
    rand bit [3:0] m_sel;
    rand bit [7:0] m_len;
    constraint c { if (m_sel == 0) m_len == 8'd1; else m_len > 8'd200; }
  endclass
  int unsigned bad;
  initial begin
    c_i i; i = new;
    repeat (50) begin
      void'(i.randomize());
      if (i.m_sel == 0 && i.m_len != 8'd1) bad = bad + 1;
      if (i.m_sel != 0 && i.m_len <= 8'd200) bad = bad + 1;
    end
    $display("bad=%0d", bad);
    $finish;
  end
endmodule""") == "bad=0\n"


def test_inline_with_constraints_compose_with_block_constraints():
    out = _out("""
module tb;
  class c_w;
    rand bit [7:0] m_v;
    constraint c { m_v > 8'd10; }
  endclass
  int unsigned n11, n12, n13, bad;
  initial begin
    c_w w; w = new;
    repeat (60) begin
      void'(w.randomize() with { m_v < 8'd14; });
      case (w.m_v)
        8'd11: n11 = n11 + 1;
        8'd12: n12 = n12 + 1;
        8'd13: n13 = n13 + 1;
        default: bad = bad + 1;
      endcase
    end
    $display("bad=%0d all3=%b", bad, n11 > 0 && n12 > 0 && n13 > 0);
    $finish;
  end
endmodule""")
    assert out == "bad=0 all3=1\n", out


def test_pre_randomize_state_feeds_constraints_post_runs_on_success_only():
    assert _out("""
module tb;
  class c_h;
    rand bit [7:0] m_v;
    bit [7:0] m_lim;
    bit [7:0] m_posts;
    constraint c { m_v < m_lim; }
    function void pre_randomize();  m_lim = 8'd3; endfunction
    function void post_randomize(); m_posts = m_posts + 1; endfunction
  endclass
  class c_f;
    rand bit [3:0] m_v;
    bit [7:0] m_posts;
    constraint c { m_v > 4'd8; m_v < 4'd4; }
    function void post_randomize(); m_posts = m_posts + 1; endfunction
  endclass
  initial begin
    c_h h; c_f f;
    h = new; f = new;
    repeat (5) void'(h.randomize());
    void'(f.randomize());
    $display("lim_ok=%b posts=%0d unsat_posts=%0d",
             h.m_v < 8'd3, h.m_posts, f.m_posts);
    $finish;
  end
endmodule""") == "lim_ok=1 posts=5 unsat_posts=0\n"


def test_seed_stability_and_seed_sensitivity():
    src = """
module tb;
  class c_r; rand bit [15:0] m_v; endclass
  initial begin
    c_r r; r = new;
    repeat (5) begin void'(r.randomize()); $display("%0d", r.m_v); end
    $finish;
  end
endmodule"""
    a, b, c = _out(src, 42), _out(src, 42), _out(src, 43)
    assert a == b, "same seed must reproduce the exact draw sequence"
    assert a != c, "different seed produced an identical sequence"


def test_nonrand_state_variables_are_never_written():
    assert _out("""
module tb;
  class c_n;
    rand bit [7:0] m_r;
    bit [7:0] m_state;
  endclass
  initial begin
    c_n n; n = new;
    n.m_state = 8'hAB;
    repeat (10) void'(n.randomize());
    $display("state=%0h", n.m_state);
    $finish;
  end
endmodule""") == "state=ab\n"


def test_state_variable_with_x_in_constraint_is_an_error_not_zero():
    with pytest.raises(SimulationError, match="X/Z"):
        _sim("""
module tb;
  class c_x;
    rand bit [7:0] m_v;
    logic [7:0] m_lim;      // four-state, never assigned: X
    constraint c { m_v < m_lim; }
  endclass
  initial begin
    c_x x; x = new;
    void'(x.randomize());
    $finish;
  end
endmodule""")


def test_partial_randomize_arguments_rejected_by_name():
    with pytest.raises(UnsupportedFeature, match="variable arguments"):
        _sim("""
module tb;
  class c_p; rand bit [3:0] a; rand bit [3:0] b; endclass
  initial begin
    c_p p; p = new;
    void'(p.randomize(a));
    $finish;
  end
endmodule""")


def test_no_rand_vars_randomize_succeeds_trivially():
    assert _out("""
module tb;
  class c_e; bit [3:0] m_v; endclass
  initial begin
    c_e e; e = new;
    $display("ret=%0d", e.randomize());
    $finish;
  end
endmodule""") == "ret=1\n"


def test_signed_constraints_and_signed_checks_agree():
    # defect 33: unary minus dropped signedness, so the CHECK `x.v > -5`
    # compared unsigned and flagged legal draws. Both the solver and the
    # procedural comparison must be signed-correct.
    assert _out("""
module tb;
  class c; rand bit signed [7:0] v;
    constraint s { v > -8'sd5; v < 8'sd5; } endclass
  int unsigned bad;
  initial begin c x; x = new;
    repeat (50) begin
      void'(x.randomize());
      if (!(x.v > -5 && x.v < 5)) bad = bad + 1;
    end
    $display("bad=%0d", bad); $finish; end
endmodule""") == "bad=0\n"


@pytest.mark.parametrize("name,src", [
    ("solve.*before", "constraint o { solve a before b; a < b; }"),
    ("unique|Uniqueness", "constraint u { unique {a, b}; }"),
])
def test_unimplemented_constraint_kinds_rejected_by_name(name, src):
    import re
    with pytest.raises(UnsupportedFeature) as exc:
        _sim(f"""
module tb;
  class c; rand bit [3:0] a; rand bit [3:0] b;
    {src} endclass
  initial begin c x; x = new; void'(x.randomize()); $finish; end
endmodule""")
    assert re.search(name, str(exc.value), re.IGNORECASE), str(exc.value)


def test_rand_unpacked_array_rejected_by_name_not_z3_crash():
    # the sweep caught a raw Z3Exception (BitVec width 0) here — the
    # rejection must be ours and must name the variable
    with pytest.raises(UnsupportedFeature, match="not a packed integral"):
        _sim("""
module tb;
  class c; rand bit [3:0] a [4];
    constraint f { foreach (a[i]) a[i] < 4'd8; } endclass
  initial begin c x; x = new; void'(x.randomize()); $finish; end
endmodule""")


# ---------------------------------------------------------------------------
# Differential vs Verilator 5.050
# ---------------------------------------------------------------------------

_VERILATOR = shutil.which("verilator")


def _run_verilator(src: str, tmp: Path, seed: int = 7) -> str:
    (tmp / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--timescale", "1ns/1ns",
         "-Wno-DECLFILENAME", "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    out = subprocess.run([str(tmp / "obj_dir" / "simv"),
                          f"+verilator+seed+{seed}"],
                         capture_output=True, text=True, timeout=120)
    # drop Verilator chrome: "- ..." runtime notes and %Warning diagnostics
    # (UNSATCONSTR prints to stdout on a failed randomize — the SEMANTIC
    # agreement is the returned 0, which the design itself prints)
    return "".join(ln + "\n" for ln in out.stdout.splitlines()
                   if not ln.startswith(("- ", "%Warning")))


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_constraint_legality(tmp_path):
    # draw values differ by solver; LEGALITY of every draw must agree.
    # The source self-checks and prints only the verdict line.
    native = _out(CONSTRAINED)
    reference = _run_verilator(CONSTRAINED, tmp_path)
    assert native == reference == "bad=0 moved=1\n", \
        f"native={native!r} verilator={reference!r}"


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_dist_membership_and_unsat(tmp_path):
    # Historical factory catalog claimed Verilator ignores dist weights.
    # Re-measured on 5.050 (2026-08-22): dist IS honored in class
    # constraints — the catalog entry is corrected in FEATURE_STATUS.md.
    # Both simulators must agree: membership never violated, zero-weight
    # item never drawn, and the contradictory class is unsat on both.
    src = """
module tb;
  class c_d;
    rand bit [3:0] m_v;
    constraint c { m_v dist { 4'd0 := 8, [4'd1:4'd3] :/ 2, 4'd9 := 0 }; }
  endclass
  class c_u;
    rand bit [3:0] m_v;
    constraint a { m_v > 4'd8; }
    constraint b { m_v < 4'd4; }
  endclass
  int unsigned nbad;
  initial begin
    c_d d; c_u u;
    d = new; u = new;
    repeat (400) begin
      void'(d.randomize());
      if (!(d.m_v inside {4'd0, [4'd1:4'd3]})) nbad = nbad + 1;
    end
    $display("nbad=%0d unsat_ret=%0d", nbad, u.randomize());
    $finish;
  end
endmodule"""
    native = _out(src)
    reference = _run_verilator(src, tmp_path)
    assert native == reference == "nbad=0 unsat_ret=0\n", \
        f"native={native!r} verilator={reference!r}"
