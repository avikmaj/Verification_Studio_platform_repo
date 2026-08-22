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

from tests.unit._toolchain import find_verilator
_VERILATOR = find_verilator()


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


# ---------------------------------------------------------------------------
# Widened differential suite (post-N4 phase): exact failure-count and
# behavior agreement per construct, not just boolean fired/silent.
# A construct Verilator cannot compile skips WITH THE REASON NAMED — a skip
# is never counted as agreement.
# ---------------------------------------------------------------------------


def _verilator_out(src: str, tmp: Path):
    """Compile+run under Verilator --assert; returns (stdout, n_errors).
    Skips the calling test if Verilator rejects the construct."""
    (tmp / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--assert",
         "--timescale", "1ns/1ns", "-Wno-DECLFILENAME",
         "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        if "UNSUPPORTED" in r.stderr:
            pytest.skip("Verilator lacks this construct: "
                        + r.stderr.splitlines()[0][:120])
        raise AssertionError(r.stderr[-1500:])
    out = subprocess.run([str(tmp / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    n_err = sum(1 for ln in out.stdout.splitlines() if ln.startswith("%Error"))
    clean = "".join(ln + "\n" for ln in out.stdout.splitlines()
                    if not ln.startswith(("- ", "%Error")))
    return clean, n_err


NONOVERLAP_OK = """
module tb;
  logic clk = 0, a = 0, b = 0;
  always #5 clk = ~clk;
  a_no: assert property (@(posedge clk) a |=> b) else $error("no b");
  initial begin
    @(posedge clk) a <= 1;
    @(posedge clk) a <= 0; b <= 1;
    @(posedge clk) b <= 0;
    #20 $finish;
  end
endmodule"""

NONOVERLAP_BROKEN = """
module tb;
  logic clk = 0, a = 0, b = 0;
  always #5 clk = ~clk;
  a_no: assert property (@(posedge clk) a |=> b) else $error("no b");
  initial begin
    @(posedge clk) a <= 1;
    @(posedge clk) a <= 0;
    #30 $finish;
  end
endmodule"""


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_nonoverlapped_pass(tmp_path):
    k = _sim(NONOVERLAP_OK)
    assert k.sva[0].fails == 0 and k.sva[0].passes == 1
    _, n_err = _verilator_out(NONOVERLAP_OK, tmp_path)
    assert n_err == 0


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_nonoverlapped_fail_count(tmp_path):
    k = _sim(NONOVERLAP_BROKEN)
    _, n_err = _verilator_out(NONOVERLAP_BROKEN, tmp_path)
    assert (k.sva[0].fails, n_err) == (1, 1), \
        f"native fails={k.sva[0].fails} verilator errors={n_err}"


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_disable_iff_aborts_inflight_attempt(tmp_path):
    # the obligation (##1 gnt) is pending when reset asserts: neither
    # simulator may report a failure for an attempt killed by disable iff
    src = """
module tb;
  logic clk = 0, rst_n = 1, req = 0, gnt = 0;
  always #5 clk = ~clk;
  a_hs: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 gnt)
    else $error("fired though disabled");
  initial begin
    @(posedge clk) req <= 1;
    @(posedge clk) req <= 0; rst_n <= 0;   // kill the pending attempt
    @(posedge clk);
    #20 $finish;
  end
endmodule"""
    k = _sim(src)
    assert k.sva[0].fails == 0
    _, n_err = _verilator_out(src, tmp_path)
    assert n_err == 0


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_multistep_chain_fail_count(tmp_path):
    # a |-> ##1 b ##2 c with c never arriving: exactly one failure on both
    src = """
module tb;
  logic clk = 0, a = 0, b = 0, c = 0;
  always #5 clk = ~clk;
  a_seq: assert property (@(posedge clk) a |-> ##1 b ##2 c)
    else $error("chain broken");
  initial begin
    @(posedge clk) a <= 1;
    @(posedge clk) a <= 0; b <= 1;
    @(posedge clk) b <= 0;
    repeat (3) @(posedge clk);
    $finish;
  end
endmodule"""
    k = _sim(src)
    _, n_err = _verilator_out(src, tmp_path)
    assert (k.sva[0].fails, n_err) == (1, 1)


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_rose_antecedent_fail(tmp_path):
    src = """
module tb;
  logic clk = 0, a = 0, b = 0;
  always #5 clk = ~clk;
  a_r: assert property (@(posedge clk) $rose(a) |=> b) else $error("late b");
  initial begin
    repeat (2) @(posedge clk);
    a <= 1;
    repeat (3) @(posedge clk);
    $finish;
  end
endmodule"""
    k = _sim(src)
    _, n_err = _verilator_out(src, tmp_path)
    assert (k.sva[0].fails, n_err) == (1, 1)


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_stable_violation_count(tmp_path):
    # d changes exactly once: exactly one $stable failure on both
    src = """
module tb;
  logic clk = 0; logic [3:0] d = 4'd7;
  always #5 clk = ~clk;
  a_s: assert property (@(posedge clk) $stable(d)) else $error("moved");
  initial begin
    repeat (2) @(posedge clk);
    d <= 4'd9;
    repeat (2) @(posedge clk);
    $finish;
  end
endmodule"""
    k = _sim(src)
    _, n_err = _verilator_out(src, tmp_path)
    assert (k.sva[0].fails, n_err) == (1, 1)


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_pipelined_attempts_both_fail(tmp_path):
    # two overlapping req attempts, gnt never comes: TWO failures on both —
    # per-attempt tracking, not a merged verdict (red-team RT-P-004 case,
    # now differential)
    src = """
module tb;
  logic clk = 0, req = 0, gnt = 0;
  always #5 clk = ~clk;
  a_p: assert property (@(posedge clk) req |-> ##1 gnt)
    else $error("no gnt");
  initial begin
    @(posedge clk) req <= 1;
    @(posedge clk);              // req still 1: second attempt starts
    @(posedge clk) req <= 0;
    repeat (2) @(posedge clk);
    $finish;
  end
endmodule"""
    k = _sim(src)
    _, n_err = _verilator_out(src, tmp_path)
    # LRM 16.14.6: two antecedent matches -> two independent attempts ->
    # two failures. Native reports 2. Verilator 5.050 MERGES overlapping
    # identical obligations and reports 1 — a measured deviation
    # (2026-08-22), recorded in FEATURE_STATUS.md. Both sides pinned so a
    # change in either simulator surfaces here.
    assert k.sva[0].fails == 2, "native must track attempts independently"
    assert n_err == 1, "Verilator deviation changed — re-measure and re-date"


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_cover_pass_action_output(tmp_path):
    # defect 34: cover pass-actions were silently dropped. Now the HIT
    # lines must agree byte-for-byte with Verilator.
    src = """
module tb;
  logic clk = 0, ev = 0;
  always #5 clk = ~clk;
  c_ev: cover property (@(posedge clk) ev) $display("HIT at %0t", $time);
  initial begin
    @(posedge clk) ev <= 1;
    @(posedge clk);
    @(posedge clk) ev <= 0;
    #20 $finish;
  end
endmodule"""
    k = _sim(src)
    native = "".join(k.stdout)
    reference, n_err = _verilator_out(src, tmp_path)
    assert n_err == 0
    # LRM 16.14: the cover pass statement executes on each match. Native
    # prints both HITs. Verilator 5.050 accepts the statement and SILENTLY
    # drops it (empty output) — a measured deviation (2026-08-22), recorded
    # in FEATURE_STATUS.md. Both sides pinned.
    assert native == "HIT at 15\nHIT at 25\n", native
    assert k.sva[0].covered == 2
    assert reference == "", \
        "Verilator now executes cover pass-actions — re-measure, re-date"


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not found")
def test_differential_past_property(tmp_path):
    src = """
module tb;
  logic clk = 0; logic [3:0] d = 4'd0;
  always #5 clk = ~clk;
  always @(posedge clk) d <= d + 1;
  a_p: assert property (@(posedge clk) d == 4'd0 || d == $past(d) + 4'd1)
    else $error("past broken");
  initial #85 $finish;
endmodule"""
    k = _sim(src)
    _, n_err = _verilator_out(src, tmp_path)
    assert (k.sva[0].fails, n_err) == (0, 0)
    assert k.sva[0].passes > 0
