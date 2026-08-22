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
   environment); its security posture rests solely on the documented
   trusted-deployment stance in DEPLOYMENT.md.
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
