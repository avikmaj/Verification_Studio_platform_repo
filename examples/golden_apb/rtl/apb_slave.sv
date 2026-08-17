// Simple APB4 slave DUT: word-addressed register file with configurable
// wait states and an error region. Deliberately real enough to produce
// interesting traffic, small enough to elaborate fast.
`ifndef APB_SLAVE_SV
`define APB_SLAVE_SV

module apb_slave #(
  parameter int ADDR_W    = 32,
  parameter int DATA_W    = 32,
  parameter int N_REGS    = 64,
  parameter int WAIT_CYC  = 0,
  // Accesses at or above this offset return PSLVERR.
  parameter int ERR_BASE  = 64*4
)(
  input  logic                pclk,
  input  logic                presetn,
  input  logic                psel,
  input  logic                penable,
  input  logic                pwrite,
  input  logic [ADDR_W-1:0]   paddr,
  input  logic [DATA_W-1:0]   pwdata,
  input  logic [DATA_W/8-1:0] pstrb,
  output logic [DATA_W-1:0]   prdata,
  output logic                pready,
  output logic                pslverr
);

  localparam int IDX_W = $clog2(N_REGS);

  logic [DATA_W-1:0] mem [N_REGS];
  logic [7:0]        wait_cnt;

  wire [IDX_W-1:0] idx     = paddr[IDX_W+1:2];
  wire             in_range = (paddr < ERR_BASE);

  always_ff @(posedge pclk or negedge presetn) begin
    if (!presetn) begin
      wait_cnt <= '0;
      pready   <= 1'b0;
      pslverr  <= 1'b0;
      prdata   <= '0;
      for (int i = 0; i < N_REGS; i++) mem[i] <= '0;
    end else begin
      pready  <= 1'b0;
      pslverr <= 1'b0;

      if (psel && penable) begin
        if (wait_cnt == WAIT_CYC[7:0]) begin
          wait_cnt <= '0;
          pready   <= 1'b1;
          pslverr  <= ~in_range;
          if (in_range) begin
            if (pwrite) begin
              for (int b = 0; b < DATA_W/8; b++)
                if (pstrb[b]) mem[idx][b*8 +: 8] <= pwdata[b*8 +: 8];
            end else begin
              prdata <= mem[idx];
            end
          end else begin
            prdata <= '0;
          end
        end else begin
          wait_cnt <= wait_cnt + 8'd1;
        end
      end else begin
        wait_cnt <= '0;
      end
    end
  end

endmodule

`endif
