# Feature status

The authoritative capability record. Every row states what was **measured**,
not what a datasheet claims. If a row says `SUPPORTED`, there is an executed
test behind it; if it says `PARTIALLY_SUPPORTED`, the limitation is named.

Status values: `SUPPORTED` · `PARTIALLY_SUPPORTED` · `EXPERIMENTAL` ·
`PLANNED` · `UNSUPPORTED`

Live version: `uvmstudio capabilities --json`

**Measurement environment**

| item | value |
|---|---|
| OS | Ubuntu 24.04, x86_64, 2 cores |
| Python | 3.11.15 |
| Frontend | slang 11.0.0 via pyslang 11.0.0 |
| Simulator | Verilator 5.050 (built from source, `v5.050`) |
| Constraint solver | Z3 5.1.0 (`z3 --in`, used by Verilator for `randomize()`) |
| UVM | Accellera `uvm-core` 2020.3.1 (`Accellera:1800.2:UVM:2020.3.1`) |
| Waveform tools | GTKWave `fst2vcd`, `verilator_coverage` |

---

## 1. Platform (this repository)

| capability | status | evidence |
|---|---|---|
| Project model (`uvmstudio.yaml`) | SUPPORTED | ordered filesets, globs, `${ENV}` expansion with hard failure on unset vars; 6 unit tests |
| Cumulative regression tiers L0-L5 | SUPPORTED | unit test: L2 ⊇ L1 ⊇ L0 |
| Source hashing (Merkle over ordered files) | SUPPORTED | content-, order- and layout-sensitive; 2 unit tests |
| Process manager w/ timeout + tree kill | SUPPORTED | POSIX `killpg`; Windows `taskkill /T /F`; timeout test kills a 30 s sleep in <20 s |
| Structured logging (human + JSONL) | SUPPORTED | dual sink, thread-safe |
| Stable exit-code taxonomy | SUPPORTED | `core/errors.py`; CI branches on failure class |
| Plugin interface boundaries | SUPPORTED | no caller imports a concrete backend; verified by import graph |
| Windows path translation (Win↔WSL) | SUPPORTED | 3 parametrised tests + UNC rejection test |
| WSL execution-host dispatch | PARTIALLY_SUPPORTED | implemented and unit-tested; **not yet executed on a real Windows host** |
| Reproducibility record | SUPPORTED | source hash, git state+dirtiness, tool versions, exec host, defines, incdirs, plusargs, seed, exact commands |
| `reproduce` with pre-flight hash check | SUPPORTED | refuses to re-run and reports `BLOCKED` when the tree changed |
| Regression DB (SQLite) + clustering | SUPPORTED | 3 unit tests incl. signature clustering 3×/1× |
| `NOT_VERIFIED` never counted as PASS | SUPPORTED | dedicated unit test |
| Reports: JSON / Markdown / HTML | SUPPORTED | rendered from one dataset; cannot disagree |
| CI generation (GH/GitLab/Jenkins/sh/ps1) | SUPPORTED | generated files call the same CLI as local runs |

## 2. SystemVerilog frontend — slang 11.0.0

Validated by 14 conformance tests in `tests/conformance/`.

| construct | status | note |
|---|---|---|
| preprocessor, `include`, `define` | SUPPORTED | driven through slang's own driver command line |
| parse + type check + elaborate | SUPPORTED | |
| modules, ports, directions | SUPPORTED | in/out/inout/ref mapped into the IR |
| interfaces, modports | SUPPORTED | modport port directions extracted |
| packages, imports, typedefs, enums | SUPPORTED | |
| classes, inheritance, virtual | SUPPORTED | base chain resolved by name |
| `rand` / `randc` classification | SUPPORTED | per-property rand mode in the IR |
| constraint blocks | PARTIALLY_SUPPORTED | **declared and located, expressions not lowered to IR** — see §6 |
| covergroups, coverpoints, bins | SUPPORTED | incl. `illegal_bins`, `ignore_bins` |
| cross coverage | PARTIALLY_SUPPORTED | crosses detected and named; target list extraction partial |
| explicit sampling detection | SUPPORTED | event control *and* `with function sample()` args |
| virtual interfaces | SUPPORTED | flagged on class properties |
| elaborated hierarchy | SUPPORTED | full instance paths |
| generate blocks | SUPPORTED | flattened into the enclosing unit |
| assertions / properties / sequences | PARTIALLY_SUPPORTED | located, **temporal semantics not analysed** |
| DPI declarations | PARTIALLY_SUPPORTED | parsed, not modelled |
| diagnostics w/ location + source line | SUPPORTED | `-Wno-<name>` suppression honoured |
| IEEE 1800-2017 / 1800-2023 selection | SUPPORTED | both accepted |
| VPI | PLANNED | |
| Constraint expression IR | PLANNED | |
| SVA temporal IR | PLANNED | |

