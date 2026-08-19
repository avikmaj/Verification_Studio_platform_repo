// eCPRI transport codec — CLOCKED two-stage pipeline (pack, then unpack).
//
// Stage 1 packs the field inputs into the on-the-wire header layout of
// eCPRI v2.0 (common header, then Type-0 PC_ID/SEQ_ID). Stage 2 unpacks
// that transmitted word back into fields and evaluates legality. The
// round-trip through an actual register stage is what makes FR-ECPRI-008
// a hardware property here rather than a function identity.
//
// Header layout (bit 63 down):
//   [63:60] ecpriVersion      [59:57] reserved (TX as 0)   [56] C-bit
//   [55:48] ecpriMessage      [47:32] ecpriPayloadSize
//   [31:16] PC_ID             [15:0]  SEQ_ID
`timescale 1ns/1ps

module ecpri_codec #(
  parameter int unsigned MTU = 1024
) (
  input  logic        clk,
  input  logic        rst_n,

  input  logic        in_valid,
  input  logic [3:0]  in_version,
  input  logic        in_concat,
  input  logic [7:0]  in_msg_type,
  input  logic [15:0] in_psize,
  input  logic [15:0] in_pcid,
  input  logic [15:0] in_seqid,

  output logic        out_valid,
  output logic [3:0]  out_version,
  output logic        out_concat,
  output logic [7:0]  out_msg_type,
  output logic [15:0] out_psize,
  output logic [15:0] out_pcid,
  output logic [15:0] out_seqid,
  output logic        out_err,
  output logic [63:0] out_hdr
);

  // -- stage 1: pack (reserved bits transmitted as zero, FR-ECPRI-009) ----
  logic [63:0] hdr_q;
  logic        v1_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      hdr_q <= '0;
      v1_q  <= 1'b0;
    end else begin
      v1_q <= in_valid;
      if (in_valid)
        hdr_q <= {in_version, 3'b000, in_concat,
                  in_msg_type, in_psize, in_pcid, in_seqid};
    end
  end

  // -- stage 2: unpack + legality (FR-ECPRI-001/003/004) ------------------
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_valid    <= 1'b0;
      out_err      <= 1'b0;
      out_version  <= '0;
      out_concat   <= 1'b0;
      out_msg_type <= '0;
      out_psize    <= '0;
      out_pcid     <= '0;
      out_seqid    <= '0;
      out_hdr      <= '0;
    end else begin
      out_valid <= v1_q;
      if (v1_q) begin
        out_hdr      <= hdr_q;
        out_version  <= hdr_q[63:60];
        out_concat   <= hdr_q[56];
        out_msg_type <= hdr_q[55:48];
        out_psize    <= hdr_q[47:32];
        out_pcid     <= hdr_q[31:16];
        out_seqid    <= hdr_q[15:0];
        out_err      <= (hdr_q[63:60] != 4'h1)
                     || (hdr_q[55:48] > 8'd7)
                     || (hdr_q[47:32] < 16'd8)
                     || (hdr_q[47:32] > MTU[15:0]);
      end
    end
  end

endmodule
