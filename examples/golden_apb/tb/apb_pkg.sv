//----------------------------------------------------------------------------
// apb_pkg — golden acceptance UVM environment for UVM Verification Studio.
//
// Contains the full agent stack the acceptance test exercises:
//   sequence item (rand + constraints) · sequence · sequencer · driver ·
//   monitor · agent · coverage subscriber (covergroup + sample) · scoreboard ·
//   env · tests
//
// Stimulus discipline: no hardcoded field values anywhere. Every address,
// data word, strobe, protection field and inter-transfer delay is produced by
// randomize() under constraint. Constraints define the legal space; the seed
// picks the value.
//----------------------------------------------------------------------------
`ifndef APB_PKG_SV
`define APB_PKG_SV

package apb_pkg;

  import uvm_pkg::*;
  `include "uvm_macros.svh"

  parameter int APB_ADDR_W = 32;
  parameter int APB_DATA_W = 32;
  parameter int APB_STRB_W = APB_DATA_W/8;

  typedef enum bit { APB_READ = 1'b0, APB_WRITE = 1'b1 } apb_dir_e;
  typedef enum bit { APB_OKAY = 1'b0, APB_ERR   = 1'b1 } apb_resp_e;

  //--------------------------------------------------------------------------
  // Sequence item
  //--------------------------------------------------------------------------
  class apb_seq_item extends uvm_sequence_item;

    rand apb_dir_e              m_dir;
    rand bit [APB_ADDR_W-1:0]   m_addr;
    rand bit [APB_DATA_W-1:0]   m_wdata;
    rand bit [APB_STRB_W-1:0]   m_strb;
    rand bit [2:0]              m_prot;
    rand int unsigned           m_delay;      // idle cycles before this transfer

    // Driven by the monitor, not randomized.
    bit [APB_DATA_W-1:0]        m_rdata;
    apb_resp_e                  m_resp;

    // Knobs — set from the test/sequence, never hardcoded inside a body.
    int unsigned                m_addr_lo   = 0;
    int unsigned                m_addr_hi   = 32'h0000_00FC;
    int unsigned                m_max_delay = 4;
    bit                         m_allow_err = 0;

    `uvm_object_utils_begin(apb_seq_item)
      `uvm_field_enum(apb_dir_e, m_dir,  UVM_ALL_ON)
      `uvm_field_int (m_addr,            UVM_ALL_ON | UVM_HEX)
      `uvm_field_int (m_wdata,           UVM_ALL_ON | UVM_HEX)
      `uvm_field_int (m_strb,            UVM_ALL_ON | UVM_HEX)
      `uvm_field_int (m_prot,            UVM_ALL_ON | UVM_HEX)
      `uvm_field_int (m_rdata,           UVM_ALL_ON | UVM_HEX)
      `uvm_field_enum(apb_resp_e, m_resp, UVM_ALL_ON)
    `uvm_object_utils_end

    function new(string name = "apb_seq_item");
      super.new(name);
    endfunction

    // APB is word-addressed in this DUT: the low two bits must be zero.
    constraint c_addr_aligned { m_addr[1:0] == 2'b00; }

    constraint c_addr_range {
      m_addr inside {[m_addr_lo : m_addr_hi]};
    }

    // Weight the corners: first word, last word, and the bulk in between.
    constraint c_addr_dist {
      m_addr dist {
        m_addr_lo                          := 2,
        [m_addr_lo+4 : m_addr_hi-4]        :/ 6,
        m_addr_hi                          := 2
      };
    }

    constraint c_strb {
      soft m_strb != '0;              // a write that writes nothing is rarely intended
      m_strb inside {[1 : (1<<APB_STRB_W)-1]};
    }

    constraint c_delay { m_delay inside {[0 : m_max_delay]}; }

    // Exercise data corners as well as the uniform middle.
    constraint c_wdata_dist {
      m_wdata dist {
        32'h0000_0000 := 1,
        32'hFFFF_FFFF := 1,
        [32'h1 : 32'hFFFF_FFFE] :/ 8
      };
    }

    constraint c_prot { soft m_prot == 3'b000; }

    function string convert2string();
      return $sformatf("%s addr=0x%08h wdata=0x%08h strb=0x%0h rdata=0x%08h resp=%s",
                       m_dir.name(), m_addr, m_wdata, m_strb, m_rdata, m_resp.name());
    endfunction

  endclass

  //--------------------------------------------------------------------------
  // Sequences
  //--------------------------------------------------------------------------
  class apb_base_seq extends uvm_sequence #(apb_seq_item);
    `uvm_object_utils(apb_base_seq)

    // All knobs are class variables so tests configure them; nothing is a
    // literal inside the body.
    rand int unsigned m_n_items = 20;
    int unsigned      m_addr_lo   = 0;
    int unsigned      m_addr_hi   = 32'h0000_00FC;
    int unsigned      m_max_delay = 4;

    constraint c_n_items { m_n_items inside {[1:200]}; }

    function new(string name = "apb_base_seq");
      super.new(name);
    endfunction

    virtual task body();
      apb_seq_item req;
      for (int i = 0; i < m_n_items; i++) begin
        req = apb_seq_item::type_id::create($sformatf("req_%0d", i));
        start_item(req);
        req.m_addr_lo   = m_addr_lo;
        req.m_addr_hi   = m_addr_hi;
        req.m_max_delay = m_max_delay;
        if (!req.randomize())
          `uvm_fatal(get_type_name(), "randomize() failed on apb_seq_item")
        finish_item(req);
      end
    endtask
  endclass

  // Write-then-read to the same address: gives the scoreboard something to check.
  class apb_write_read_seq extends apb_base_seq;
    `uvm_object_utils(apb_write_read_seq)

    function new(string name = "apb_write_read_seq");
      super.new(name);
    endfunction

    virtual task body();
      apb_seq_item wr, rd;
      for (int i = 0; i < m_n_items; i++) begin
        wr = apb_seq_item::type_id::create($sformatf("wr_%0d", i));
        start_item(wr);
        wr.m_addr_lo   = m_addr_lo;
        wr.m_addr_hi   = m_addr_hi;
        wr.m_max_delay = m_max_delay;
        if (!wr.randomize() with { m_dir == APB_WRITE; m_strb == '1; })
          `uvm_fatal(get_type_name(), "randomize() failed on write item")
        finish_item(wr);

        rd = apb_seq_item::type_id::create($sformatf("rd_%0d", i));
        start_item(rd);
        rd.m_addr_lo   = m_addr_lo;
        rd.m_addr_hi   = m_addr_hi;
        rd.m_max_delay = m_max_delay;
        // Same address as the write — constrained, not hardcoded.
        if (!rd.randomize() with { m_dir == APB_READ; m_addr == wr.m_addr; })
          `uvm_fatal(get_type_name(), "randomize() failed on read item")
        finish_item(rd);
      end
    endtask
  endclass

  // Negative sequence: drive the error region and expect PSLVERR.
  class apb_error_seq extends apb_base_seq;
    `uvm_object_utils(apb_error_seq)

    int unsigned m_err_lo = 32'h0000_0100;
    int unsigned m_err_hi = 32'h0000_01FC;

    function new(string name = "apb_error_seq");
      super.new(name);
    endfunction

    virtual task body();
      apb_seq_item req;
      for (int i = 0; i < m_n_items; i++) begin
        req = apb_seq_item::type_id::create($sformatf("err_%0d", i));
        start_item(req);
        req.m_addr_lo   = m_err_lo;
        req.m_addr_hi   = m_err_hi;
        req.m_max_delay = m_max_delay;
        if (!req.randomize() with { m_addr inside {[m_err_lo : m_err_hi]}; })
          `uvm_fatal(get_type_name(), "randomize() failed on error item")
        finish_item(req);
      end
    endtask
  endclass

  //--------------------------------------------------------------------------
  // Sequencer
  //--------------------------------------------------------------------------
  class apb_sequencer extends uvm_sequencer #(apb_seq_item);
    `uvm_component_utils(apb_sequencer)
    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction
  endclass

  //--------------------------------------------------------------------------
  // Driver
  //--------------------------------------------------------------------------
  class apb_driver extends uvm_driver #(apb_seq_item);
    `uvm_component_utils(apb_driver)

    virtual apb_if m_vif;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual apb_if)::get(this, "", "vif", m_vif))
        `uvm_fatal(get_type_name(),
                   "virtual interface 'vif' not set in uvm_config_db")
    endfunction

    task run_phase(uvm_phase phase);
      drive_idle();
      @(posedge m_vif.presetn);
      forever begin
        seq_item_port.get_next_item(req);
        drive_transfer(req);
        seq_item_port.item_done();
      end
    endtask

    task drive_idle();
      m_vif.psel    <= 1'b0;
      m_vif.penable <= 1'b0;
      m_vif.pwrite  <= 1'b0;
      m_vif.paddr   <= '0;
      m_vif.pwdata  <= '0;
      m_vif.pstrb   <= '0;
      m_vif.pprot   <= '0;
    endtask

    task drive_transfer(apb_seq_item item);
      repeat (item.m_delay) @(posedge m_vif.pclk);

      // SETUP phase
      @(posedge m_vif.pclk);
      m_vif.psel    <= 1'b1;
      m_vif.penable <= 1'b0;
      m_vif.pwrite  <= (item.m_dir == APB_WRITE);
      m_vif.paddr   <= item.m_addr;
      m_vif.pwdata  <= item.m_wdata;
      m_vif.pstrb   <= (item.m_dir == APB_WRITE) ? item.m_strb : '0;
      m_vif.pprot   <= item.m_prot;

      // ACCESS phase, held until PREADY
      @(posedge m_vif.pclk);
      m_vif.penable <= 1'b1;
      do @(posedge m_vif.pclk); while (m_vif.pready !== 1'b1);

      m_vif.psel    <= 1'b0;
      m_vif.penable <= 1'b0;
    endtask
  endclass

  //--------------------------------------------------------------------------
  // Monitor
  //--------------------------------------------------------------------------
  class apb_monitor extends uvm_monitor;
    `uvm_component_utils(apb_monitor)

    virtual apb_if m_vif;
    uvm_analysis_port #(apb_seq_item) m_ap;

    function new(string name, uvm_component parent);
      super.new(name, parent);
      m_ap = new("m_ap", this);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      if (!uvm_config_db#(virtual apb_if)::get(this, "", "vif", m_vif))
        `uvm_fatal(get_type_name(),
                   "virtual interface 'vif' not set in uvm_config_db")
    endfunction

    task run_phase(uvm_phase phase);
      apb_seq_item tr;
      forever begin
        @(posedge m_vif.pclk);
        if (m_vif.presetn && m_vif.psel && m_vif.penable && m_vif.pready) begin
          tr = apb_seq_item::type_id::create("mon_tr");
          tr.m_dir   = m_vif.pwrite ? APB_WRITE : APB_READ;
          tr.m_addr  = m_vif.paddr;
          tr.m_wdata = m_vif.pwdata;
          tr.m_strb  = m_vif.pstrb;
          tr.m_prot  = m_vif.pprot;
          tr.m_rdata = m_vif.prdata;
          tr.m_resp  = m_vif.pslverr ? APB_ERR : APB_OKAY;
          `uvm_info(get_type_name(), {"observed ", tr.convert2string()}, UVM_HIGH)
          m_ap.write(tr);
        end
      end
    endtask
  endclass

  //--------------------------------------------------------------------------
  // Coverage subscriber
  //--------------------------------------------------------------------------
  class apb_coverage extends uvm_subscriber #(apb_seq_item);
    `uvm_component_utils(apb_coverage)

    // Sampling contract is explicit: the covergroup takes its values as
    // arguments rather than reaching into an enclosing class member.
    //
    // Dereferencing an enclosing class member in a coverpoint is legal
    // IEEE 1800, but Verilator 5.050 does not implement it and silently
    // IGNORES THE WHOLE COVERGROUP:
    //
    //   %Warning-COVERIGN: Unsupported: 'covergroup' coverpoint referencing
    //     enclosing class member; ignoring covergroup '__vlAnonCG_cg_apb'
    //
    // Losing every bin to a warning is exactly the silent degradation this
    // platform exists to prevent, so the VIP uses the portable form.
    covergroup cg_apb with function sample(apb_dir_e            dir,
                                           apb_resp_e           resp,
                                           bit [APB_ADDR_W-1:0] addr,
                                           bit [APB_STRB_W-1:0] strb,
                                           bit [APB_DATA_W-1:0] wdata);
      option.per_instance = 1;

      cp_dir: coverpoint dir {
        bins rd = {APB_READ};
        bins wr = {APB_WRITE};
      }

      cp_resp: coverpoint resp {
        bins okay = {APB_OKAY};
        bins err  = {APB_ERR};
      }

      cp_addr: coverpoint addr[9:2] {
        bins first_word = {8'h00};
        bins low        = {[8'h01 : 8'h1F]};
        bins mid        = {[8'h20 : 8'h3E]};
        bins last_word  = {8'h3F};
        bins err_region = {[8'h40 : 8'h7F]};
      }

      cp_strb: coverpoint strb {
        bins byte0    = {4'b0001};
        bins byte3    = {4'b1000};
        bins halfword = {4'b0011, 4'b1100};
        bins fullword = {4'b1111};
        bins others   = default;
      }

      cp_wdata_corner: coverpoint wdata {
        bins zero    = {32'h0000_0000};
        bins allones = {32'hFFFF_FFFF};
        bins other   = default;
      }

      // Every meaningful field interaction is crossed.
      x_dir_resp : cross cp_dir, cp_resp;
      x_dir_addr : cross cp_dir, cp_addr;
      // NOTE: reads carry no byte strobes, so read x strobe bins are not
      // protocol-meaningful. The natural expression is an ignore_bins using
      // binsof, but Verilator 5.050 supports neither binsof in a select
      // expression nor explicit cross bins. Rather than silence the warning,
      // the cross is left unfiltered and those bins are expected holes.
      x_dir_strb : cross cp_dir, cp_strb;
    endgroup

    function new(string name, uvm_component parent);
      super.new(name, parent);
      cg_apb = new();
    endfunction

    function void write(apb_seq_item t);
      cg_apb.sample(t.m_dir, t.m_resp, t.m_addr, t.m_strb, t.m_wdata);

    endfunction

    function void report_phase(uvm_phase phase);
      `uvm_info(get_type_name(),
                $sformatf("functional coverage = %0.2f%%", cg_apb.get_coverage()),
                UVM_LOW)
    endfunction
  endclass

  //--------------------------------------------------------------------------
  // Scoreboard — reference model of the register file
  //--------------------------------------------------------------------------
  class apb_scoreboard extends uvm_component;
    `uvm_component_utils(apb_scoreboard)

    uvm_analysis_imp #(apb_seq_item, apb_scoreboard) m_imp;

    bit [APB_DATA_W-1:0] m_ref [int];
    int unsigned         m_checks;
    int unsigned         m_writes;
    int unsigned         m_errors_seen;

    // Anything at or above this offset is expected to return PSLVERR.
    int unsigned         m_err_base = 32'h0000_0100;

    function new(string name, uvm_component parent);
      super.new(name, parent);
      m_imp = new("m_imp", this);
    endfunction

    function void write(apb_seq_item t);
      bit in_range = (t.m_addr < m_err_base);

      // 1. response check
      if (in_range && t.m_resp != APB_OKAY)
        `uvm_error(get_type_name(),
                   $sformatf("unexpected PSLVERR on in-range access: %s",
                             t.convert2string()))
      if (!in_range) begin
        m_errors_seen++;
        if (t.m_resp != APB_ERR)
          `uvm_error(get_type_name(),
                     $sformatf("expected PSLVERR on out-of-range access: %s",
                               t.convert2string()))
        return;   // no data model for the error region
      end

      // 2. data check against the reference model
      if (t.m_dir == APB_WRITE) begin
        bit [APB_DATA_W-1:0] cur = m_ref.exists(t.m_addr) ? m_ref[t.m_addr] : '0;
        for (int b = 0; b < APB_STRB_W; b++)
          if (t.m_strb[b]) cur[b*8 +: 8] = t.m_wdata[b*8 +: 8];
        m_ref[t.m_addr] = cur;
        m_writes++;
      end else begin
        bit [APB_DATA_W-1:0] exp = m_ref.exists(t.m_addr) ? m_ref[t.m_addr] : '0;
        m_checks++;
        if (t.m_rdata !== exp)
          `uvm_error(get_type_name(),
                     $sformatf("READ DATA MISMATCH addr=0x%08h exp=0x%08h got=0x%08h",
                               t.m_addr, exp, t.m_rdata))
        else
          `uvm_info(get_type_name(),
                    $sformatf("read match addr=0x%08h data=0x%08h",
                              t.m_addr, t.m_rdata), UVM_HIGH)
      end
    endfunction

    function void report_phase(uvm_phase phase);
      `uvm_info(get_type_name(),
                $sformatf("scoreboard: %0d write(s), %0d read check(s), %0d error-region access(es)",
                          m_writes, m_checks, m_errors_seen), UVM_LOW)
      if (m_checks == 0 && m_writes == 0 && m_errors_seen == 0)
        `uvm_error(get_type_name(),
                   "scoreboard saw no transactions — the test proved nothing")
    endfunction
  endclass

  //--------------------------------------------------------------------------
  // Agent
  //--------------------------------------------------------------------------
  class apb_agent extends uvm_agent;
    `uvm_component_utils(apb_agent)

    apb_sequencer m_seqr;
    apb_driver    m_drv;
    apb_monitor   m_mon;

    uvm_analysis_port #(apb_seq_item) m_ap;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      m_mon = apb_monitor::type_id::create("m_mon", this);
      m_ap  = new("m_ap", this);
      if (get_is_active() == UVM_ACTIVE) begin
        m_seqr = apb_sequencer::type_id::create("m_seqr", this);
        m_drv  = apb_driver ::type_id::create("m_drv",  this);
      end
    endfunction

    function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      m_mon.m_ap.connect(m_ap);
      if (get_is_active() == UVM_ACTIVE)
        m_drv.seq_item_port.connect(m_seqr.seq_item_export);
    endfunction
  endclass

  //--------------------------------------------------------------------------
  // Environment
  //--------------------------------------------------------------------------
  class apb_env extends uvm_env;
    `uvm_component_utils(apb_env)

    apb_agent      m_agent;
    apb_scoreboard m_sb;
    apb_coverage   m_cov;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      m_agent = apb_agent     ::type_id::create("m_agent", this);
      m_sb    = apb_scoreboard::type_id::create("m_sb",    this);
      m_cov   = apb_coverage  ::type_id::create("m_cov",   this);
    endfunction

    function void connect_phase(uvm_phase phase);
      super.connect_phase(phase);
      m_agent.m_ap.connect(m_sb.m_imp);
      m_agent.m_ap.connect(m_cov.analysis_export);
    endfunction
  endclass

  //--------------------------------------------------------------------------
  // Tests
  //--------------------------------------------------------------------------
  class apb_base_test extends uvm_test;
    `uvm_component_utils(apb_base_test)

    apb_env      m_env;
    int unsigned m_n_items = 20;

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
      super.build_phase(phase);
      void'($value$plusargs("N_ITEMS=%d", m_n_items));
      m_env = apb_env::type_id::create("m_env", this);
    endfunction

    function void end_of_elaboration_phase(uvm_phase phase);
      super.end_of_elaboration_phase(phase);
      uvm_top.print_topology();
    endfunction
  endclass

  // L0 smoke — a handful of random transfers.
  class apb_smoke_test extends apb_base_test;
    `uvm_component_utils(apb_smoke_test)

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
      apb_base_seq seq;
      phase.raise_objection(this, "smoke");
      seq = apb_base_seq::type_id::create("seq");
      if (!seq.randomize() with { m_n_items == 8; })
        `uvm_fatal(get_type_name(), "sequence randomize() failed")
      seq.start(m_env.m_agent.m_seqr);
      #200ns;
      phase.drop_objection(this, "smoke");
    endtask
  endclass

  // L1 — constrained-random write/read with data checking.
  class apb_random_test extends apb_base_test;
    `uvm_component_utils(apb_random_test)

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
      apb_write_read_seq seq;
      phase.raise_objection(this, "random");
      seq = apb_write_read_seq::type_id::create("seq");
      if (!seq.randomize() with { m_n_items inside {[8:24]}; })
        `uvm_fatal(get_type_name(), "sequence randomize() failed")
      seq.start(m_env.m_agent.m_seqr);
      #200ns;
      phase.drop_objection(this, "random");
    endtask
  endclass

  // L2 — error-response test: PSLVERR must be produced and detected.
  class apb_error_test extends apb_base_test;
    `uvm_component_utils(apb_error_test)

    function new(string name, uvm_component parent);
      super.new(name, parent);
    endfunction

    task run_phase(uvm_phase phase);
      apb_error_seq seq;
      phase.raise_objection(this, "error");
      seq = apb_error_seq::type_id::create("seq");
      if (!seq.randomize() with { m_n_items inside {[4:12]}; })
        `uvm_fatal(get_type_name(), "sequence randomize() failed")
      seq.start(m_env.m_agent.m_seqr);
      #200ns;
      phase.drop_objection(this, "error");
    endtask
  endclass

endpackage

`endif
