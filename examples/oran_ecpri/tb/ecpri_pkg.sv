// eCPRI transport VIP — full UVM agent stack for the clocked codec DUT.
// Traces to GATE0_ecpri_transport_vplan.md: FR-ECPRI-001..006, 008, 009.
//
// Constrained-random discipline: no hardcoded field values anywhere in a
// sequence or test — constraints define the legal space, the seed picks the
// values, and the error sequence violates exactly ONE legality check per
// item (the isolated-negative pattern from the O-RAN mutation work) so a
// detection proves the specific check, not a gross pile-up.
`timescale 1ns/1ps

package ecpri_pkg;

  import uvm_pkg::*;
  `include "uvm_macros.svh"

  localparam int unsigned ECPRI_MTU = 1024;

  typedef enum { VIOL_NONE, VIOL_VERSION, VIOL_MSG_TYPE,
                 VIOL_PSIZE_LO, VIOL_PSIZE_HI } ecpri_viol_e;

  // ------------------------------------------------------------------
  // sequence item
  // ------------------------------------------------------------------
  class ecpri_seq_item extends uvm_sequence_item;
    rand bit [3:0]   m_version;
    rand bit         m_concat;
    rand bit [7:0]   m_msg_type;
    rand bit [15:0]  m_psize;
    rand bit [15:0]  m_pcid;
    rand bit [15:0]  m_seqid;
    rand int unsigned m_delay;
    rand ecpri_viol_e m_viol;

    int unsigned m_max_delay = 3;
    bit          m_inject_illegal = 1'b0;

    `uvm_object_utils_begin(ecpri_seq_item)
      `uvm_field_int(m_version,  UVM_ALL_ON)
      `uvm_field_int(m_concat,   UVM_ALL_ON)
      `uvm_field_int(m_msg_type, UVM_ALL_ON)
      `uvm_field_int(m_psize,    UVM_ALL_ON)
      `uvm_field_int(m_pcid,     UVM_ALL_ON)
      `uvm_field_int(m_seqid,    UVM_ALL_ON)
    `uvm_object_utils_end

    function new(string name = "ecpri_seq_item");
      super.new(name);
    endfunction

    // Exactly one violation when illegal injection is on; none otherwise.
    constraint c_viol_select {
      if (m_inject_illegal)
        m_viol inside {VIOL_VERSION, VIOL_MSG_TYPE,
                       VIOL_PSIZE_LO, VIOL_PSIZE_HI};
      else
        m_viol == VIOL_NONE;
    }

    // FR-ECPRI-001: legal version is 0x1; violation picks any other code.
    constraint c_version {
      if (m_viol == VIOL_VERSION) m_version != 4'h1;
      else                        m_version == 4'h1;
    }

    // FR-ECPRI-003: msg_type 0..7 legal, with every encoding reachable.
    constraint c_msg_type {
      if (m_viol == VIOL_MSG_TYPE) m_msg_type >  8'd7;
      else                         m_msg_type <= 8'd7;
    }

    // FR-ECPRI-004: payload size in [8, MTU]; boundary-weighted so the
    // COV-004 bucket edges are actually exercised.
    constraint c_psize {
      if (m_viol == VIOL_PSIZE_LO)
        m_psize < 16'd8;
      else if (m_viol == VIOL_PSIZE_HI)
        m_psize > ECPRI_MTU[15:0];
      else {
        m_psize inside {[16'd8 : ECPRI_MTU[15:0]]};
        // bucket selector: dist weights are not honored by every solver
        // (Verilator: constraints PARTIALLY_SUPPORTED), and uniform draws
        // starve the narrow [8:16] COV-004 bucket. Equality per bucket is
        // portable and keeps every bucket reachable by construction.
        if (m_psize_sel == 2'd0)      m_psize inside {[16'd8   : 16'd16]};
        else if (m_psize_sel == 2'd1) m_psize inside {[16'd17  : 16'd256]};
        else if (m_psize_sel == 2'd2) m_psize inside {[16'd257 : 16'd768]};
        else                          m_psize inside {[16'd769 : ECPRI_MTU[15:0]]};
      }
    }
    rand bit [1:0] m_psize_sel;

    // FR-ECPRI-006 / COV-003: hit the seq-id boundary encodings.
    // A dist on a part-select was silently ineffective under Verilator's
    // solver (found as two named coverage holes at GATE-8 style review);
    // an explicit selector variable is solver-portable.
    rand bit [1:0] m_seq_sel;
    constraint c_seq_sel {
      m_seq_sel dist { 2'd0 := 2, 2'd1 := 2, 2'd2 := 6 };
    }
    constraint c_seqid_boundaries {
      if (m_seq_sel == 2'd0)      m_seqid[15:8] == 8'd0;
      else if (m_seq_sel == 2'd1) m_seqid[15:8] == 8'd255;
      else                        m_seqid[15:8] inside {[8'd1 : 8'd254]};
    }

    constraint c_delay { m_delay inside {[0 : m_max_delay]}; }

    function bit is_legal();
      return (m_version == 4'h1) && (m_msg_type <= 8'd7)
          && (m_psize >= 16'd8) && (m_psize <= ECPRI_MTU[15:0]);
    endfunction
  endclass : ecpri_seq_item

  // ------------------------------------------------------------------
  // sequences
  // ------------------------------------------------------------------
  class ecpri_base_seq extends uvm_sequence #(ecpri_seq_item);
    `uvm_object_utils(ecpri_base_seq)

    int unsigned m_n_items = 20;
    bit          m_inject_illegal = 1'b0;

    function new(string name = "ecpri_base_seq");
      super.new(name);
    endfunction

    virtual task body();
      ecpri_seq_item item;
      repeat (m_n_items) begin
        item = ecpri_seq_item::type_id::create("item");
        item.m_inject_illegal = m_inject_illegal;
        start_item(item);
        if (!item.randomize())
          `uvm_fatal(get_type_name(), "randomize() failed")
        finish_item(item);
      end
    endtask
  endclass : ecpri_base_seq

  class ecpri_smoke_seq extends ecpri_base_seq;
    `uvm_object_utils(ecpri_smoke_seq)
    function new(string name = "ecpri_smoke_seq");
      super.new(name);
      m_n_items = 20;
    endfunction
  endclass : ecpri_smoke_seq

  class ecpri_random_seq extends ecpri_base_seq;
    `uvm_object_utils(ecpri_random_seq)
    function new(string name = "ecpri_random_seq");
      super.new(name);
      m_n_items = 60;
    endfunction
  endclass : ecpri_random_seq

  class ecpri_error_seq extends ecpri_base_seq;
    `uvm_object_utils(ecpri_error_seq)
    function new(string name = "ecpri_error_seq");
      super.new(name);
      m_n_items = 25;
      m_inject_illegal = 1'b1;
    endfunction
  endclass : ecpri_error_seq

  typedef uvm_sequencer #(ecpri_seq_item) ecpri_sequencer;

  // ------------------------------------------------------------------
  // driver
  // ------------------------------------------------------------------
  class ecpri_driver extends uvm_driver #(ecpri_seq_item);
    `uvm_component_utils(ecpri_driver)

    virtual ecpri_if m_vif;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual ecpri_if)::get(this, "", "vif", m_vif))
        `uvm_fatal(get_type_name(), "no virtual interface in config_db")
    endfunction

    task run_phase(uvm_phase phase);
      ecpri_seq_item item;
      m_vif.in_valid <= 1'b0;
      @(posedge m_vif.rst_n);
      forever begin
        seq_item_port.get_next_item(item);
        repeat (item.m_delay) @(posedge m_vif.clk);
        m_vif.in_valid    <= 1'b1;
        m_vif.in_version  <= item.m_version;
        m_vif.in_concat   <= item.m_concat;
        m_vif.in_msg_type <= item.m_msg_type;
        m_vif.in_psize    <= item.m_psize;
        m_vif.in_pcid     <= item.m_pcid;
        m_vif.in_seqid    <= item.m_seqid;
        @(posedge m_vif.clk);
        m_vif.in_valid <= 1'b0;
        seq_item_port.item_done();
      end
    endtask
  endclass : ecpri_driver

  // ------------------------------------------------------------------
  // monitor — observes the DUT's unpacked outputs
  // ------------------------------------------------------------------
  class ecpri_out_txn extends uvm_object;
    bit [3:0]  m_version;
    bit        m_concat;
    bit [7:0]  m_msg_type;
    bit [15:0] m_psize;
    bit [15:0] m_pcid;
    bit [15:0] m_seqid;
    bit        m_err;
    bit [63:0] m_hdr;
    `uvm_object_utils(ecpri_out_txn)
    function new(string name = "ecpri_out_txn");
      super.new(name);
    endfunction
  endclass : ecpri_out_txn

  class ecpri_monitor extends uvm_component;
    `uvm_component_utils(ecpri_monitor)

    virtual ecpri_if m_vif;
    uvm_analysis_port #(ecpri_out_txn) m_ap;

    function new(string name, uvm_component parent);
      super.new(name, parent);
      m_ap = new("m_ap", this);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual ecpri_if)::get(this, "", "vif", m_vif))
        `uvm_fatal(get_type_name(), "no virtual interface in config_db")
    endfunction

    task run_phase(uvm_phase phase);
      ecpri_out_txn t;
      forever begin
        @(posedge m_vif.clk);
        if (m_vif.rst_n && m_vif.out_valid) begin
          t = ecpri_out_txn::type_id::create("t");
          t.m_version  = m_vif.out_version;
          t.m_concat   = m_vif.out_concat;
          t.m_msg_type = m_vif.out_msg_type;
          t.m_psize    = m_vif.out_psize;
          t.m_pcid     = m_vif.out_pcid;
          t.m_seqid    = m_vif.out_seqid;
          t.m_err      = m_vif.out_err;
          t.m_hdr      = m_vif.out_hdr;
          m_ap.write(t);
        end
      end
    endtask
  endclass : ecpri_monitor

  // Input-side monitor: what the driver actually put on the wire.
  class ecpri_in_monitor extends uvm_component;
    `uvm_component_utils(ecpri_in_monitor)

    virtual ecpri_if m_vif;
    uvm_analysis_port #(ecpri_seq_item) m_ap;

    function new(string name, uvm_component parent);
      super.new(name, parent);
      m_ap = new("m_ap", this);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual ecpri_if)::get(this, "", "vif", m_vif))
        `uvm_fatal(get_type_name(), "no virtual interface in config_db")
    endfunction

    task run_phase(uvm_phase phase);
      ecpri_seq_item t;
      forever begin
        @(posedge m_vif.clk);
        if (m_vif.rst_n && m_vif.in_valid) begin
          t = ecpri_seq_item::type_id::create("t");
          t.m_version  = m_vif.in_version;
          t.m_concat   = m_vif.in_concat;
          t.m_msg_type = m_vif.in_msg_type;
          t.m_psize    = m_vif.in_psize;
          t.m_pcid     = m_vif.in_pcid;
          t.m_seqid    = m_vif.in_seqid;
          m_ap.write(t);
        end
      end
    endtask
  endclass : ecpri_in_monitor

  // ------------------------------------------------------------------
  // scoreboard — independent golden model + isolated-negative accounting
  // ------------------------------------------------------------------
  class ecpri_scoreboard extends uvm_component;
    `uvm_component_utils(ecpri_scoreboard)

    uvm_tlm_analysis_fifo #(ecpri_seq_item) m_in_fifo;
    uvm_tlm_analysis_fifo #(ecpri_out_txn)  m_out_fifo;

    int unsigned m_checks;
    int unsigned m_neg_detected;

    function new(string name, uvm_component parent);
      super.new(name, parent);
      m_in_fifo  = new("m_in_fifo", this);
      m_out_fifo = new("m_out_fifo", this);
    endfunction

    // Golden pack model, written against the eCPRI v2.0 clause text —
    // FR-ECPRI-008's reference for the round-trip check.
    function bit [63:0] golden_pack(ecpri_seq_item i);
      return {i.m_version, 3'b000, i.m_concat,
              i.m_msg_type, i.m_psize, i.m_pcid, i.m_seqid};
    endfunction

    task run_phase(uvm_phase phase);
      ecpri_seq_item exp;
      ecpri_out_txn  got;
      forever begin
        m_in_fifo.get(exp);
        m_out_fifo.get(got);
        m_checks++;

        // FR-ECPRI-008: the transmitted word matches the golden layout.
        if (got.m_hdr !== golden_pack(exp))
          `uvm_error(get_type_name(), $sformatf(
            "FR-ECPRI-008 pack mismatch: got %016h expected %016h",
            got.m_hdr, golden_pack(exp)))

        // Round-trip identity of every field.
        if (got.m_version  !== exp.m_version  ||
            got.m_concat   !== exp.m_concat   ||
            got.m_msg_type !== exp.m_msg_type ||
            got.m_psize    !== exp.m_psize    ||
            got.m_pcid     !== exp.m_pcid     ||
            got.m_seqid    !== exp.m_seqid)
          `uvm_error(get_type_name(),
                     "FR-ECPRI-008 unpack mismatch against driven fields")

        // Legality verdict must agree with the independent predicate.
        if (got.m_err !== !exp.is_legal())
          `uvm_error(get_type_name(), $sformatf(
            "legality mismatch: DUT err=%0b, model legal=%0b (viol=%s)",
            got.m_err, exp.is_legal(), exp.m_viol.name()))
        else if (got.m_err)
          m_neg_detected++;   // expected violation DETECTED
      end
    endtask

    function void report_phase(uvm_phase phase);
      super.report_phase(phase);
      `uvm_info(get_type_name(), $sformatf(
        "checks=%0d negative_detections=%0d", m_checks, m_neg_detected),
        UVM_LOW)
      // Anti-vacuity: a test that produced no observable evidence has
      // proved nothing and must not be allowed to report PASS.
      if (m_checks == 0 && m_neg_detected == 0)
        `uvm_error(get_type_name(),
                   "scoreboard saw no transactions - the test proved nothing")
    endfunction
  endclass : ecpri_scoreboard

  // ------------------------------------------------------------------
  // functional coverage — COV-001..005 from the vplan, in the
  // portable `with function sample` form (see FEATURE_STATUS §3).
  // ------------------------------------------------------------------
  class ecpri_coverage extends uvm_subscriber #(ecpri_seq_item);
    `uvm_component_utils(ecpri_coverage)

    covergroup cg_ecpri with function sample(bit [7:0]  msg_type,
                                             bit        concat,
                                             bit [15:0] psize,
                                             bit [7:0]  seq_hi,
                                             bit [3:0]  version);
      cp_msg: coverpoint msg_type {
        // COV-001 requires every eCPRI type as its OWN bin. The array form
        // `bins t[] = {[0:7]}` silently collapsed into one aggregate bin
        // under this simulator (red-team RT-P-003), which would let a
        // single msg_type value satisfy "100%". Explicit bins make that
        // model degradation impossible.
        bins t0 = {0}; bins t1 = {1}; bins t2 = {2}; bins t3 = {3};
        bins t4 = {4}; bins t5 = {5}; bins t6 = {6}; bins t7 = {7};
      }
      cp_concat: coverpoint concat {
        bins clear = {1'b0};
        bins set   = {1'b1};
      }
      cp_psize: coverpoint psize {
        bins b_min      = {[8:16]};
        bins b_typ      = {[17:256]};
        bins b_mid      = {[257:768]};
        bins b_max      = {[769:1024]};
      }
      cp_seq: coverpoint seq_hi {
        bins s_min = {8'd0};
        bins s_max = {8'd255};
        bins s_mid = {[8'd1:8'd254]};
      }
      cp_ver: coverpoint version {
        bins legal = {4'h1};
      }
      x_msg_concat: cross cp_msg, cp_concat;
      x_msg_psize:  cross cp_msg, cp_psize;
    endgroup

    function new(string name, uvm_component parent);
      super.new(name, parent);
      cg_ecpri = new();
    endfunction

    function void write(ecpri_seq_item t);
      if (t.is_legal())
        cg_ecpri.sample(t.m_msg_type, t.m_concat, t.m_psize,
                        t.m_seqid[15:8], t.m_version);
    endfunction
  endclass : ecpri_coverage

  // ------------------------------------------------------------------
  // agent / env
  // ------------------------------------------------------------------
  class ecpri_agent extends uvm_agent;
    `uvm_component_utils(ecpri_agent)

    ecpri_sequencer  m_seqr;
    ecpri_driver     m_drv;
    ecpri_in_monitor m_in_mon;
    ecpri_monitor    m_out_mon;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      m_seqr    = ecpri_sequencer::type_id::create("m_seqr", this);
      m_drv     = ecpri_driver::type_id::create("m_drv", this);
      m_in_mon  = ecpri_in_monitor::type_id::create("m_in_mon", this);
      m_out_mon = ecpri_monitor::type_id::create("m_out_mon", this);
    endfunction

    function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      m_drv.seq_item_port.connect(m_seqr.seq_item_export);
    endfunction
  endclass : ecpri_agent

  class ecpri_env extends uvm_env;
    `uvm_component_utils(ecpri_env)

    ecpri_agent      m_agent;
    ecpri_scoreboard m_sb;
    ecpri_coverage   m_cov;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      m_agent = ecpri_agent::type_id::create("m_agent", this);
      m_sb    = ecpri_scoreboard::type_id::create("m_sb", this);
      m_cov   = ecpri_coverage::type_id::create("m_cov", this);
    endfunction

    function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      m_agent.m_in_mon.m_ap.connect(m_sb.m_in_fifo.analysis_export);
      m_agent.m_out_mon.m_ap.connect(m_sb.m_out_fifo.analysis_export);
      m_agent.m_in_mon.m_ap.connect(m_cov.analysis_export);
    endfunction
  endclass : ecpri_env

  // ------------------------------------------------------------------
  // tests
  // ------------------------------------------------------------------
  class ecpri_base_test extends uvm_test;
    `uvm_component_utils(ecpri_base_test)

    ecpri_env m_env;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      m_env = ecpri_env::type_id::create("m_env", this);
    endfunction

    task run_seq(ecpri_base_seq seq, uvm_phase phase);
      phase.raise_objection(this);
      seq.start(m_env.m_agent.m_seqr);
      // drain the two-stage pipeline
      repeat (10) @(posedge m_env.m_agent.m_drv.m_vif.clk);
      phase.drop_objection(this);
    endtask
  endclass : ecpri_base_test

  class ecpri_smoke_test extends ecpri_base_test;
    `uvm_component_utils(ecpri_smoke_test)
    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction
    task run_phase(uvm_phase phase);
      ecpri_smoke_seq seq = ecpri_smoke_seq::type_id::create("seq");
      run_seq(seq, phase);
    endtask
  endclass : ecpri_smoke_test

  class ecpri_random_test extends ecpri_base_test;
    `uvm_component_utils(ecpri_random_test)
    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction
    task run_phase(uvm_phase phase);
      ecpri_random_seq seq = ecpri_random_seq::type_id::create("seq");
      run_seq(seq, phase);
    endtask
  endclass : ecpri_random_test

  class ecpri_error_test extends ecpri_base_test;
    `uvm_component_utils(ecpri_error_test)

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
      ecpri_error_seq seq = ecpri_error_seq::type_id::create("seq");
      run_seq(seq, phase);
    endtask

    function void report_phase(uvm_phase phase);
      super.report_phase(phase);
      // Negative-test PASS requires the expected violations to have been
      // DETECTED, not merely driven.
      if (m_env.m_sb.m_neg_detected == 0)
        `uvm_error(get_type_name(),
                   "error test completed with zero detected violations")
    endfunction
  endclass : ecpri_error_test

endpackage : ecpri_pkg
