# Roadmap

Staged so that every milestone is executable. A stage is done when it runs,
has tests, and its capability map is honest — not when the files exist.

## Done — Stage 0: Foundation

- repository, packaging, licence and third-party tracking
- project model with hard failure on unresolved environment variables
- structured logging (human + JSONL), stable exit-code taxonomy
- process manager with timeout and process-tree teardown (POSIX + Windows)
- content hashing for reproducibility
- plugin interface boundaries

## Done — Stage 1: Frontend and IR

- slang 11.0 integration through its own driver command line
- normalised diagnostics with locations, source lines, `-Wno-` suppression
- Design IR: units, ports, parameters, classes, rand modes, constraint blocks,
  covergroups, coverpoints, bins, crosses, modports, elaborated hierarchy
- UVM library discovery and generation detection
- 14 conformance tests pinning SystemVerilog semantics

## Done — Stage 2: Lint

- layered rule engine over the IR (9 implemented, 12 declared as PLANNED)
- rule catalogue published with per-rule status

## Done — Stage 3: Simulation, regression, evidence

- `ISimulator` with a Verilator 5.050 backend and compile caching
- execution-host dispatch (native / WSL) with Windows path translation
- evidence-based status classification; `NOT_VERIFIED` never promoted
- parallel regression runner, seed expansion, per-run artefacts
- SQLite regression DB with failure-signature clustering and seed effectiveness
- reproducibility records with pre-flight hash verification
- coverage model + Verilator `coverage.dat` reader, merge, hole reporting
  (differentially validated against `verilator_coverage`)
- VCD parser; FST reader via `fst2vcd`; format detection by content
- JSON / Markdown / HTML reports
- CI generators: GitHub Actions, GitLab CI, Jenkins, shell, PowerShell

---

## Next — Stage 4: Prove UVM execution

The single most important open item. Elaboration of Accellera UVM 2020.3.1 on
Verilator 5.050 is confirmed clean; **execution is `NOT_VERIFIED`.**

- build and run `examples/golden_apb/` to completion on adequate hardware
- record per-generation results (1.2, 2017-x, 2020-x) in `FEATURE_STATUS.md`
- add a `requires_uvm` conformance suite: phases, factory overrides,
  `config_db`, objections, TLM analysis ports, sequence/sequencer handshake
- decide the UVM DPI story (`UVM_NO_DPI` limits regex-dependent features)

## Stage 5: Transaction database and the verification graph

The differentiator. Vertices exist; the edges do not.

- first-class transaction model: id, type, start/end time, parent/child,
  sequence, producing/consuming component, fields, status
- UVM transaction recording hooks (`uvm_transaction` begin/end)
- waveform ↔ transaction correlation (time interval → signal window)
- assertion ↔ transaction correlation
- coverage bin ↔ contributing transaction ↔ seed
- query API: from an uncovered bin, walk back to constraint and driver

## Stage 6: Native constraint engine

- constraint expression lowering from slang into a normalised constraint IR
- native fast path for ranges, `inside`, simple relations
- Z3 backend for the rest, with SystemVerilog-correct distribution semantics
- `solve...before`, `soft`, `constraint_mode()`, `rand_mode()`
- deterministic seeding independent of any simulator
- differential validation of value distributions against a reference simulator

## Stage 7: Assertions

- SVA temporal IR (sequences, properties, implication, repetition, `disable iff`)
- vacuity detection and cover-property pairing
- assertion → timestamp → waveform → transaction cross-links
- lint rules `SVA001`/`SVA002` promoted from PLANNED

## Stage 8: Additional backends

- VCS, Questa, Xcelium adapters behind the existing `ISimulator`
- remote/farm execution host
- differential conformance harness: same source, N backends, compare
  diagnostics, hierarchy, randomised values, event ordering, coverage, result

## Stage 9: IDE

- LSP server over the existing frontend and IR
- Monaco + Tauri shell; project tree, diagnostics, navigation
- UVM-aware navigation: driver ↔ sequencer, monitor ↔ analysis port,
  factory override ↔ registered type
- cross-probing wired to the Stage 5 graph
- waveform viewer with transaction overlays

## Stage 10: Native simulation kernel

Only after everything above is proven, and validated differentially at every
step.

- event scheduler, simulation time, delta cycles
- active / NBA / observed / reactive / postponed regions
- processes, fork/join, events, mailboxes, semaphores
- 4-state values, class runtime, interfaces, clocking blocks
- constraint, covergroup and assertion runtimes
- UVM runtime
- DPI/VPI

---

## Standing rules

1. No milestone ships without a test that executes it.
2. No capability is claimed without a recorded measurement.
3. Backends stay replaceable; the IDE stays independent of the runtime.
4. Differential testing wherever a reference simulator exists.
5. Unsupported constructs raise `UnsupportedFeature` — never degrade silently.
