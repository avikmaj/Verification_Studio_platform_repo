"""`uvmstudio init` — scaffold a runnable project.

What it writes is a *working* project, not a set of placeholder files: the
generated sources compile and run on the first `uvmstudio build`. A scaffold
that does not run teaches nothing about whether the toolchain is set up.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_YAML = """\
name: {name}
top: tb_top
language_standard: "1800-2017"
{uvm_home}
filesets:
  - name: rtl
    files:
      - rtl/dut.sv
    include_dirs:
      - rtl

  - name: tb
    files:
{tb_files}
    include_dirs:
      - tb
{tb_defines}
default_backend: verilator
coverage: true
waves: on_fail
build_dir: build
results_dir: results

tests:
{tests}
"""

_DUT = """\
// Minimal but real DUT: a registered adder with saturation.
`timescale 1ns/1ps

module dut #(parameter int W = 8) (
  input  logic         clk,
  input  logic         rst_n,
  input  logic         vld,
  input  logic [W-1:0] a,
  input  logic [W-1:0] b,
  output logic [W-1:0] sum,
  output logic         sat,
  output logic         done
);
  logic [W:0] wide;

  always_comb wide = a + b;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sum  <= '0;
      sat  <= 1'b0;
      done <= 1'b0;
    end else begin
      done <= vld;
      if (vld) begin
        sat <= wide[W];
        sum <= wide[W] ? {W{1'b1}} : wide[W-1:0];
      end
    end
  end
endmodule
"""

_TB_PLAIN = """\
// Class-based constrained-random testbench with functional coverage.
// No UVM — this scaffold runs anywhere the simulator supports classes.
`timescale 1ns/1ps

module tb_top;

  localparam int W = 8;

  logic         clk = 0;
  logic         rst_n = 0;
  logic         vld = 0;
  logic [W-1:0] a, b;
  logic [W-1:0] sum;
  logic         sat, done;

  always #5ns clk = ~clk;

  dut #(.W(W)) u_dut (.*);

  // --- stimulus item ----------------------------------------------------
  class stim_item;
    rand bit [W-1:0] m_a;
    rand bit [W-1:0] m_b;
    rand int unsigned m_gap;

    int unsigned m_max_gap = 3;

    constraint c_gap  { m_gap inside {[0 : m_max_gap]}; }
    // Weight the saturating corner so it is actually reached.
    constraint c_corner {
      m_a dist { 0 := 1, {W{1'b1}} := 2, [1 : {W{1'b1}}-1] :/ 7 };
      m_b dist { 0 := 1, {W{1'b1}} := 2, [1 : {W{1'b1}}-1] :/ 7 };
    }
  endclass

  // --- functional coverage ----------------------------------------------
  covergroup cg_stim_t with function sample(bit [W-1:0] va, bit [W-1:0] vb, bit vsat);
    cp_a: coverpoint va {
      bins zero_a  = {0};
      bins low_a   = {[1 : 63]};
      bins high_a  = {[64 : 254]};
      bins max_a   = {255};
    }
    cp_b: coverpoint vb {
      bins zero_b  = {0};
      bins low_b   = {[1 : 63]};
      bins high_b  = {[64 : 254]};
      bins max_b   = {255};
    }
    cp_sat: coverpoint vsat {
      bins no_sat = {0};
      bins is_sat = {1};
    }
    x_a_sat: cross cp_a, cp_sat;
  endgroup

  // A covergroup declaration is a type; sampling requires an instance.
  cg_stim_t cg_stim = new();

  // --- reference model + checker ----------------------------------------
  int unsigned n_checks = 0;
  int unsigned n_errors = 0;

  task automatic check(input bit [W-1:0] ea, input bit [W-1:0] eb);
    logic [W:0]   wide = ea + eb;
    logic [W-1:0] exp  = wide[W] ? {W{1'b1}} : wide[W-1:0];
    n_checks++;
    if (sum !== exp || sat !== wide[W]) begin
      n_errors++;
      $error("MISMATCH a=%0d b=%0d exp_sum=%0d got_sum=%0d exp_sat=%0b got_sat=%0b",
             ea, eb, exp, sum, wide[W], sat);
    end
    cg_stim.sample(ea, eb, sat);
  endtask

  // --- test -------------------------------------------------------------
  initial begin
    automatic stim_item item = new();
    automatic int n_items = 200;
    string wave_file;

    if ($test$plusargs("DUMP_WAVES")) begin
      if (!$value$plusargs("WAVE_FILE=%s", wave_file)) wave_file = "waves.vcd";
      $dumpfile(wave_file);
      $dumpvars(0, tb_top);
    end
    void'($value$plusargs("N_ITEMS=%d", n_items));

    repeat (3) @(posedge clk);
    rst_n = 1;

    for (int i = 0; i < n_items; i++) begin
      if (!item.randomize()) $fatal(1, "randomize() failed on stim_item");
      repeat (item.m_gap) @(posedge clk);
      @(negedge clk);
      a   = item.m_a;
      b   = item.m_b;
      vld = 1'b1;
      @(negedge clk);
      vld = 1'b0;
      @(posedge clk);           // result registered
      #1ps check(item.m_a, item.m_b);
    end

    $display("[tb_top] checks=%0d errors=%0d coverage=%0.2f%%",
             n_checks, n_errors, cg_stim.get_coverage());
    if (n_errors != 0) $fatal(1, "%0d mismatch(es) detected", n_errors);
    $display("TEST PASSED");
    $finish;
  end

endmodule
"""

_TB_UVM_NOTE = """\
// UVM scaffold intentionally left to the golden example, which carries a
// complete agent stack. Copy examples/golden_apb/ and rename, rather than
// starting from a partial UVM skeleton.
"""


def write_scaffold(root: Path, *, name: str, with_uvm: bool = False) -> list[Path]:
    (root / "rtl").mkdir(parents=True, exist_ok=True)
    (root / "tb").mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    def w(rel: str, text: str) -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        created.append(p)

    w("rtl/dut.sv", _DUT)
    w("tb/tb_top.sv", _TB_PLAIN)

    tests = "\n".join(
        [
            "  - name: smoke",
            "    tier: L0",
            "    seeds: 1",
            "    timeout_s: 300",
            "",
            "  - name: random",
            "    tier: L1",
            "    seeds: 5",
            "    timeout_s: 600",
        ]
    )
    w(
        "uvmstudio.yaml",
        _PROJECT_YAML.format(
            name=name,
            uvm_home="uvm_home: ${UVM_HOME}\n" if with_uvm else "",
            tb_files="      - tb/tb_top.sv",
            tb_defines="    defines:\n      - UVM_NO_DPI\n" if with_uvm else "",
            tests=tests,
        ),
    )
    w(
        ".gitignore",
        "build/\nresults/\nobj_dir/\n*.vcd\n*.fst\n*.dat\n__pycache__/\n",
    )
    return created
