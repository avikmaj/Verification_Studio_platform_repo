#!/usr/bin/env python3
"""Native-engine performance baseline vs compiled Verilator.

Measured, not estimated — replaces the "performance unmeasured" bound from
the platform red-team. Run on any box; the numbers in FEATURE_STATUS.md name
the machine they came from. Identical source per benchmark on both engines;
outputs are cross-checked before timings are accepted (a fast wrong answer
is not a result).

Usage: python3 scripts/perf_baseline.py [--cycles N] [--rand N]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit._toolchain import find_verilator  # noqa: E402


def rtl_bench(cycles: int) -> str:
    # counter + FSM + comb activity every cycle; self-reports a checksum so
    # both engines can be verified to have done the same work
    horizon = cycles * 10 + 7
    return f"""
module tb;
  logic clk = 0;
  logic [15:0] cnt = 0;
  logic [15:0] acc = 0;
  logic [1:0] st = 0;
  always #5 clk = ~clk;
  always @(posedge clk) begin
    cnt <= cnt + 1;
    case (st)
      2'd0: st <= (cnt[2:0] == 3'd7) ? 2'd1 : 2'd0;
      2'd1: st <= 2'd2;
      2'd2: st <= (cnt[0]) ? 2'd3 : 2'd2;
      default: st <= 2'd0;
    endcase
    acc <= acc + (cnt ^ {{14'd0, st}});
  end
  initial begin
    #{horizon};
    $display("cnt=%0d acc=%0d st=%0d", cnt, acc, st);
    $finish;
  end
endmodule"""


def rand_bench(n: int) -> str:
    return f"""
module tb;
  class c_pkt;
    rand bit [7:0] m_addr;
    rand bit [7:0] m_len;
    rand bit [3:0] m_burst;
    constraint c_a {{ m_addr inside {{[8'h10:8'hF0]}}; }}
    constraint c_l {{ m_len > 8'd2; m_len < 8'd200; }}
    constraint c_r {{ (m_burst == 4'd1) -> (m_len == 8'd4); }}
  endclass
  int unsigned bad;
  initial begin
    c_pkt p;
    p = new;
    repeat ({n}) begin
      if (p.randomize() != 1) bad = bad + 1;
      if (!(p.m_addr inside {{[8'h10:8'hF0]}})) bad = bad + 1;
    end
    $display("bad=%0d", bad);
    $finish;
  end
endmodule"""


def run_native(src: str, seed: int = 7):
    from pyslang import ast, syntax
    from uvmstudio.engine.interp import Interp
    from uvmstudio.engine.kernel import Kernel

    t0 = time.monotonic()
    comp = ast.Compilation()
    comp.addSyntaxTree(syntax.SyntaxTree.fromText(src))
    errs = [str(d) for d in comp.getAllDiagnostics() if d.isError]
    assert not errs, errs
    k = Kernel()
    Interp(comp, k, seed=seed).elaborate()
    t_build = time.monotonic() - t0
    t0 = time.monotonic()
    k.run()
    t_run = time.monotonic() - t0
    return "".join(k.stdout), t_build, t_run


def run_verilator(src: str, tmp: Path, seed: int = 7):
    vl = find_verilator()
    (tmp / "d.sv").write_text(src)
    t0 = time.monotonic()
    r = subprocess.run(
        [vl, "--binary", "--sv", "--timing", "--timescale", "1ns/1ns",
         "-Wno-DECLFILENAME", "--top-module", "tb", "-o", "simv", "d.sv"],
        cwd=tmp, capture_output=True, text=True, timeout=900)
    t_build = time.monotonic() - t0
    assert r.returncode == 0, r.stderr[-800:]
    t0 = time.monotonic()
    out = subprocess.run([str(tmp / "obj_dir" / "simv"),
                          f"+verilator+seed+{seed}"],
                         capture_output=True, text=True, timeout=300)
    t_run = time.monotonic() - t0
    text = "".join(ln + "\n" for ln in out.stdout.splitlines()
                   if not ln.startswith(("- ", "%Warning")))
    return text, t_build, t_run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=20000)
    ap.add_argument("--rand", type=int, default=2000)
    args = ap.parse_args()

    print(f"# host: {os.uname().nodename} ({os.uname().machine}), "
          f"python {sys.version.split()[0]}")
    rows = []

    # RTL benchmark — cross-check outputs before accepting timings
    src = rtl_bench(args.cycles)
    n_out, n_b, n_r = run_native(src)
    with tempfile.TemporaryDirectory() as td:
        v_out, v_b, v_r = run_verilator(src, Path(td))
    assert n_out == v_out, f"OUTPUT MISMATCH: {n_out!r} vs {v_out!r}"
    rows.append(("rtl counter+fsm", f"{args.cycles} cycles",
                 n_b, n_r, args.cycles / n_r,
                 v_b, v_r, args.cycles / v_r))

    # randomize benchmark — legality cross-checked (bad=0 both), draw
    # values legitimately differ by solver
    src = rand_bench(args.rand)
    n_out, n_b, n_r = run_native(src)
    with tempfile.TemporaryDirectory() as td:
        v_out, v_b, v_r = run_verilator(src, Path(td))
    assert n_out == v_out == "bad=0\n", (n_out, v_out)
    rows.append(("randomize (3 vars, 3 blocks)", f"{args.rand} calls",
                 n_b, n_r, args.rand / n_r,
                 v_b, v_r, args.rand / v_r))

    print(f"{'benchmark':<30} {'work':<14} "
          f"{'nat build':>9} {'nat run':>9} {'nat rate':>12} "
          f"{'vl build':>9} {'vl run':>9} {'vl rate':>12} {'ratio':>8}")
    for (name, work, nb, nr, nrate, vb, vr, vrate) in rows:
        print(f"{name:<30} {work:<14} "
              f"{nb:>8.2f}s {nr:>8.2f}s {nrate:>10.0f}/s "
              f"{vb:>8.2f}s {vr:>8.2f}s {vrate:>10.0f}/s "
              f"{vrate/nrate:>7.0f}x")


if __name__ == "__main__":
    main()
