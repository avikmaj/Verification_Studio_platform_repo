# UVM Verification Studio — README

**Package:** `uvm_verification_studio` — a full-stack SystemVerilog/UVM
verification platform: real IEEE-1800 language frontend, replaceable simulator
backends, an own event-driven simulation engine, functional coverage, waveform
analysis, regression orchestration, reproducibility, and CI generation.
**Author:** AVIK MAJUMDAR

![platform](https://img.shields.io/badge/methodology-SystemVerilog%20%2F%20UVM-blue)
![frontend](https://img.shields.io/badge/frontend-slang%2011.0.0-blue)
![simulator](https://img.shields.io/badge/simulator-Verilator%205.050-blue)
![uvm](https://img.shields.io/badge/UVM-Accellera%202020.3.1-blue)
![native engine](https://img.shields.io/badge/native%20engine-N1--N4%20EXPERIMENTAL-orange)
![tests](https://img.shields.io/badge/tests-155%20passing-brightgreen)
![PASS](https://img.shields.io/badge/PASS-simulator%20evidence%20only-brightgreen)
![license](https://img.shields.io/badge/license-MIT-informational)

> **PASS authority.** A test is PASS only when a simulator executed it and the
> evidence says so. Every run writes `repro.json` and a per-run log parsed for
> named evidence — that evidence is the single source of truth. Nothing is
> inferred from code inspection, and `NOT_VERIFIED` is never reported as PASS.

**Not** a mock IDE, not a toy SystemVerilog interpreter, not a UVM clone. It
drives a real IEEE 1800 frontend (slang), executes on a real simulator (and,
experimentally, its own kernel), reads real coverage databases and real
waveforms, and refuses to report a result it did not observe.

---

## The one rule

> **PASS requires simulator evidence. Absence of an error is not evidence.**

Every run resolves to exactly one of:

| status | meaning |
|---|---|
| `PASS` | the simulator ran and every pass criterion was observed |
| `FAIL` | the simulator ran and a failure was observed |
| `NOT_VERIFIED` | no evidence, or evidence insufficient to judge |
| `BLOCKED` | never got as far as running (build/tool/environment) |
| `ERROR` | fault in the platform itself |

`NOT_VERIFIED` is never promoted to `PASS`, and a regression containing one
never reports `PASS`. For a negative test (`expect: FAIL`), PASS means the
expected violation was **detected**.

The same discipline applies to features. Every component publishes a capability
map — `SUPPORTED`, `PARTIALLY_SUPPORTED`, `EXPERIMENTAL`, `PLANNED`,
`UNSUPPORTED` — and `docs/FEATURE_STATUS.md` records the measurement behind
each claim. See it before trusting any capability.

---

## Quick start

```bash
pip install -e .

uvmstudio env                       # what this machine actually has
uvmstudio init my_vip               # scaffold a runnable project
cd my_vip

uvmstudio compile                   # parse, type-check, elaborate
uvmstudio elaborate                 # dump hierarchy / classes / covergroups
uvmstudio lint                      # layered lint over the IR
uvmstudio build                     # build a simulation image
uvmstudio run random --seed 42      # one test, one seed
uvmstudio regress --tier L1 -j 4    # a regression tier
uvmstudio coverage --threshold 90   # merge + gate on functional coverage
uvmstudio waves results/random/seed_42/waves.fst --signal tb_top.sum
uvmstudio reproduce results/random/seed_42/repro.json
uvmstudio ci github                 # generate a pipeline that calls this CLI
```

Every data command takes `--json`. Exit codes are stable and classify the
failure (see `uvmstudio --help`), so CI can branch without parsing text.

### Windows / PowerShell

The platform runs natively on Windows. The pure-Python layers — project model,
slang frontend, lint, regression, coverage, waveform, reports, CI generation —
work as-is. Simulation is *dispatched*: because Verilator has no supported
native Windows build, the backend detects WSL and translates paths at the
boundary (`C:\work\tb.sv` → `/mnt/c/work/tb.sv`).

```powershell
uvmstudio env
uvmstudio regress -p . --tier L1 --exec-host wsl --wsl-distro Ubuntu -j 4
.\uvmstudio-ci.ps1 -Project . -Tier L1 -Seed 1 -Jobs 4
```

See `docs/WINDOWS.md`.

---

## Architecture

```
                        apps / CLI
                            |
   +------------------------+------------------------+
   |            |           |            |           |
 frontend    simulator   coverage    waveform    regression
 (ISVFrontend)(ISimulator)(ICoverage) (IWaveform) (IRegression)
   |            |           |            |           |
 slang 11    Verilator   verilator     VCD (native)  SQLite
 (pyslang)   5.050       coverage.dat  FST (fst2vcd)
   |            |
 Surelog*    VCS/Questa/Xcelium/native*        (* PLANNED)
```

The interfaces in `plugins/interfaces.py` are the contract. Nothing above a
boundary imports a concrete implementation:

- the IDE, lint, regression and coverage layers never import `pyslang`
- nothing outside `simulator/verilator.py` knows Verilator exists
- swapping a backend is a registration, not a refactor

**Why Verilator first.** It is scaffolding and a reference oracle, not the
product. A simulator abstraction with zero implementations is an untested
abstraction; and a future native kernel can only be proven correct by running
identical source against a known-good simulator and diffing. Verilator is the
only such reference that is free, scriptable and installable in CI. The native
SystemVerilog kernel remains the long-term goal — reached with evidence rather
than hope.

---

## Layout

```
src/uvmstudio/
  core/         project model, logging, hashing, process, platform (WSL/Windows)
  plugins/      the stable interface contracts
  language/     diagnostics, design IR, frontend abstraction, slang adapter
  uvm/          Accellera UVM discovery and version detection
  simulator/    ISimulator + Verilator backend
  coverage/     neutral coverage model + Verilator coverage.dat reader
  waveform/     VCD parser, FST reader, format dispatch
  lint/         layered rule engine over the IR
  regression/   SQLite DB, parallel runner, reports (JSON/MD/HTML)
  repro/        reproducibility records
  ci/           GitHub / GitLab / Jenkins / shell / PowerShell generators
  cli/          the uvmstudio command
examples/golden_apb/   real UVM APB4 environment (the acceptance test)
tests/unit/            core tests, no simulator required
tests/conformance/     SystemVerilog semantics the frontend must preserve
```

---

## Reproducibility

Every run writes `repro.json` capturing source hash, Git state (including
dirtiness), tool versions, exec host, defines, include paths, plusargs, seed and
the exact build/run command lines.

`uvmstudio reproduce` verifies the source hash **before** re-running, and
reports `BLOCKED` rather than a misleading result if the tree has changed. A
record missing any field required for reproduction is marked incomplete and
says so.

---

## Status

The full pipeline is implemented and executable end to end: project model →
frontend → elaboration → lint → build → run → seeds → regression → coverage →
waveform → reproducibility → reports → CI. Real Accellera UVM builds and runs
locally, in a 1 GB cgroup (low-memory build mode, behavioral parity proven),
and on the free-tier cloud backend.

The **native simulation engine** (`--backend native`) is our own — no external
toolchain. It has shipped four increments, all `EXPERIMENTAL` and each
differential-tested against Verilator 5.050:

| increment | capability |
|---|---|
| **N1** | event-driven four-state kernel + own VCD writer |
| **N2** | concurrent SVA (assert/cover, `\|->`/`\|=>`, `disable iff`, `##N`, sampled-value fns), vacuity-aware |
| **N3** | classes — properties, methods, `new()`, `this`, reference-semantics handles, null diagnosis |
| **N4** | z3-backed `randomize()` — constraint blocks, `inside`, `dist` (solved, never dropped), `soft`, pre/post_randomize, seed-stable |

Two VIPs run at 100% functional coverage; the platform's own evidence pipeline
and untrusted-input boundary have been red-teamed with **executed** attacks
(`docs/RED_TEAM_PLATFORM.md`). `docs/FEATURE_STATUS.md` is the authoritative
capability record, with the measurement behind every claim and a ledger of the
34 defects found and fixed during bring-up.

## License

MIT — see [`LICENSE`](LICENSE). Third-party components and their licenses are
listed in `THIRD_PARTY_NOTICES.md`; no third-party source is vendored into this
repository.
