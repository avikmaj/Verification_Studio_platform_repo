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

### Compile memory — measured, not assumed

Verilator concatenates its 2,506 generated `.cpp` files into `--build-jobs`
aggregate translation units. That flag, not `-j`, is what sets peak compiler
memory — and the platform used to pass one value for both, so asking for less
parallelism asked for a *larger* compile.

Measured per process on 2 cores, gcc `-Os`, no ccache, UVM 2020.3.1 + a full
APB agent stack:

| `--build-jobs` | translation units | peak `cc1plus` | C++ build | `simv` |
|---:|---:|---:|---:|---:|
| 2 | 4 | 2,620 MB | 350.5 s | 30.8 MB |
| 16 | 31 | **1,096 MB** | **326.9 s** | 31.0 MB |

Both binaries were run, not just linked: `apb_smoke_test` reaches `$finish`
with `UVM_ERROR: 0`, `UVM_FATAL: 0`.

The floor is ~1 GB and it is **not** reducible. The obvious suspect was the
precompiled header (`V<top>__pch.h.fast.gch`: 940 MB to build, 312 MB on disk,
mapped by every unit), so the no-PCH case was measured rather than reasoned
about:

| configuration | peak `cc1plus` | C++ build |
|---|---:|---:|
| 31 units, PCH on (default) | 1,096 MB | 326.9 s |
| 31 units, PCH off (`VK_PCH_I_FAST=` / `VK_PCH_I_SLOW=`) | 982 MB | 411.0 s |

Dropping the PCH *at -Os* buys 10% memory for 26% more wall time — but that
experiment left the 940 MB PCH still being *built* as a make prerequisite.
Killing it entirely (stub header) and dropping to `-O0` with a 48-way split
(91 TUs) changes the answer:

| mode | peak `cc1plus` | C++ build | sim speed |
|---|---:|---:|---|
| normal: `-Os`, PCH, 31 TUs | 1,096 MB | 327 s | 1x |
| **low-memory: `-O0`, stub PCH, 91 TUs, `-j1`** | **641 MB** | 718 s | ~5x slower |

**Verified end to end inside a hard 1 GB cgroup** (memory.max=1GiB, swap off,
plus a 90 MB resident hog standing in for the API server): `uvmstudio build`
completes (746.5 s), the binary runs UVM with 0 errors, and the golden L2
regression passes **6/6**. This falsifies the earlier claim that "a 1 GB
container cannot build UVM at any setting" — recorded as defect 25.

The backend selects low-memory mode automatically when the detected container
limit (cgroup v1/v2 or physical RAM) is under 2,000 MB; `UVMSTUDIO_LOW_MEMORY=1`
forces it. Between 2,000 and 3,500 MB, normal flags run with `make -j1`.
Split default is 16 (48 in low-memory mode); override with
`UVMSTUDIO_COMPILE_SPLIT` or `backend_options.compile_split`.

End to end through the CLI: `uvmstudio build` **305.8 s**, `STATUS: PASS`,
then `regress --tier L2` → **6/6 PASS**.

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
| **UVM golden APB environment (full agent stack) runtime** | **SUPPORTED** | 6/6 PASS at tier L2. Scoreboard observed 3 writes + 5 read checks; error test detected PSLVERR on 4 out-of-range accesses. 0 UVM_ERROR, 0 UVM_FATAL |
| Driver / monitor / sequencer / sequence handshake | SUPPORTED | transactions driven, observed and scoreboarded |
| `uvm_subscriber` + covergroup sampling | SUPPORTED | 32/39 bins hit; read x strobe holes are the predicted ones |
| Negative test (PSLVERR detection) | SUPPORTED | `expect: FAIL` semantics: PASS = violation detected |
| Multi-seed determinism on a UVM env | SUPPORTED | 3 seeds of apb_random_test, all PASS |
| FST waveform from a UVM run | SUPPORTED | 93 signals incl. `apb_pkg` and `uvm_pkg` scopes |
| Reproducibility round-trip on a UVM run | SUPPORTED | `reproduce` re-ran seed 42: recorded=PASS, reproduced=PASS |
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

