#!/usr/bin/env python3
"""Low-memory build mode: behavioral-parity proof.

The claim being defended (platform red-team): the low-memory build path
(-O0, stub PCH, ~91-TU split) changes only build MEMORY and TIME — never
simulation behavior. If that is true, the same project run at the same seed
must produce identical verdicts, identical coverage, and identical
simulation output under both modes. This script runs it both ways and diffs.

A behavioral difference here would be a correctness defect in the low-memory
mode, not an optimization — so the diff is the whole point.

Usage: python3 scripts/lowmem_parity.py [project] [--seed N] [--tier L1]
Requires: Verilator 5.050 + UVM_HOME set (real UVM build, both modes).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uvmstudio.core.project import Project
from uvmstudio.language.frontend import get_frontend
from uvmstudio.regression.runner import RegressionRunner
from uvmstudio.simulator.base import get_simulator


def _run(project_dir: Path, tier: str, seed: int, lowmem: bool) -> dict:
    # force the mode explicitly so the proof does not depend on the ambient
    # container budget
    if lowmem:
        os.environ["UVMSTUDIO_LOW_MEMORY"] = "1"
    else:
        os.environ.pop("UVMSTUDIO_LOW_MEMORY", None)
        os.environ["UVMSTUDIO_LOW_MEMORY"] = "0"

    proj = Project.load(project_dir)
    sim = get_simulator(proj.default_backend, jobs=2)
    runner = RegressionRunner(proj, sim, frontend=get_frontend("slang"), jobs=2)
    outcome = runner.run_regression(tier=tier, base_seed=seed)

    # collect the per-test evidence that must be seed-determined, not
    # build-determined: status, and the run log's UVM summary digest
    rows = []
    db = runner.db
    for r in db.runs(outcome.regression_id):
        log_path = r.get("log_path")
        digest = None
        if log_path and Path(log_path).exists():
            text = Path(log_path).read_text(errors="replace")
            # keep only UVM report lines — timing/paths legitimately differ
            summary = "\n".join(
                ln for ln in text.splitlines()
                if "UVM_" in ln or "TEST" in ln or ln.startswith("---"))
            digest = hashlib.sha256(summary.encode()).hexdigest()[:16]
        rows.append((r.get("test"), r.get("seed"), r.get("status"), digest))
    return {
        "status": outcome.summary.get("status"),
        "passed": outcome.summary.get("passed"),
        "total": outcome.summary.get("total"),
        "rows": sorted(rows, key=lambda x: (str(x[0]), x[1] or 0)),
        "coverage": outcome.summary.get("coverage"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?", default="golden_apb")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--tier", default="L1")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1] / "examples" / args.project
    print(f"# low-memory parity: {args.project} tier={args.tier} seed={args.seed}")
    print(f"# host: {os.uname().nodename}")

    std = _run(root, args.tier, args.seed, lowmem=False)
    low = _run(root, args.tier, args.seed, lowmem=True)

    print("\n           standard build      low-memory build")
    print(f"status     {std['status']:<20} {low['status']}")
    print(f"passed     {std['passed']}/{std['total']:<18} {low['passed']}/{low['total']}")
    print(f"coverage   {std['coverage']!s:<20} {low['coverage']!s}")
    print("\nper-test (test, seed, status, uvm-summary-digest):")
    for a, b in zip(std["rows"], low["rows"]):
        mark = "OK " if a == b else "DIFF"
        print(f"  {mark} std={a}  low={b}")

    parity = (std["status"] == low["status"]
              and std["rows"] == low["rows"]
              and std["coverage"] == low["coverage"])
    print(f"\nPARITY: {'PROVEN — identical behavior both modes' if parity else 'BROKEN — see DIFF rows above'}")
    sys.exit(0 if parity else 1)


if __name__ == "__main__":
    main()