## 3. Simulator backend — Verilator 5.050

Measured on this install. **These are Verilator's limits, not the platform's**,
and the capability map is computed from the detected version rather than assumed.

| capability | status | measured evidence |
|---|---|---|
| RTL synthesizable subset | SUPPORTED | golden DUT builds and runs |
| `--timing` (delays, event control) | SUPPORTED | clock/reset, `#10ns`, `@(posedge)` all execute |
| Classes | SUPPORTED | class-based TB compiles and runs |
| `randomize()` with constraints | SUPPORTED | `m_addr[1:0]==2'b00` and `inside {[1:16]}` both honoured over 20 draws |
| Seed determinism | SUPPORTED | `+verilator+seed+N`: seed 1 twice → identical value stream; seeds 1/7/12345 → distinct streams |
| Covergroup declaration + `sample()` | SUPPORTED | executes; bins recorded |
| Covergroup `with function sample(args)` | SUPPORTED | 5 coverpoints + 3 crosses = 36 bins recorded, 0 warnings |
| **Coverpoint referencing an enclosing class member** | **UNSUPPORTED** | `coverpoint m_tr.m_dir` → `%Warning-COVERIGN: ... ignoring covergroup '__vlAnonCG_cg_apb'` — **the entire covergroup is dropped**. This is the default UVM subscriber idiom. |
| **`binsof` in a cross select expression** | **UNSUPPORTED** | `ignore_bins x = binsof(cp_dir.rd)` → `%Warning-COVERIGN: Unsupported: 'binsof' in coverage select expression` |
| **Explicit cross bins** | **UNSUPPORTED** | `%Warning-COVERIGN: Unsupported: explicit coverage cross bins` |
| Covergroup coverage in `coverage.dat` | SUPPORTED | 3/3 bins, counts 1/3/2 — matches `verilator_coverage` |
| **In-language `covergroup::get_coverage()`** | **UNSUPPORTED** | **returns `0.00` even with bins hit — read the coverage DB instead** |
| **Per-coverpoint member access (`cg.cp`)** | **UNSUPPORTED** | `%Error: Member 'cp' not found in covergroup 'cg_t'` |
| Line / branch / expression / toggle coverage | SUPPORTED | cross-checked against `verilator_coverage` |
| VCD tracing | SUPPORTED | |
| FST tracing | SUPPORTED | **requires `liblz4-dev` + `libzstd-dev` at build time**, else `fatal error: lz4.h` |
| 4-state X propagation | PARTIALLY_SUPPORTED | 2-state by default; `--x-assign` only |
| DPI-C | SUPPORTED | untested here |
| SVA concurrent assertions | PARTIALLY_SUPPORTED | not exercised in this measurement round |
| Accellera UVM 2020.3.1 | see §4 | |

### Covergroups in UVM: the idiom that does not work

The standard `uvm_subscriber` coverage pattern —

```systemverilog
apb_seq_item m_tr;
covergroup cg_apb;
  cp_dir: coverpoint m_tr.m_dir { ... }     // <-- enclosing class member
endgroup
function void write(apb_seq_item t); m_tr = t; cg_apb.sample(); endfunction
```

— is legal IEEE 1800 and is what most UVM VIP is written against. Verilator
5.050 does not implement it and **silently discards the whole covergroup** with
a warning. With `-Wno-fatal` off it is an error; with warnings demoted you would
get a build that runs and reports 0 functional coverage for a reason that never
appears in the report. That is precisely the silent degradation this platform
forbids, so the golden VIP was rewritten rather than the warning silenced:

