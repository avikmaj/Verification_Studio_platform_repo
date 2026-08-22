"""Native engine N3: classes — semantics pinned, then differential.

Reference semantics are the heart of it: a handle copy must alias the same
object, and the differential tests demand Verilator agrees byte-for-byte.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from uvmstudio.core.errors import UnsupportedFeature
from uvmstudio.engine.kernel import Kernel, SimulationError


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


CLASS_DEMO = """
module tb;
  class packet;
    bit [7:0] addr;
    bit [7:0] data = 8'h5;
    function new(bit [7:0] a);
      addr = a;
    endfunction
    function bit [8:0] sum();
      return addr + data;
    endfunction
    function void bump(bit [7:0] k);
      data = data + k;
    endfunction
  endclass

  initial begin
    packet p, q, r;
    p = new(8'h10);
    q = p;
    q.bump(8'h3);
    $display("sum=%0d data=%0d", p.sum(), p.data);
    $display("same=%b nullcheck=%b", p == q, r == null);
    r = new(8'h1);
    r.addr = 8'hFF;
    $display("r=%0d", r.sum());
    $finish;
  end
endmodule"""


def test_reference_semantics_and_methods():
    k = _sim(CLASS_DEMO)
    assert "".join(k.stdout) == "sum=24 data=8\nsame=1 nullcheck=1\nr=260\n"


def test_property_initializer_and_defaults():
    # LRM 6.8: two-state `bit` defaults to 0; four-state `logic` defaults
    # to X. (The original N3 test wrongly pinned b=xxxx — caught in the N4
    # defect-30-class sweep, defect 31. Verilator matches on `bit`; its
    # `logic` inits to 0 unless --x-initial — a documented deviation.)
    k = _sim("""
module tb;
  class c_thing;
    bit [3:0] a = 4'd9;
    bit [3:0] b;          // two-state, no initializer: 0
    logic [3:0] c;        // four-state, no initializer: X
  endclass
  initial begin
    c_thing t;
    t = new;
    $display("a=%0d b=%b c=%b", t.a, t.b, t.c);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "a=9 b=0000 c=xxxx\n"


def test_null_dereference_is_a_diagnosed_error():
    with pytest.raises(SimulationError, match="null handle dereference"):
        _sim("""
module tb;
  class c_x; bit [3:0] v; endclass
  initial begin
    c_x h;
    $display("%0d", h.v);   // h never constructed
    $finish;
  end
endmodule""")


def test_method_locals_do_not_leak_between_calls():
    k = _sim("""
module tb;
  class c_acc;
    bit [7:0] total;
    function void add(bit [7:0] x);
      bit [7:0] tmp;
      tmp = x + 1;
      total = total + tmp;
    endfunction
  endclass
  initial begin
    c_acc a;
    a = new;
    a.total = 0;
    a.add(8'd1);   // tmp=2
    a.add(8'd2);   // tmp=3 (fresh, not stale)
    $display("total=%0d", a.total);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "total=5\n"


def test_two_objects_are_independent():
    k = _sim("""
module tb;
  class c_p; bit [7:0] v = 8'd1; endclass
  initial begin
    c_p a, b;
    a = new; b = new;
    a.v = 8'd10;
    $display("a=%0d b=%0d eq=%b", a.v, b.v, a == b);
    $finish;
  end
endmodule""")
    assert "".join(k.stdout) == "a=10 b=1 eq=0\n"


def test_randc_raises_named_not_silent():
    # randomize() itself is live since N4 (tests/unit/test_native_randomize
    # .py); randc stays a named rejection — never silently degraded to rand
    with pytest.raises(UnsupportedFeature, match="randc"):
        _sim("""
module tb;
  class c_r; randc bit [3:0] v; endclass
  initial begin
    c_r r;
    r = new;
    void'(r.randomize());
    $finish;
  end
endmodule""")


def test_task_method_with_timing_is_rejected():
    with pytest.raises((SimulationError, UnsupportedFeature)):
        _sim("""
module tb;
  logic clk = 0;
  always #5 clk = ~clk;
  class c_t;
    task wait_a_bit();
      #10;
    endtask
  endclass
  initial begin
    c_t t;
    t = new;
    t.wait_a_bit();
    $finish;
  end
endmodule""")


# ---------------------------------------------------------------------------
# Differential vs Verilator
# ---------------------------------------------------------------------------

from tests.unit._toolchain import find_verilator
_VERILATOR = find_verilator()


def _run_verilator(src: str, tmp: Path) -> str:
    (tmp / "d.sv").write_text(src)
    r = subprocess.run(
        [_VERILATOR, "--binary", "--sv", "--timing", "--timescale", "1ns/1ns",
         "-Wno-DECLFILENAME", "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-1500:]
    out = subprocess.run([str(tmp / "obj_dir" / "simv")],
                         capture_output=True, text=True, timeout=120)
    return "".join(ln + "\n" for ln in out.stdout.splitlines()
                   if not ln.startswith("- "))


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_class_demo(tmp_path):
    native = "".join(_sim(CLASS_DEMO).stdout)
    assert native == _run_verilator(CLASS_DEMO, tmp_path)


@pytest.mark.skipif(_VERILATOR is None, reason="verilator not on PATH")
def test_differential_object_state_machine(tmp_path):
    src = """
module tb;
  class c_fifo_model;
    bit [15:0] count;
    bit [15:0] pushed;
    function void push(bit [15:0] n);
      count = count + n;
      pushed = pushed + 1;
    endfunction
    function void pop();
      if (count > 0) count = count - 1;
    endfunction
  endclass
  initial begin
    c_fifo_model m;
    m = new;
    m.count = 0; m.pushed = 0;
    for (int i = 1; i <= 5; i = i + 1) m.push(i[15:0]);
    repeat (3) m.pop();
    $display("count=%0d pushed=%0d", m.count, m.pushed);
    $finish;
  end
endmodule"""
    native = "".join(_sim(src).stdout)
    reference = _run_verilator(src, tmp_path)
    assert native == reference, f"{native!r} vs {reference!r}"
