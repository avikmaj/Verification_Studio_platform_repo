//----------------------------------------------------------------------------
// tb_top — golden acceptance testbench top.
//
// Responsibilities kept deliberately narrow: clock, reset, DUT+interface
// instantiation, virtual interface publication into the config DB, waveform
// dumping under plusarg control, and a hard watchdog. All verification
// behaviour lives in apb_pkg.
//----------------------------------------------------------------------------
`timescale 1ns/1ps

module tb_top;

  import uvm_pkg::*;
  import apb_pkg::*;
  `include "uvm_macros.svh"

  localparam int    ADDR_W    = 32;
  localparam int    DATA_W    = 32;
  localparam time   CLK_PERIOD = 10ns;
  localparam int    WAIT_CYC   = 1;         // exercise PREADY wait states

  logic pclk;
  logic presetn;

  // --- clock -------------------------------------------------------------
  initial begin
    pclk = 1'b0;
    forever #(CLK_PERIOD/2) pclk = ~pclk;
  end

  // --- reset -------------------------------------------------------------
  initial begin
    presetn = 1'b0;
    repeat (5) @(posedge pclk);
    presetn = 1'b1;
  end

  // --- DUT + interface ---------------------------------------------------
  apb_if #(.ADDR_W(ADDR_W), .DATA_W(DATA_W)) apb_bus (.pclk(pclk), .presetn(presetn));

  apb_slave #(
    .ADDR_W  (ADDR_W),
    .DATA_W  (DATA_W),
    .N_REGS  (64),
    .WAIT_CYC(WAIT_CYC),
    .ERR_BASE(32'h0000_0100)
  ) u_dut (
    .pclk    (pclk),
    .presetn (presetn),
    .psel    (apb_bus.psel),
    .penable (apb_bus.penable),
    .pwrite  (apb_bus.pwrite),
    .paddr   (apb_bus.paddr),
    .pwdata  (apb_bus.pwdata),
    .pstrb   (apb_bus.pstrb),
    .prdata  (apb_bus.prdata),
    .pready  (apb_bus.pready),
    .pslverr (apb_bus.pslverr)
  );

  // --- UVM bring-up ------------------------------------------------------
  initial begin
    uvm_config_db#(virtual apb_if)::set(null, "*", "vif", apb_bus);
    run_test();
  end

  // --- waveforms ---------------------------------------------------------
  // Controlled by plusarg so the same image serves both traced and untraced
  // runs; the studio decides per run whether to keep the dump.
  initial begin
    string wave_file;
    if ($test$plusargs("DUMP_WAVES")) begin
      if (!$value$plusargs("WAVE_FILE=%s", wave_file)) wave_file = "waves.vcd";
      $dumpfile(wave_file);
      $dumpvars(0, tb_top);
      $display("[tb_top] waveform dumping enabled -> %s", wave_file);
    end
  end

  // --- watchdog ----------------------------------------------------------
  // A hung test must fail loudly rather than consume a regression slot.
  initial begin
    int unsigned timeout_ns = 200_000;
    void'($value$plusargs("TIMEOUT_NS=%d", timeout_ns));
    #(timeout_ns * 1ns);
    `uvm_fatal("TB_TIMEOUT",
               $sformatf("global watchdog expired after %0d ns", timeout_ns))
  end

endmodule
