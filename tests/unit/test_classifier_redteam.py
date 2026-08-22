"""Beat-the-classifier: forged-log attacks on the PASS classifier.

The classifier turns raw simulator text into a verdict. Text is forgeable by
a hostile or broken testbench, so the classifier must fail SAFE — a forged
line may make a run look worse, never cleaner. RT-P-00N ids match
RED_TEAM_PLATFORM.md.

What is IN scope: parser tricks that flip a real FAIL to PASS.
What is OUT of scope (stated, not fixed): a testbench that runs, does no
checking, prints a genuinely clean report and finishes — the log is honest
about what ran; the log cannot reveal that nothing was checked. That vacuity
is caught at the VIP level (anti-vacuity scoreboard/coverage guards), not
here.
"""

from __future__ import annotations

from pathlib import Path

from uvmstudio.simulator.base import RunRequest, RunStatus
from uvmstudio.simulator.verilator import VerilatorSimulator


def _classify(text: str, rc: int = 0, expect: str = "PASS") -> RunStatus:
    sim = VerilatorSimulator()
    req = RunRequest(binary=Path("/tmp/x"), run_dir=Path("/tmp"), seed=1,
                     uvm_testname="t", expect=expect, timeout_s=60,
                     plusargs=[], waves=None)
    return sim._classify(text=text, returncode=rc,
                         timed_out=False, request=req).status


_CLEAN = ("\n--- UVM Report Summary ---\nUVM_INFO :   42\n"
          "UVM_ERROR :    0\nUVM_FATAL :    0\n- tb.sv:100: Verilog $finish\n")
_REAL5 = ("\n--- UVM Report Summary ---\nUVM_ERROR :    5\n"
          "UVM_FATAL :    0\n- tb.sv:100: Verilog $finish\n")


def test_legit_clean_run_still_passes():
    assert _classify(_CLEAN) is RunStatus.PASS


def test_rtp007_forged_trailing_zero_cannot_lower_error_count():
    # a second "UVM_ERROR : 0" printed after a real "UVM_ERROR : 5" must NOT
    # flip the verdict — counters take the max (fail-safe)
    assert _classify(_REAL5 + "\nUVM_ERROR :    0\n") is RunStatus.FAIL


def test_rtp007_forged_high_count_still_fails_safe():
    # a forged HIGH count only makes it look worse — acceptable direction
    assert _classify(_CLEAN + "\nUVM_ERROR :   99\n") is RunStatus.FAIL


def test_rtp008_inline_errors_contradicting_clean_summary_fail():
    attack = "\nUVM_ERROR tb.sv(42) @ 100: uvm_test_top [SB] mismatch\n" + _CLEAN
    assert _classify(attack) is RunStatus.FAIL


def test_duplicate_summary_is_not_trusted():
    assert _classify(_CLEAN + _CLEAN) is RunStatus.FAIL


def test_real_inline_fatal_without_summary_fails():
    assert _classify(
        "\nUVM_FATAL tb.sv(9) @ 5: [X] boom\n- tb.sv:1: Verilog $finish\n"
    ) is RunStatus.FAIL


def test_summary_without_finish_still_not_verified():
    # RT-P-002 regression stays intact under the new parsing
    no_finish = "\n--- UVM Report Summary ---\nUVM_ERROR :    0\nUVM_FATAL :    0\n"
    assert _classify(no_finish) is RunStatus.NOT_VERIFIED