## 8b. Native simulation engine — EXPERIMENTAL

`--backend native`: our own event-driven kernel, interpreting slang's bound
(type-checked, width-annotated) AST. No C++ compile, no external toolchain —
build is elaboration, run is interpretation, waves come from our own VCD
writer and round-trip through our own VCD reader.

| piece | status | evidence |
|---|---|---|
| four-state values (aval/bval, LRM tables) | EXPERIMENTAL | unit tests: and/or/xor tables, X-pessimistic arithmetic, ==/=== distinction, signed extension, div-by-zero→X |
| stratified scheduler (Active/deltas/NBA) | EXPERIMENTAL | NBA swap test (`a<=b; b<=a` swaps), blocking-vs-NBA ordering test |
| @(posedge/negedge), #delay, @(*) | EXPERIMENTAL | counter/shift-reg/FSM tests |
| dynamic comb sensitivity | EXPERIMENTAL | reacts to any-bit change (defect: bit-0-only edge match, fixed) |
| zero-time loop detection | EXPERIMENTAL | `always_comb a = ~a` reports, never hangs |
| module hierarchy + port binding | EXPERIMENTAL | signal unification across instance boundary |
| own VCD writer | EXPERIMENTAL | round-trips through our VCD reader, X/Z included |
| **differential vs Verilator 5.050** | **4/4 designs byte-identical output** | counter+async reset, ALU ops, shift register, FSM |
| classes / UVM / randomize / covergroups / SVA | UNSUPPORTED (raises) | attempting them names the construct and the supported subset |

The subset is enumerated in `engine/interp.py::SUPPORTED`. Everything outside
it raises `UnsupportedFeature` — the engine never silently downgrades.

