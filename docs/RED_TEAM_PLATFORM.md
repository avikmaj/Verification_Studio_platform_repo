# Red-Team of the Platform Claims — Executed Adversarial Review

Date: 2026-08-19 · Scope: every claim in the end-of-arc status
("real UVM locally / 1 GB cgroup / free-tier cloud; two VIPs at 100%;
13-component suite validated unmodified; own simulator with SVA,
differential-tested; 98/98 tests; no claim without a run").

Method: the same discipline applied to the O-RAN suite's signoff — attacks
are **executed**, not argued. Every finding below has a run behind it, every
fix has a re-attack behind it, and what the claims do *not* prove is stated
in the bounds section rather than omitted.

---

## Findings

### RT-P-001 — Evidence classifier under hostile logs: DEFENDED (9/10)
Ten adversarial logs fed directly to the PASS classifier: nonzero
UVM_ERROR/UVM_FATAL counts, empty logs, bare "TEST PASSED" text with no
summary, clean summary with nonzero exit code, timeout, summary plus
assertion `%Error`, segfault. Nine classified correctly (FAIL or
NOT_VERIFIED, never PASS). The tenth is RT-P-002.

### RT-P-002 — Summary without $finish earned PASS: **FOUND → FIXED**
A clean UVM report summary with **no `$finish`** classified PASS. A run that
printed its report but never completed in an orderly way should not count as
evidence. PASS now requires the summary **and** `$finish`;
summary-without-finish is NOT_VERIFIED with a named reason. Re-attack
verified both directions. Logged as defect 27.

### RT-P-003 — "100% functional coverage" measured a degraded model: **FOUND → FIXED**
The eCPRI VIP's `bins t[] = {[0:7]}` silently collapsed into ONE aggregate
bin under Verilator, and both crosses inherited the collapse: 17 bins
recorded where the model intended 66. **A run driving a single msg_type
value would still have scored 100%.** This is the exact self-referential
trap the O-RAN red-team named RT-006. Fixed with explicit per-encoding bins;
the model now records **66/66 bins including all 48 cross bins**, re-run
clean at 100.00%, 6/6 PASS. Logged as defect 28.

### RT-P-007 — Forged counter line flips FAIL to PASS: **FOUND → FIXED**
The counter parse was last-write-wins, so a testbench printing a second
`UVM_ERROR :    0` line *after* a real `UVM_ERROR :    5` summary classified
**PASS** despite five real errors. Fixed: counters take the **max** across
all occurrences — a forged line can only make a run look worse (fail-safe),
never cleaner. A summary printed more than once is itself refused. Pinned by
`test_classifier_redteam.py`.

### RT-P-008 — Inline errors hidden behind a clean summary: **FOUND → FIXED**
Real inline `UVM_ERROR tb.sv(42) @ 100: …` messages plus a forged
`UVM_ERROR : 0` summary passed, because inline messages were ignored whenever
a summary was present. A genuine UVM run keeps inline and summary consistent;
the contradiction is now a failure. (Care taken that the summary's own
`UVM_ERROR : 0` counter line is not miscounted as an inline error — the legit
clean run still passes.)

### Beat-the-classifier — the honest limit (C3/C4): STATED, not fixed
A testbench that actually runs, does **no checking**, prints a genuinely
clean UVM report and calls `$finish` will classify PASS — and should, from
the log's point of view: the text is honest about what executed; it cannot
reveal that nothing was *checked*. Forging the `$finish` line itself via
`$display` is the same class. This is not a classifier-defensible property
from log text alone; it is defended one layer up by the VIP-level
anti-vacuity guards (scoreboard transaction counts, coverage, the
`m_checks`/`m_errors_seen` guard of defect 17). The classifier's job — not
being fooled by text that contradicts itself — is now hardened (RT-P-007/008);
the deeper "did the TB check anything" question stays a VIP-signoff
responsibility, stated here so it is not mistaken for a classifier guarantee.

### RT-P-004 — SVA semantics and reproducibility: DEFENDED
- Overlapping attempts (req at T and T+1, only the first granted): the
  engine tracked both independently — 1 pass AND 1 fail, not a merged
  verdict. Pipelined back-to-back requests: 2 passes.
- Repro tamper: source modified after a recorded run →
  `uvmstudio reproduce` refused with `BLOCKED: source hash changed
  (a5ac179c… → 09888b8c…)`. Restored and confirmed.

### RT-P-005 — Own VCD writer: identifier collision + scope duplication: **FOUND → FIXED**
Dumping a *hierarchical* design exposed two writer bugs: port-unified
signals were assigned colliding identifiers (every `dut` variable mapped to
the same ident) and each hierarchical path re-opened scopes from the root,
duplicating the top scope. Value lookups on the dump returned nothing. The
original round-trip test was blind to both (flat scope, unique keys) — a
test-quality finding in its own right. Fixed with proper VCD aliasing (one
identifier, multiple `$var` names) and scope-transition tracking; the
round-trip now reads back correct counter values at every probe time, and a
hierarchy+alias regression test pins it. Logged as defect 29.

### RT-P-006 — Mutation campaign: **3/3 KILLED**
Planted bugs, demanded detection, restored, re-verified green:

| mutation | planted defect | expectation | observed |
|---|---|---|---|
| M1 | APB read data off-by-one (`mem[idx]+1`) | data-integrity FAIL | **0/4 PASS, 4 FAIL** |
| M2 | eCPRI pack: PC_ID/SEQ_ID swapped | pack-vs-golden FAIL | **smoke FAIL** |
| M3 | eCPRI DUT stops flagging illegal versions | missed-detection FAIL | **2/2 FAIL** |

After each restore, both VIPs re-ran 6/6 PASS. The checkers have teeth on
the planted set; the set is small (3) and does not constitute a kill-rate
study of the O-RAN scale.

### Waveform-dump capability: CONFIRMED, both paths
- Verilator backend: VCD and FST dumping (`waves: always|on_fail|never`),
  read back by the platform's own VCD reader / FST conversion path;
  failure waves retained by policy.
- **Native engine: dumps VCD through our own writer with no third-party
  code**, verified by round-trip through our own reader on a hierarchical
  design — counter values correct at every probed time (0→2→4→6 across the
  reset and count sequence). The answer to "do we have our own waveform-dump
  simulator" is yes, with RT-P-005's fixes as the proof it is now correct.

---

## Job-runner red-team — hostile source / yaml (executed 2026-08-22)

Scope: the untrusted-project boundary. A `uvmstudio.yaml` is attacker-
controlled input the moment the platform ingests a third-party VIP (the
amba_bfm / O-RAN onboarding path) or exposes any upload endpoint. Every
attack below was **executed** against a real `Project.load`, then re-run
after the fix.

**Reachability first (stated, not assumed):** the deployed API has **no
upload endpoint** — `/jobs` takes a project *directory name* confined to the
workspace by `_safe_project_path`, so today only server-seeded projects
(`golden_apb`, `oran_ecpri`) run remotely. The findings below were therefore
**latent** — not remotely live — but they are real primitives that go live
the instant an untrusted yaml is loaded. Fixed at the primitive, not left to
the absence of a reachable path.

### RT-J-001 — source/include path escape: **FOUND → FIXED**
A hostile `files: [/etc/passwd]`, `files: ["../../../etc/hostname"]`, or a
symlink inside the project pointing out, all resolved **outside the project
root** and would be handed to the compiler, whose diagnostics echo file
content — an arbitrary server-file read. Fixed: `Project.load(...,
confine=True)` (the API's mode) refuses any source or include path whose
*resolved* location escapes the root — catching absolute paths, `..`, and
symlinks in one check. Six attack shapes now blocked; pinned by
`test_job_runner_redteam.py`.

### RT-J-002 — env-var exfil through error text: **FOUND → FIXED**
`files: ["${UVMSTUDIO_API_TOKEN}.sv"]` expanded the secret into a path, and
the "source file not found: SECRET-TOKEN-abc123.sv" error **echoed it back**.
Fixed: confined `_expand` allowlists env vars (`UVM_HOME` only) and refuses
any other **by name, without reading its value** — the secret can no longer
reach the error text.

### RT-J-003 — arbitrary uvm_home: **FOUND → FIXED**
`uvm_home: /etc` added `/etc` to the include path. Fixed: in confined mode
`uvm_home` must resolve to the server's own `$UVM_HOME` (the allowlisted
`${UVM_HOME}` reference); any other value is refused. Both seeded projects,
which use `uvm_home: ${UVM_HOME}`, still load confined.

### RT-J-004 — resource exhaustion: **OPEN, BOUNDED (not fixed)**
A hostile source with a huge `generate` loop or deep elaboration can OOM the
worker. `MAX_CONCURRENT=1` and the 3600 s job timeout bound wall-clock and
concurrency but **not memory**. A per-job memory cap (cgroup on the worker
subprocess) is the fix and is not implemented — stated here rather than
claimed. The remote container's own limit is the only current backstop.

---

## Bounds — what the claims do NOT prove (accepted, not hidden)

1. **"13/13 unmodified" is a platform-integration result.** It proves the
   platform ingests, builds, runs and honestly classifies the O-RAN Lane-2B
   suite. It does NOT strengthen that suite's own red-team bounds: the
   suite's DUTs remain self-checking models (its RT-001), its stimulus/
   checker loop remains partially self-referential (its RT-002). The
   platform *inherits* those bounds; 13/13 must not be quoted as new DUT
   verification.
2. ~~SVA differential coverage is thin~~ **NARROWED 2026-08-22**: the
   differential suite is now 10 cases with exact failure-count agreement
   (|=>, disable iff killing in-flight attempts, ##N chains, $rose/$stable/
   $past, pipelined attempts, cover pass-actions), and it caught defect 34
   plus two measured Verilator deviations (attempt merging; dropped cover
   pass-actions), both pinned from both sides. What REMAINS bounded:
   sampling approximates Observed-region values, and blocking-assign drives
   at the sampling edge can race, as documented in `engine/sva.py`.
3. ~~Performance unmeasured~~ **RETIRED 2026-08-22, measured** by
   `scripts/perf_baseline.py` with outputs cross-checked between engines:
   RTL 962 cyc/s native vs 2.86 M cyc/s Verilator (~3000× slower, offset
   only by zero build time); randomize() 78/s vs 86/s — parity, solving
   dominates both. The engine now HAS classes, SVA, and z3 randomize
   (N2–N4, all EXPERIMENTAL); still no UVM or covergroups, still not a
   Verilator replacement for RTL throughput — that part of the bound
   stands, now with numbers instead of adjectives.
4. **19 of the platform tests skip without the toolchain** (Verilator +
   UVM_HOME). A bare CI runner silently exercises less of the suite;
   treat green CI without the toolchain image as partial.
   (`tests/unit/_toolchain.py` now prevents a stale system Verilator from
   silently shadowing 5.050 — skips remain named, never counted as pass.)
5. ~~The remote 4/4 covers the golden project only~~ **RETIRED
   2026-08-22**: the workspace image seeds `oran_ecpri` alongside
   `golden_apb` (commit 3615211, Railway deployment 2afcfce4), and remote
   job `074b90324947` ran the eCPRI L1 regression on the live free-tier
   container: **4/4 PASS** (ecpri_smoke + 3× ecpri_random, server-assigned
   random seeds, each with UVM summary + 0 errors + $finish). Two projects
   now exercised remotely; "any project" remains unproven — one-yaml
   onboarding of further suites is the standing path.
6. **No DAST ran on the API** (no HawkScan credentials in this
   environment). The hostile-yaml surface is now covered by the executed
   job-runner red-team above (RT-J-001..004, three fixed and pinned, one
   open+bounded); network/transport-level DAST of the HTTP surface remains
   unrun.
7. **The mutation set is 3**, chosen to span data path, encode path and
   checker blindness — indicative, not exhaustive.

---

## Signoff

**PLATFORM-INTEGRITY SIGNOFF — GRANTED, BOUNDED.**

The evidence pipeline (classification, reproducibility, regression
accounting, coverage reporting, assertion verdicts including vacuity, and
both waveform paths) survived executed adversarial attack; the three
defects the attack found (27, 28, 29) are fixed with re-attacks and pinned
by regression tests, and all prior results were re-established green after
every mutation restore (99/99 platform tests; both VIPs 6/6; coverage
100.00% of the full-strength 66-bin model).

This is **not** a simulator-completeness signoff (bound 3), **not** a
security signoff (bound 6), and does **not** upgrade the O-RAN suite's own
bounded signoff (bound 1).

Defect ledger after this review: **29 logged, all with resolution or
explicit open disposition, none silent.**