```systemverilog
covergroup cg_apb with function sample(apb_dir_e dir, apb_resp_e resp, ...);
  cp_dir: coverpoint dir { ... }            // <-- explicit argument
endgroup
function void write(apb_seq_item t);
  cg_apb.sample(t.m_dir, t.m_resp, t.m_addr, t.m_strb, t.m_wdata);
endfunction
```

Measured: the argument form compiles clean and records all 5 coverpoints and
all 3 crosses (36 bins). It is also better practice — the sampling contract is
explicit — so this is a portability fix, not a workaround.

**Consequence for existing VIP:** any UVM environment being brought up on this
backend needs the same transformation. It is mechanical but it is not nothing,
and it is the single biggest porting cost measured so far.

### Known Verilator lexer quirk

`bins small = {...}` fails to parse: `small` collides with the Verilog drive-
strength keyword.

```
%Error: s.sv:8:35: syntax error, unexpected STRENGTH keyword (strong1/etc)
```

Avoid `small`, `large`, `medium`, `highz0/1`, `pull0/1`, `strong0/1`, `weak0/1`
as bin names. This is recorded because the failure message does not point at
the real cause.

## 4. Accellera UVM

| item | status | evidence |
|---|---|---|
| UVM tree discovery + version detection | SUPPORTED | `uvm-core` identified as `2020-3.1`, generation `1800.2`, IEEE-1800.2 = true |
| Legacy generation recognition (1.0-1.2, 2017-x) | PARTIALLY_SUPPORTED | version-string parsing implemented; only 2020.3.1 measured |
| **UVM 2020.3.1 SystemVerilog elaboration on Verilator 5.050** | **SUPPORTED** | full `uvm_pkg.sv` + a `uvm_test` with phases/objections/`uvm_info` elaborated with **0 `%Error`** |
| **UVM 2020.3.1 executable image** | **SUPPORTED** | 2021 C++ translation units built, `MAKE_RC=0`, 21 MB binary |
| **UVM 2020.3.1 runtime execution** | **SUPPORTED** | see the run evidence below — `RUN_RC=0`, 0 UVM_ERROR, 0 UVM_FATAL |
| `run_test()` dispatch by name | SUPPORTED | `[RNTST] Running test hello_test...` |
| Factory construction of a `uvm_test` | SUPPORTED | `uvm_test_top` built and reported |
| Phasing (`run_phase`) | SUPPORTED | `[HELLO] UVM is alive` emitted from `run_phase` |
| Objections (`raise`/`drop`) | SUPPORTED | phase ended at **10 ns**, not 0 — the objection held the phase open |
| Report server + severity/id aggregation | SUPPORTED | `--- UVM Report Summary ---` with counts by severity *and* by id |
| Clean shutdown via `uvm_root` | SUPPORTED | `uvm_root.svh:633: Verilog $finish`, exit code 0 |
| UVM golden APB environment (agent stack) runtime | NOT_VERIFIED | elaborates clean; codegen not yet run. **Not claimed.** |
| UVM 1.2 (legacy generation) | NOT_VERIFIED | not attempted |
| UVM DPI (`uvm_re_match` etc.) | UNSUPPORTED | built with `UVM_NO_DPI`; regex-dependent features unavailable, confirmed by `[NO_VISIT_CHECK]` at runtime |

### UVM runtime evidence

```
UVM_INFO uvm_root.svh(476) @ 0: reporter [UVM/RELNOTES]
        Accellera:1800.2:UVM:2020.3.1
UVM_INFO @ 0: reporter [RNTST] Running test hello_test...
UVM_INFO tb.sv(9) @ 0: uvm_test_top [HELLO] UVM is alive
UVM_INFO uvm_report_server.svh(1009) @ 10000: [UVM/REPORT/SERVER]
--- UVM Report Summary ---
UVM_INFO :    4        UVM_ERROR :    0
UVM_WARNING :    2     UVM_FATAL :    0
- uvm_root.svh:633: Verilog $finish
RUN_RC=0
```

