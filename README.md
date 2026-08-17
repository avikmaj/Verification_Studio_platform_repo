# UVM Verification Studio

A SystemVerilog/UVM verification platform: real language frontend, replaceable
simulator backends, functional coverage, waveform analysis, regression
orchestration, reproducibility and CI generation.

**Not** a mock IDE, not a toy SystemVerilog interpreter, not a UVM clone. It
drives a real IEEE 1800 frontend (slang), executes on a real simulator, reads
real coverage databases and real waveforms, and refuses to report a result it
did not observe.

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

Stages 0-3 of the roadmap are implemented and executable end to end:
project model → frontend → elaboration → lint → build → run → seeds →
regression → coverage → waveform → reproducibility → reports → CI.

Constrained-random and covergroup execution run on the simulator backend.
A native constraint solver (Z3-backed) and a native simulation kernel are
`PLANNED`; see `docs/ROADMAP.md`.

`docs/FEATURE_STATUS.md` is the authoritative capability record, with the
evidence for every claim.

## Licence

Apache-2.0. Third-party components and their licences are listed in
`THIRD_PARTY_NOTICES.md`. No third-party source is vendored into this
repository.
