"""Native simulation engine tests.

Three layers, in order of trust:
1. FourState unit tests — LRM truth tables, exact.
2. Kernel/interpreter behavior — semantics the LRM prescribes.
3. Differential tests vs Verilator — same source, same printed output.
   These are the tests that make the engine's claims real; they skip only
   when Verilator is genuinely absent.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uvmstudio.engine.fourstate import FourState
from uvmstudio.engine.kernel import Kernel, SimulationError
from uvmstudio.engine.vcd_writer import VCDWriter
from uvmstudio.waveform.vcd import VCDReader


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


# ---------------------------------------------------------------------------
# FourState
# ---------------------------------------------------------------------------

def test_fourstate_literals_parse_x_and_z():
    v = FourState.from_svint_str("4'b101x", 4)
    assert v.to_bin() == "101x"
    z = FourState.from_svint_str("4'bz01z", 4)
    assert z.to_bin() == "z01z"


def test_fourstate_and_or_tables():
    x = FourState.from_svint_str("1'bx", 1)
    zero = FourState.from_int(0, 1)
    one = FourState.from_int(1, 1)
    assert zero.bit_and(x, 1).to_bin() == "0"      # 0 & x = 0
    assert one.bit_and(x, 1).to_bin() == "x"       # 1 & x = x
    assert one.bit_or(x, 1).to_bin() == "1"        # 1 | x = 1
    assert zero.bit_or(x, 1).to_bin() == "x"       # 0 | x = x


def test_fourstate_arithmetic_is_x_pessimistic():
    a = FourState.from_svint_str("4'b00x0", 4)
    b = FourState.from_int(3, 4)
    assert a.add(b, 4).to_bin() == "xxxx"
    assert b.add(b, 4).to_int() == 6


def test_fourstate_eq_vs_case_eq():
    a = FourState.from_svint_str("4'b10x0", 4)
    b = FourState.from_svint_str("4'b10x0", 4)
    assert a.eq(b).has_unknown                     # == with X -> x
    assert a.case_eq(b).to_int() == 1              # === compares X literally


def test_fourstate_signed_resize_sign_extends():
    v = FourState.from_int(0b1000, 4, signed=True)
    assert v.resize(8).to_bin() == "11111000"
    u = FourState.from_int(0b1000, 4, signed=False)
    assert u.resize(8).to_bin() == "00001000"


def test_fourstate_division_by_zero_is_x():
    a = FourState.from_int(7, 4)
    assert a.div(FourState.from_int(0, 4), 4).to_bin() == "xxxx"


# ---------------------------------------------------------------------------
# Kernel / interpreter semantics
# ---------------------------------------------------------------------------

def test_counter_counts_and_finishes():
    k = _sim("""
module tb;
  logic clk = 0; logic [7:0] q = 0;
  always #5 clk = ~clk;
  always @(posedge clk) q <= q + 1;
  initial begin #52 $display("q=%0d", q); $finish; end
endmodule""")
    assert "".join(k.stdout) == "q=5\n"
    assert k.finish_time == 52


def test_nba_reads_old_value_within_timestep():
    # classic swap: only correct if NBA updates apply after the active region
    k = _sim("""
module tb;
  logic clk = 0; logic [3:0] a = 1, b = 2;
  always #5 clk = ~clk;
  always @(posedge clk) a <= b;
  always @(posedge clk) b <= a;
  initial begin #7 $display("a=%0d b=%0d", a, b); $finish; end
endmodule""")
    assert "".join(k.stdout) == "a=2 b=1\n"


def test_blocking_vs_nonblocking_ordering():
    k = _sim("""
module tb;
  logic [3:0] x;
  initial begin
    x = 4'd1;
    x <= 4'd9;
    $display("before=%0d", x);   // blocking value still visible
    #1 $display("after=%0d", x); // NBA applied
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "before=1\nafter=9\n"


