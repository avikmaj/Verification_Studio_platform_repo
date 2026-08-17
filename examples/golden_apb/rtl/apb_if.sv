// APB4 interface with driver/monitor modports and a bind-friendly protocol checker.
`ifndef APB_IF_SV
`define APB_IF_SV

interface apb_if #(parameter int ADDR_W = 32, parameter int DATA_W = 32)
                 (input logic pclk, input logic presetn);

  logic                  psel;
  logic                  penable;
  logic                  pwrite;
  logic [ADDR_W-1:0]     paddr;
  logic [DATA_W-1:0]     pwdata;
  logic [DATA_W-1:0]     prdata;
  logic                  pready;
  logic                  pslverr;
  logic [DATA_W/8-1:0]   pstrb;
  logic [2:0]            pprot;

  clocking mst_cb @(posedge pclk);
    default input #1step output #1ns;
    output psel, penable, pwrite, paddr, pwdata, pstrb, pprot;
    input  prdata, pready, pslverr;
  endclocking

  clocking mon_cb @(posedge pclk);
    default input #1step;
    input psel, penable, pwrite, paddr, pwdata, pstrb, pprot;
    input prdata, pready, pslverr;
  endclocking

  modport mst (clocking mst_cb, input pclk, input presetn);
  modport mon (clocking mon_cb, input pclk, input presetn);

`ifndef UVMSTUDIO_NO_SVA
  // --- protocol invariants -------------------------------------------------
  // SETUP (psel & !penable) must be followed by ACCESS (psel & penable).
  property p_setup_to_access;
    @(posedge pclk) disable iff (!presetn)
      (psel && !penable) |=> (psel && penable);
  endproperty
  a_setup_to_access: assert property (p_setup_to_access)
    else $error("APB: SETUP phase not followed by ACCESS phase");
  c_setup_to_access: cover property (p_setup_to_access);

  // Address and control must be stable through the whole transfer.
  property p_addr_stable;
    @(posedge pclk) disable iff (!presetn)
      (psel && penable && !pready) |=> $stable(paddr) && $stable(pwrite);
  endproperty
  a_addr_stable: assert property (p_addr_stable)
    else $error("APB: paddr/pwrite changed during a wait-stated ACCESS phase");
  c_addr_stable: cover property (p_addr_stable);

  // penable must never be asserted without psel.
  property p_enable_requires_sel;
    @(posedge pclk) disable iff (!presetn) penable |-> psel;
  endproperty
  a_enable_requires_sel: assert property (p_enable_requires_sel)
    else $error("APB: penable asserted without psel");
`endif

endinterface

`endif
