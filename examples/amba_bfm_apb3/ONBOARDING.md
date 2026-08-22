# AMBA BFM Suite — platform onboarding (APB3 pilot)

**STATUS: PARTIAL — platform side done, end-to-end run NOT_VERIFIED (source
not in this workspace).**

## What was done

The AMBA BFM suite is pure SystemVerilog (no UVM) and reports pass/fail
through `amba_bfm_reporter`, which emits `[TEST_PASS]` / `[TEST_FAIL]` and
then `$finish`es. That exposed a real evidence gap and one deliverable:

1. **Classifier gap FOUND + FIXED.** The platform's PASS classifier keyed on
   the UVM report summary and, for non-UVM runs, fell back to
   "`$finish` + exit 0 ⇒ PASS". A BFM test that printed **`[TEST_FAIL]` and
   still `$finish`ed cleanly would have classified PASS.** The classifier now
   recognizes the reporter's tokens: `[TEST_FAIL]` (or `TEST FAILED`) forces
   FAIL regardless of `$finish`; `[TEST_PASS]` + orderly completion is the
   accepted positive evidence. A bare `$finish` with no token still passes
   (unchanged), so nothing regresses. Pinned by
   `tests/unit/test_classifier_redteam.py`.

2. **One-yaml project authored** (`uvmstudio.yaml`, APB3), modeling the
   suite's documented layout: `common/` infra package, `bfm/apb3/*`,
   `tb/per_protocol/apb3_*`, top `tb_top`, no UVM home, L1/L2/L3 tiers with
   the `slverr` negative test declared `expect: FAIL`.

## What remains (needs the suite source)

The suite source is not in this session (it lives in Avik's
`amba_bfm_suite` repo / laptop). To finish onboarding with real evidence:

1. Push `amba_bfm_suite` to a repo the platform session can clone (as the
   O-RAN suite was), **or** stage it into the workspace.
2. Drop this `uvmstudio.yaml` at the suite root so the relative paths
   resolve.
3. `uvmstudio regress --tier L1` (local) or submit a remote job. The exact
   test-class names in `apb3_test_pkg.sv` may differ from the placeholders
   here (`apb3_smoke` / `apb3_directed` / `apb3_slverr` / `apb3_random`) —
   align them to the suite's real names.
4. Only then does APB3 move from NOT_VERIFIED to PASS, with a run behind it.

The suite's own signoff records 2987/2987 PASS on Verilator 5.050; this
onboarding does not restate that as platform evidence until the platform has
run it (the same discipline applied to the O-RAN 13/13 result).