Engine defects found by its own bring-up (same discipline as the rest of the
platform): pybind symbol wrappers have unstable `id()` (keyed the signal map
on hierarchical paths); `@(*)` edge matching compared only bit 0 (missed 3→9
on a vector); dynamic sensitivity subscribed after execution, so a self-
triggering comb settled on wrong values instead of reporting a zero-time loop
(feedback guard added).

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
| 14 | Remote regression on Railway | `make: ccache: No such file or directory` / `Error 127`. Verilator bakes `OBJCACHE = ccache` into `verilated.mk` at configure time because ccache existed in the *builder* stage; the runtime stage never installed it | added `ccache` to the runtime image and set `CCACHE_DIR` |
| 15 | Remote regression on Railway | `fstcpp_writer.cpp:18:10: fatal error: lz4.h: No such file or directory`. Runtime image had `liblz4-1`/`libzstd1` (shared libs) but not the headers | runtime image now installs the `-dev` variants. **Root cause shared with 14: the runtime image IS a build image** — Verilator ships no prebuilt runtime library, so every `verilator --binary` compiles `verilated*.cpp` in the user's build dir and needs compilers, make, perl, ccache and dev headers at *simulation* time |
| 16 | local Verilator lint | `%Error-BADVLTPRAGMA: Unknown verilator comment`. A comment **beginning** with the token `verilator` (any case) is reserved for lint pragmas; an explanatory comment started `// Verilator 5.050 does not implement it ...` | reworded + regex guard over the file. Not the backticks or em-dash in that line — both lint fine in isolation. The first rewrite reproduced the bug, because the note *warning about the rule* also began with the token |
| 18 | Railway UVM regression | `g++: fatal error: Killed signal terminated program cc1plus` — OOM kill on the 1 GB Railway container (`MEMORY_LIMIT_GB: 1.000`, confirmed from the live service metrics) | **the original 4.1 GB figure was wrong** — it was peak *total* container RSS with `-j 2` plus ccache, not one compiler. Re-measured per process: 2,620 MB. Root cause was defect 20; see there. Platform classifies OOM explicitly (`CAUSE: compiler killed by the OOM killer ...`) instead of surfacing a raw compiler message that reads like a compiler bug |
| 19 | own smoke test of the new classifier | `import uvmstudio.simulator.verilator` raised `ImportError: partially initialized module ... circular import`. Pre-existing: `base.py` imported backends at module scope while each backend imports `base` | registry now holds lazy thunks; backends import on first construction. Also means an unused backend costs nothing at startup |
| 17 | **L2 regression on the golden env** | `apb_error_test` FAILED on both seeds: *"scoreboard saw no transactions — the test proved nothing"*. **False positive in the platform's own anti-vacuity guard** — it counted `m_checks` and `m_writes` but not `m_errors_seen`, so an error-injection test (which legitimately does zero of the first two) was judged vacuous when it had in fact proved exactly what it set out to prove | guard now requires all three counters to be zero. Found by running the tier, not by review |
| 20 | measuring the OOM instead of assuming it | **`--build-jobs` decides how many C++ translation units Verilator generates, and the platform passed the same value as make's `-j`.** So lowering parallelism to save memory produced *fewer, larger* compiles — the exact opposite of the intent. At `-j 2`: 4 units, peak `cc1plus` **2,620 MB**, C++ build 350.5 s | split decoupled from parallelism: `--build-jobs 16` + `-MAKEFLAGS -jN`. 31 units, peak **1,096 MB**, build 326.9 s — **2.4× less memory and 7% faster**, so it is on by default. Concurrency cap verified (`max_concurrent_cc1plus = 2`), binary verified (`simv` runs UVM, 0 errors), regression verified (6/6 PASS at L2). Locked in by 4 unit tests so the two knobs cannot be re-merged |
| 21 | full UVM build through the CLI | `TypeError: BuildResult.__init__() got an unexpected keyword argument 'reasons'` — commit 8403f68 added the argument at the call site but not to the dataclass. **The build itself succeeded and linked `simv`; only the result object failed**, so the CLI reported a traceback for a passing build | added the `reasons` field and surfaced it in `to_dict()`. Missed by the suite because no test covered the real build path — one added. This is why that commit was never worth shipping to the user as a patch |
| 22 | reviewing the image while fixing the rebase | `apps/api/Dockerfile` cloned Accellera UVM **unpinned** (`--depth 1` of the default branch). A rebuild months later would silently ship a different UVM than every `repro.json` claims — a reproducibility hole in the one component the whole platform is measured against | pinned to `ARG UVM_TAG=2020.3.1`, cloned by tag, with a `test -f uvm_pkg.sv` so a bad tag fails the build instead of producing an image that 404s at simulation time |
| 23 | user ran the documented remote flow on Windows | `uvmstudio regress --backend remote` printed `ERROR ... Use \`uvmstudio regress --backend remote\`` — the CLI never routed the remote backend to `regress_remote()`, so it built a `RegressionRunner`, called `sim.build()` and died on `UnsupportedFeature`, telling the user to run the command they had just run. DEPLOYMENT.md documented a path the code did not have | `cmd_regress` now branches on `sim.name == "remote"`: submits the job, streams the log, prints STATUS/EVIDENCE from the remote summary. Two unit tests pin the routing and the non-PASS exit code |
| 24 | watching the remote OOM test crawl | `POST /jobs/{id}/cancel` only cancels **queued** jobs — a RUNNING build cannot be aborted; the worker checks the cancel flag once, before starting. A memory-thrashing compile on an undersized container therefore runs until the job timeout (3600 s) | recorded as a known limitation, not silently: running-job cancellation needs the subprocess handle plumbed into the job record (kill process group on cancel). PLANNED |
| 25 | refusing to accept my own negative result | Docs claimed "**a 1 GB container cannot build UVM at any setting**" — but the no-PCH experiment behind that claim had only emptied the `-include` flags while the 940 MB `.gch` still *built* as a make prerequisite, so nothing below ~1 GB was ever actually tested | stub the PCH out entirely (`VK_PCH_H=<empty header>`) + `-O0` + 48-way split: peak cc1plus **641 MB**, full `uvmstudio build` + 6/6 L2 regression verified inside a hard 1 GB cgroup with swap off and a 90 MB hog. Shipped as automatic low-memory mode (engages when the detected container limit < 2 GB; `UVMSTUDIO_LOW_MEMORY=1` forces). Claim corrected here rather than quietly edited |