**Cost:** 2021 C++ translation units, ~2 h wall clock on 2 cores, 21 MB binary.
UVM's codegen dominates; size CI runners and containers accordingly.

**Honest reading:** the UVM *runtime* is proven on this backend — factory,
phasing, objections, report server and shutdown all work. What is **not** yet
proven is a full agent stack under load: the golden APB environment in
`examples/golden_apb/` elaborates clean but has not been through codegen, so
driver/monitor/sequencer/scoreboard/covergroup execution remains
`NOT_VERIFIED`. Two limitations stand regardless: the build uses `UVM_NO_DPI`,
which disables component-name constraint checking (`[NO_VISIT_CHECK]`,
`[UVM/COMP/NAMECHECK]`) and every regex-dependent feature; and UVM itself warns
it may withdraw `UVM_NO_DPI` support.

## 5. Coverage

| capability | status | evidence |
|---|---|---|
| Read Verilator `coverage.dat` | SUPPORTED | `\x01key\x02value` record format decoded |
| Covergroup bin extraction | SUPPORTED | full `cg.coverpoint.bin` hierarchy + hit counts |
| Line/branch/expr/toggle/FSM kinds | SUPPORTED | |
| **Differential validation** | SUPPORTED | parser output matches `verilator_coverage` exactly on all reported metrics: line 5/24, branch 0/4, expr 0/6, covergroup 3/3 |
| Multi-run merge (union bins, sum counts) | SUPPORTED | 6-run merge produced 16/18 functional bins |
| Hole reporting | SUPPORTED | named the two uncovered cross bins with source locations |
| Functional vs code coverage separation | SUPPORTED | functional = covergroup bins only; conflating them is prevented by a unit test |
| Coverage threshold gating | SUPPORTED | `uvmstudio coverage --threshold` |
| Exclusions / waivers | PLANNED | |
| UCIS export | PLANNED | model is UCIS-shaped to allow it |

## 6. Constrained random

| capability | status | note |
|---|---|---|
| Execution of `rand`/`randc`/`randomize()`/`with` | SUPPORTED | **via the simulator backend** (see §3) |
| Deterministic seeds and reproduction | SUPPORTED | measured, see §3 |
| Frontend recognition of rand modes + constraint blocks | SUPPORTED | see §2 |
| Lint over stimulus quality | PARTIALLY_SUPPORTED | unconstrained-rand and wide-`randc` rules implemented |
| **Native constraint IR + Z3 solver path** | **PLANNED** | Z3 is installed and used *by Verilator*; the platform's own constraint engine is not built |
| `solve...before` cycle detection | PLANNED | |
| Hardcoded-literal (stimulus gap) detection | PLANNED | rule `CRV011` declared, not implemented |

## 7. Waveform

| capability | status | evidence |
|---|---|---|
| VCD parse (scopes, vars, values) | SUPPORTED | 3 unit tests |
| Vector + scalar + 4-state values | SUPPORTED | `b11111111` decoded on an 8-bit signal |
| Cursor semantics (`value_at`) | SUPPORTED | last-change-at-or-before, unit tested |
| Time-window query | SUPPORTED | includes the value entering the window |
| FST read | PARTIALLY_SUPPORTED | via GTKWave `fst2vcd`; **native FST reader PLANNED**. Read a real 12-signal FST: scopes `tb_top`, `tb_top.u_dut`, 152 changes on `tb_top.sum` |
| Format detection by content | SUPPORTED | not by extension alone |
| Unreadable ≠ empty | SUPPORTED | raises `UnsupportedFeature` rather than returning 0 signals |
| EVCD | PLANNED | explicit error, not silent |
| Transaction overlay | PLANNED | |
| GUI viewer | PLANNED | |

## 8. Lint

9 rules implemented, 12 declared-but-unimplemented (visible in
`uvmstudio lint --json` under `rules`, each with its status).

