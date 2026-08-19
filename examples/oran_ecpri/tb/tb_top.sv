//----------------------------------------------------------------------------
// tb_top — eCPRI transport VIP testbench top.
// Clock, reset, DUT + interface, virtual-interface publication, waves under
// plusarg control, hard watchdog. All verification behavior is in ecpri_pkg.
//----------------------------------------------------------------------------
`timescale 1ns/1ps

module tb_top;

  import uvm_pkg::*;
  import ecpri_pkg::*;
  `include "uvm_macros.svh"

  localparam int unsigned MTU = 1024;
  localparam time CLK_PERIOD  = 10ns;

  logic clk;
  logic rst_n;

  // --- clock -------------------------------------------------------------
  initial begin
    clk = 1'b0;
    forever #(CLK_PERIOD/2) clk = ~clk;
  end

  // --- reset -------------------------------------------------------------
  initial begin
    rst_n = 1'b0;
    repeat (5) @(posedge clk);
    rst_n = 1'b1;
  end

  // --- DUT + interface ---------------------------------------------------
  ecpri_if #(.MTU(MTU)) ecpri_bus (.clk(clk), .rst_n(rst_n));

  ecpri_codec #(.MTU(MTU)) u_dut (
    .clk         (clk),
    .rst_n       (rst_n),
    .in_valid    (ecpri_bus.in_valid),
    .in_version  (ecpri_bus.in_version),
    .in_concat   (ecpri_bus.in_concat),
    .in_msg_type (ecpri_bus.in_msg_type),
    .in_psize    (ecpri_bus.in_psize),
    .in_pcid     (ecpri_bus.in_pcid),
    .in_seqid    (ecpri_bus.in_seqid),
    .out_valid   (ecpri_bus.out_valid),
    .out_version (ecpri_bus.out_version),
    .out_concat  (ecpri_bus.out_concat),
    .out_msg_type(ecpri_bus.out_msg_type),
    .out_psize   (ecpri_bus.out_psize),
    .out_pcid    (ecpri_bus.out_pcid),
    .out_seqid   (ecpri_bus.out_seqid),
    .out_err     (ecpri_bus.out_err),
    .out_hdr     (ecpri_bus.out_hdr)
  );

  // --- UVM bring-up ------------------------------------------------------
  initial begin
    uvm_config_db#(virtual ecpri_if)::set(null, "*", "vif", ecpri_bus);
    run_test();
  end

  // --- waveforms ---------------------------------------------------------
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
  initial begin
    #10ms;
    `uvm_fatal("WATCHDOG", "simulation exceeded 10ms wall of sim time")
  end

endmodule : tb_top
