// eCPRI transport interface — field-level handshake into the codec.
// Assertions here are the Lane-1 protocol invariants from the vplan
// (SVA-001/002/003/005) stated as concurrent properties on the DUT outputs.
`timescale 1ns/1ps

interface ecpri_if #(
  parameter int unsigned MTU = 1024
) (
  input logic clk,
  input logic rst_n
);

  // -- request side (driver -> DUT) --------------------------------------
  logic        in_valid;
  logic [3:0]  in_version;
  logic        in_concat;
  logic [7:0]  in_msg_type;
  logic [15:0] in_psize;
  logic [15:0] in_pcid;
  logic [15:0] in_seqid;

  // -- response side (DUT -> monitor) ------------------------------------
  logic        out_valid;
  logic [3:0]  out_version;
  logic        out_concat;
  logic [7:0]  out_msg_type;
  logic [15:0] out_psize;
  logic [15:0] out_pcid;
  logic [15:0] out_seqid;
  logic        out_err;
  logic [63:0] out_hdr;      // packed common+Type-0 header as transmitted

  default clocking cb @(posedge clk); endclocking
  default disable iff (!rst_n);

  // SVA-001 / FR-ECPRI-001: a recovered header not flagged as an error
  // carries ecpriVersion == 0x1.
  property p_version_legal;
    (out_valid && !out_err) |-> (out_version == 4'h1);
  endproperty
  a_version_legal: assert property (p_version_legal)
    else $error("SVA-001: non-error output with version %0h", out_version);
  property p_good_output;
    (out_valid && !out_err) |-> 1'b1;
  endproperty
  c_version_legal: cover property (p_good_output);

  // SVA-002 / FR-ECPRI-003: non-error msg_type is a Table-5 encoding (0..7).
  property p_msg_type_legal;
    (out_valid && !out_err) |-> (out_msg_type <= 8'd7);
  endproperty
  a_msg_type_legal: assert property (p_msg_type_legal)
    else $error("SVA-002: non-error output with msg_type %0d", out_msg_type);

  // SVA-003 / FR-ECPRI-004: non-error payload size within [8, MTU].
  property p_psize_bounded;
    (out_valid && !out_err) |->
      (out_psize >= 16'd8 && out_psize <= MTU[15:0]);
  endproperty
  a_psize_bounded: assert property (p_psize_bounded)
    else $error("SVA-003: non-error output with psize %0d", out_psize);

  // SVA-008 / FR-ECPRI-009: reserved bits of the transmitted header are 0.
  property p_reserved_zero;
    out_valid |-> (out_hdr[59:57] == 3'b000);
  endproperty
  a_reserved_zero: assert property (p_reserved_zero)
    else $error("SVA-008: reserved bits nonzero: %b", out_hdr[59:57]);
  property p_err_output;
    (out_valid && out_err) |-> 1'b1;
  endproperty
  c_err_seen: cover property (p_err_output);

endinterface : ecpri_if
