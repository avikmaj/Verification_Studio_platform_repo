# Architecture

## The organising idea

One semantic model, many replaceable backends.

```
SystemVerilog source
   → frontend (ISVFrontend)            slang today; Surelog/UHDM/native later
   → Design IR                         the platform's own model — the stable layer
   → lint · navigation · UVM overlay
   → simulator (ISimulator)            Verilator today; VCS/Questa/Xcelium/native later
   → run artefacts                     log · waveform · coverage DB
   → coverage (ICoverageEngine) · waveform (IWaveformReader)
   → regression DB + reproducibility
   → reports · CI
```

The value is not in any one box. It is in the arrows staying stable while the
boxes are replaced.

## Boundaries that must not be crossed

These are enforceable by inspection and are the first thing to check in review:

1. **Nothing above `language/` imports `pyslang`.** The frontend adapter is the
   only translation point between slang's AST and the Design IR. If a caller
   reaches for a slang symbol, the frontend has stopped being replaceable.

2. **Nothing outside `simulator/verilator.py` knows Verilator exists.** Argument
   construction, log grammar and coverage file layout are all confined there.
   The regression runner takes a `Simulator` and never inspects which one.

3. **The regression engine does not depend on the simulator implementation.**
   It builds once, runs many, records evidence. Swapping the backend changes
   nothing in `regression/`.

4. **Reports render from one dataset.** JSON, Markdown and HTML are three views
   of `build_report()`. They cannot disagree, because there is nothing to
   disagree about.

5. **Capability maps are computed, not asserted.** `VerilatorSimulator.
   capabilities()` reads the detected version. A 5.020 install and a 5.050
   install do not claim the same features.

## Why the Design IR is not UHDM (and not slang's AST)

Both are good, and both are someone else's model. The IR here is deliberately
small and serialisable: it records *what was written*, with source locations,
plus the minimum resolved semantics needed to navigate. It does not evaluate,
and it does not try to be a complete design database.

That matters because the IR is what the lint engine, the UVM overlay, the
coverage planner and — eventually — the verification graph are written against.
Those consumers must survive a frontend swap. A thin, owned IR is the seam that
makes that possible; adopting UHDM directly would move the seam into a
third-party schema.

UHDM remains the right *interchange* format, and a `surelog_adapter` producing
the same IR is the planned second implementation. That is the test of whether
the seam is real.

## Status discipline as a type

`RunStatus` is not a string. It has five members and one hard rule enforced in
`_classify()`: `PASS` is only returned when named positive evidence was
observed — a UVM report summary with zero errors, or `$finish` with exit code 0.
Every other outcome falls through to `NOT_VERIFIED` with the reason recorded.

The regression summary applies the same rule at the aggregate level: a
regression is `PASS` only if *every* run is `PASS`; any `NOT_VERIFIED` or
`BLOCKED` with no outright failures yields `NOT_VERIFIED`, never `PASS`.

Negative tests invert cleanly: `expect: FAIL` means the pass criterion is that
the violation was **detected**. A negative test that passes silently is a
failure, and is reported as one.

## Reproducibility as a first-class artefact

`repro.json` is written for every run, not just failures. It captures source
hash, Git commit *and dirtiness*, tool versions, execution host, defines,
include paths, plusargs, seed and the exact command lines.

Two design choices matter:

- **Incomplete records say so.** `missing_fields` is computed, and `complete`
  goes false. A record that cannot reproduce a run must not look authoritative.
- **`reproduce` verifies before running.** It re-hashes the sources and reports
  `BLOCKED` if they changed, rather than running different code and calling the
  difference non-determinism.

## Execution hosts

`ISimulator` implementations declare where their executable runs: `NATIVE`,
`WSL` or `REMOTE`. This is what makes Windows support real rather than
aspirational — the Python layers run natively, and the backend translates paths
at the boundary. It is also the mechanism a future remote/farm backend will
use, so it was worth building before it was strictly needed.

## Concurrency model

The regression runner uses a thread pool, not processes. The work is
subprocess-bound (each job is an external simulator invocation), so the GIL is
irrelevant and threads keep artefact handling simple. Each job writes into its
own `results/<test>/seed_<n>/` directory; the only shared mutable state is
SQLite, which is opened per operation in WAL mode.

## Compile caching

The build stamp hashes the full argument set *and* the source content *and* the
simulator version. Any change invalidates. This is what makes `build once, run
many seeds` safe: the image genuinely corresponds to the recorded sources.

## Where the differentiation is meant to live

Not in the editor, and not in the waveform viewer. In the graph that connects:

```
source ↔ symbol ↔ UVM component ↔ sequence ↔ transaction ↔ signal ↔
assertion ↔ coverage bin ↔ waveform range ↔ regression failure ↔ seed ↔ commit
```

The pieces built so far are the vertices and the plumbing that produces them
with evidence attached: the IR (source, symbols, covergroups, constraints), the
coverage DB (bins with hits and locations), the waveform DB (signals with time
ranges), the regression DB (failures with signatures, seeds and commits), and
the reproducibility record that ties a result back to exact inputs.

The edges — transaction database, waveform↔transaction correlation,
assertion↔transaction correlation — are the next substantial piece of work, and
they are `PLANNED`, not implied.