| id | layer | status |
|---|---|---|
| `CRV001` rand fields with no constraint block | constraints | SUPPORTED |
| `CRV002` wide `randc` (cyclic state explosion) | constraints | SUPPORTED |
| `COV001` coverpoint without explicit bins | coverage | SUPPORTED |
| `COV002` covergroup without `illegal_bins` | coverage | SUPPORTED |
| `COV003` covergroup without explicit sampling | coverage | SUPPORTED |
| `UVM001` `uvm_component` without a constructor | uvm | SUPPORTED |
| `UVM002` sequence item with rand fields, no constraints | uvm | SUPPORTED |
| `UVM003` virtual interface above driver/monitor layer | uvm | PARTIALLY_SUPPORTED |
| `RTL001` module with no ports/vars/instances | structural | SUPPORTED |
| `UVM010`-`UVM015` phase/objection/factory/config_db/TLM/sequencer checks | uvm | PLANNED |
| `SVA001`-`SVA002` vacuity, reset qualification | assertions | PLANNED |
| `CRV010`-`CRV011` solve-before cycles, hardcoded literals | constraints | PLANNED |
| `COV010` unreachable cross bins | coverage | PLANNED |

Syntax and semantic linting are **not** re-implemented here — they are the
frontend's job and are surfaced through the same diagnostic model.

## 9. Not built

Named explicitly so nothing is implied by omission:

- Native SystemVerilog simulation kernel (scheduler, regions, delta cycles)
- Native constraint solver and constraint IR
- Native covergroup runtime
- SVA evaluation engine
- Transaction database and the unified verification graph
- UVM-aware debugger (hierarchy/phase/objection/factory/config_db views)
- Monaco/Tauri IDE, LSP server, cross-probing UI
- VCS / Questa / Xcelium / Riviera adapters
- UCIS import/export
- Regression intelligence beyond signature clustering and seed effectiveness
- DPI/VPI implementation

---

## Defects found and fixed during bring-up

Recorded because they are evidence the pipeline was exercised, not simulated.

| # | found by | defect | resolution |
|---|---|---|---|
| 1 | slang frontend | scaffold TB sampled a covergroup *type* instead of an instance (`cg_stim.sample`) | added `cg_stim_t cg_stim = new();` |
| 2 | slang frontend | scaffold DUT had no timescale while the TB did | added `` `timescale 1ns/1ps `` |
| 3 | conformance test | `has_sampling_event` always false — wrong pyslang API (`getCoverageEvent()`) | use `CovergroupType.coverageEvent` |
| 4 | manual review | `sample_args` never populated | read formals from the body's `sample` subroutine |
| 5 | differential vs `verilator_coverage` | coverage parser returned 37/37 `unknown` — record format uses `\x01`/`\x02` delimiters, invisible in a terminal | rewrote as a delimited key/value parse |
| 6 | CLI run | `uvmstudio waves` reported "0 signals" for FST instead of failing | added format detection + `fst2vcd` path; unreadable now raises |
| 7 | CLI run | vector signals unreachable by plain path (`tb_top.sum`) because the bit range was folded into the name | canonical name excludes full ranges; ranged path kept as an alias |
| 8 | Verilator build | FST tracing failed with `fatal error: lz4.h` | documented `liblz4-dev`/`libzstd-dev` prerequisite |
| 9 | Verilator parse | `bins small = {...}` rejected as a STRENGTH keyword | documented; renamed the bin |
| 10 | Railway deploy | `startCommand` in `railway.json` ran without a shell → `--port '$PORT' is not a valid integer` | dropped `startCommand`; Dockerfile `CMD` is the single source of truth |
| 11 | Railway deploy | PowerShell 5.1 `Set-Content -Encoding utf8` wrote a BOM → `failed to parse railway.json: invalid character 'ï'`, failing at SNAPSHOT_CODE with an empty build log | documented BOM-free write + `Format-Hex` check |
| 12 | Remote regression on Railway | golden UVM env built BLOCKED with no cause shown | job runner now surfaces the build log when a build failure blocks every run — that is what exposed defect 13 |
| 13 | Remote regression on Railway | Verilator 5.050 silently discards a covergroup whose coverpoint references an enclosing class member (the default UVM subscriber idiom) | golden VIP rewritten to `with function sample(args)`; verified 36 bins recorded, 0 warnings |