def test_combinational_process_reacts_to_inputs():
    k = _sim("""
module tb;
  logic [3:0] a = 0, b = 0, s;
  always_comb s = a + b;
  initial begin
    a = 3; b = 4;
    #1 $display("s=%0d", s);
    a = 9;
    #1 $display("s=%0d", s);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "s=7\ns=13\n"


def test_hierarchy_ports_bind_both_directions():
    k = _sim("""
module inv(input logic [3:0] i, output logic [3:0] o);
  assign o = ~i;
endmodule
module tb;
  logic [3:0] i = 4'b0101, o;
  inv dut(.i(i), .o(o));
  initial begin #1 $display("o=%b", o); $finish; end
endmodule""")
    assert "".join(k.stdout) == "o=1010\n"


def test_x_propagates_through_uninitialized_register():
    k = _sim("""
module tb;
  logic [3:0] q;   // never reset
  logic [3:0] y;
  always_comb y = q + 1;
  initial begin #1 $display("y=%b", y); $finish; end
endmodule""")
    assert "".join(k.stdout) == "y=xxxx\n"


def test_zero_time_loop_is_reported_not_hung():
    with pytest.raises(SimulationError, match="zero-time loop"):
        _sim("""
module tb;
  logic a = 0;
  always_comb a = ~a;   // reads and writes itself: no stable solution
  initial #10 $finish;
endmodule""")


def test_unsupported_construct_raises_not_downgrades():
    from uvmstudio.core.errors import UnsupportedFeature
    with pytest.raises(UnsupportedFeature):
        _sim("""
module tb;
  class c_cls; int x; endclass   // classes are outside the N1 subset
  initial $finish;
endmodule""")


def test_case_statement_with_default():
    k = _sim("""
module tb;
  logic [1:0] sel = 2'd2; logic [7:0] y;
  initial begin
    case (sel)
      2'd0: y = 8'hAA;
      2'd2: y = 8'hBB;
      default: y = 8'hCC;
    endcase
    $display("y=%h", y);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "y=bb\n"


def test_for_loop_and_part_select():
    k = _sim("""
module tb;
  logic [7:0] v = 0;
  initial begin
    for (int i = 0; i < 4; i = i + 1)
      v[i] = 1'b1;
    $display("v=%b hi=%b", v, v[7:4]);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "v=00001111 hi=0000\n"


# ---------------------------------------------------------------------------
# VCD round-trip through our own reader
# ---------------------------------------------------------------------------

def test_vcd_writer_round_trips_through_our_reader(tmp_path):
    w = VCDWriter(tmp_path / "t.vcd")
    w.begin_scope("tb")
    w.add_signal(1, "clk", 1)
    w.add_signal(2, "bus", 4)
    w.end_scope()
    w.end_definitions()
    w.change(1, FourState.from_int(0, 1), 0)
    w.change(2, FourState.from_svint_str("4'b10x1", 4), 0)
    w.change(1, FourState.from_int(1, 1), 5)
    w.change(2, FourState.from_int(9, 4), 5)
    w.close(10)

    db = VCDReader().open(tmp_path / "t.vcd")
    sigs = list(db.signals.values())
    names = {sig.name for sig in sigs}
    assert {"clk", "bus"} <= names
    clk = [sig for sig in sigs if sig.name == "clk"][0]
    assert db.value_at(clk.path, 6) == "1"
    bus = [sig for sig in sigs if sig.name == "bus"][0]
    assert db.value_at(bus.path, 0).endswith("10x1")


# ---------------------------------------------------------------------------
# Differential vs Verilator — the tests that make the claims real
# ---------------------------------------------------------------------------

_VERILATOR = shutil.which("verilator")

DIFF_DESIGNS = {
    "counter_reset": """
module counter #(parameter int W = 4) (
  input logic clk, rst_n, output logic [W-1:0] q);
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) q <= '0; else q <= q + 1'b1;
endmodule
module tb;
  logic clk = 0, rst_n; logic [3:0] q;
  counter dut(.clk(clk), .rst_n(rst_n), .q(q));
  always #5 clk = ~clk;
  initial begin
    rst_n = 0; #12 rst_n = 1; #100;
    $display("q=%0d at t=%0t", q, $time);
    $finish;
  end
endmodule""",
    "alu_ops": """
module tb;
  logic [7:0] a = 8'h5A, b = 8'h0F;
  initial begin
    $display("and=%h or=%h xor=%h", a & b, a | b, a ^ b);
    $display("add=%0d sub=%0d", a + b, a - b);
    $display("sh=%h %h", a << 2, a >> 3);
    $display("cmp=%b %b %b", a < b, a == b, a >= b);
    $finish;
  end
endmodule""",
    "shift_reg": """
module tb;
  logic clk = 0; logic [7:0] sr = 8'h01;
  always #3 clk = ~clk;
  always @(posedge clk) sr <= {sr[6:0], sr[7]};
  initial begin #40 $display("sr=%h t=%0t", sr, $time); $finish; end
endmodule""",
    "fsm": """
module tb;
  logic clk = 0; logic [1:0] st = 0; logic [7:0] cnt = 0;
  always #5 clk = ~clk;
  always @(posedge clk) begin
    case (st)
      2'd0: st <= 2'd1;
      2'd1: st <= 2'd3;
      2'd3: st <= 2'd0;
      default: st <= 2'd0;
    endcase
    cnt <= cnt + {6'd0, st};
  end
  initial begin #83 $display("st=%0d cnt=%0d", st, cnt); $finish; end
endmodule""",
}


def _run_native(src: str) -> str:
    return "".join(_sim(src).stdout)


def _run_verilator(src: str, tmp: Path) -> str:
    (tmp / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--timescale", "1ns/1ns",
         "-Wno-DECLFILENAME", "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    out = subprocess.run([str(tmp / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    return "".join(
        ln + "\n" for ln in out.stdout.splitlines()
        if not ln.startswith(("- ", "- V"))
    )


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
@pytest.mark.parametrize("name", sorted(DIFF_DESIGNS))
def test_differential_vs_verilator(name, tmp_path):
    src = DIFF_DESIGNS[name]
    native = _run_native(src)
    reference = _run_verilator(src, tmp_path)
    assert native == reference, (
        f"native and Verilator disagree on {name}:\n"
        f"  native   : {native!r}\n  verilator: {reference!r}"
    )
