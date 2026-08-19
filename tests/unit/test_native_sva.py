"""Native concurrent SVA (engine N2) tests.

Semantics pinned first, then differential agreement with Verilator on the
same source: a design whose assertion holds must be silent on both engines,
and one whose assertion fires must report a failure on both.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from uvmstudio.core.errors import UnsupportedFeature
from uvmstudio.engine.kernel import Kernel


def _sim(src: str) -> Kernel:
    from pyslang import ast, syntax
    from uvmstudio.engine.interp import Interp

    comp = ast.Compilation()
    comp.addSyntaxTree(syntax.SyntaxTree.fromText(src))
    errs = [str(d) for d in comp.getAllDiagnostics() if d.isError]
    assert not errs, errs
    k = Kernel()
    Interp(comp, k).elaborate()
    k.run()
    return k


HANDSHAKE_OK = """
module tb;
  logic clk = 0, rst_n = 0, req = 0, gnt = 0;
  always #5 clk = ~clk;
  a_hs: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 gnt)
    else $error("handshake violated");
  initial begin
    #12 rst_n = 1;
    @(posedge clk) req <= 1;
    @(posedge clk) req <= 0; gnt <= 1;
    @(posedge clk) gnt <= 0;
    #40 $finish;
  end
endmodule"""

HANDSHAKE_BROKEN = """
module tb;
  logic clk = 0, rst_n = 0, req = 0, gnt = 0;
  always #5 clk = ~clk;
  a_hs: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 gnt)
    else $error("handshake violated");
  initial begin
    #12 rst_n = 1;
    @(posedge clk) req <= 1;
    @(posedge clk) req <= 0;
    #30 $finish;
  end
endmodule"""


def test_passing_implication_is_nonvacuous_pass():
    k = _sim(HANDSHAKE_OK)
    r = k.sva[0]
    assert r.kind == "assert"
    assert r.nonvacuous == 1 and r.passes == 1 and r.fails == 0
    assert not r.vacuous
    assert "".join(k.stdout) == ""


def test_failing_implication_fires_action_block():
    k = _sim(HANDSHAKE_BROKEN)
    r = k.sva[0]
    assert r.fails == 1 and r.passes == 0
    assert "handshake violated" in "".join(k.stdout)


def test_vacuous_assert_is_flagged():
    k = _sim("""
module tb;
  logic clk = 0, rst_n = 0, req = 0, gnt = 0;
  always #5 clk = ~clk;
  a_hs: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 gnt);
  initial begin #12 rst_n = 1; #50 $finish; end
endmodule""")
    r = k.sva[0]
    assert r.attempts > 0 and r.nonvacuous == 0 and r.fails == 0
    assert r.vacuous          # GATE 7: vacuous proves nothing


def test_disable_iff_suppresses_attempts_in_reset():
    k = _sim("""
module tb;
  logic clk = 0, rst_n = 0, req = 0, gnt = 0;
  always #5 clk = ~clk;
  // req high during reset with no gnt: must NOT fire while disabled
  a_hs: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 gnt)
    else $error("fired in reset");
  initial begin
    req = 1;
    #42 $finish;              // rst_n stays low the whole time
  end
endmodule""")
    r = k.sva[0]
    assert r.fails == 0 and r.disabled_ticks > 0


def test_rose_and_nonoverlapped_implication():
    k = _sim("""
module tb;
  logic clk = 0, a = 0, b = 0;
  always #5 clk = ~clk;
  always @(posedge clk) b <= a;
  a_r: assert property (@(posedge clk) $rose(a) |=> b) else $error("late");
  initial begin
    repeat (2) @(posedge clk);
    a <= 1;
    repeat (3) @(posedge clk);
    $finish;
  end
endmodule""")
    r = k.sva[0]
    assert r.nonvacuous == 1 and r.passes == 1 and r.fails == 0


def test_stable_and_past():
    k = _sim("""
module tb;
  logic clk = 0; logic [3:0] d = 4'd7;
  always #5 clk = ~clk;
  a_s: assert property (@(posedge clk) $stable(d)) else $error("moved");
  a_p: assert property (@(posedge clk) $past(d) == 4'd7 || $time < 10);
  initial #47 $finish;
endmodule""")
    stab = [r for r in k.sva if r.name.endswith("a_s")][0]
    assert stab.fails == 0 and stab.passes > 0


def test_multi_step_sequence_delays():
    # a |-> ##1 b ##2 c : b one tick after a, c two ticks after b
    k = _sim("""
module tb;
  logic clk = 0, a = 0, b = 0, c = 0;
  always #5 clk = ~clk;
  a_seq: assert property (@(posedge clk) a |-> ##1 b ##2 c)
    else $error("sequence broken");
  initial begin
    @(posedge clk) a <= 1;
    @(posedge clk) a <= 0; b <= 1;
    @(posedge clk) b <= 0;
    @(posedge clk) c <= 1;
    @(posedge clk) c <= 0;
    #20 $finish;
  end
endmodule""")
    r = k.sva[0]
    assert r.fails == 0 and r.passes == 1, r.to_dict()


def test_cover_property_counts_hits():
    k = _sim("""
module tb;
  logic clk = 0, ev = 0;
  always #5 clk = ~clk;
  c_ev: cover property (@(posedge clk) ev);
  initial begin
    @(posedge clk) ev <= 1;
    @(posedge clk);
    @(posedge clk) ev <= 0;
    #20 $finish;
  end
endmodule""")
    r = k.sva[0]
    assert r.kind == "cover" and r.covered == 2


def test_unsupported_sequence_feature_raises():
    with pytest.raises(UnsupportedFeature):
        _sim("""
module tb;
  logic clk = 0, a = 0, b = 0;
  a_rep: assert property (@(posedge clk) a |-> b [*3]);
  initial #20 $finish;
endmodule""")


# ---------------------------------------------------------------------------
# Differential vs Verilator: failure/silence agreement on identical source
# ---------------------------------------------------------------------------

_VERILATOR = shutil.which("verilator")


def _verilator_fails(src: str, tmp: Path) -> bool:
    (tmp / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--assert",
         "--timescale", "1ns/1ns", "-Wno-DECLFILENAME",
         "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    out = subprocess.run([str(tmp / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    return "%Error" in out.stdout or out.returncode != 0


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_passing_assertion_silent_on_both(tmp_path):
    k = _sim(HANDSHAKE_OK)
    assert k.sva[0].fails == 0
    assert not _verilator_fails(HANDSHAKE_OK, tmp_path)


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_failing_assertion_fires_on_both(tmp_path):
    k = _sim(HANDSHAKE_BROKEN)
    assert k.sva[0].fails >= 1
    assert _verilator_fails(HANDSHAKE_BROKEN, tmp_path)
