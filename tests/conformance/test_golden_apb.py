"""Golden acceptance environment — frontend-level conformance.

These run without a simulator and gate every commit: they prove the platform
can compile and elaborate a *real* UVM environment against a *real* Accellera
UVM library, and that the IR correctly models the agent stack.

Simulation of this environment is a separate, longer acceptance step; its
status is recorded in docs/FEATURE_STATUS.md and is deliberately not asserted
here — a test that cannot run must not be dressed up as one that passes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uvmstudio.core.project import Project
from uvmstudio.language.frontend import CompileRequest, get_frontend
from uvmstudio.lint.engine import LintEngine
from uvmstudio.uvm.library import find_uvm_home, inspect_uvm

GOLDEN = Path(__file__).resolve().parents[2] / "examples" / "golden_apb"

pytestmark = pytest.mark.requires_uvm


@pytest.fixture(scope="module")
def uvm_home() -> Path:
    home = find_uvm_home(os.environ.get("UVM_HOME"))
    if home is None:
        pytest.skip("UVM_HOME not set — clone accellera-official/uvm-core")
    return home


@pytest.fixture(scope="module")
def compiled(uvm_home):
    proj = Project.load(GOLDEN)
    fe = get_frontend("slang")
    res = fe.compile(
        CompileRequest(
            files=proj.source_files(),
            include_dirs=proj.include_dirs(),
            defines=proj.defines(),
            top=proj.top,
            language_standard=proj.language_standard,
            timescale=proj.timescale,
            suppress_warnings=list(
                proj.backend_options.get("suppress_warnings", [])
            ),
        )
    )
    return proj, res


# --- the headline claim ---------------------------------------------------
def test_real_accellera_uvm_is_detected(uvm_home):
    lib = inspect_uvm(str(uvm_home))
    assert lib.pkg_file is not None and lib.pkg_file.name == "uvm_pkg.sv"
    assert lib.version is not None
    assert lib.generation in ("1.x", "2017", "1800.2")


def test_golden_env_compiles_and_elaborates_with_real_uvm(compiled):
    proj, res = compiled
    assert res.ok, res.diagnostics.format(root=proj.root, limit=25)
    assert res.error_count == 0


def test_uvm_class_library_is_present_in_the_ir(compiled):
    _, res = compiled
    d = res.design
    # The full library elaborates, not just a handful of stubs.
    assert len(d.classes) > 200
    for base in ("uvm_component", "uvm_object", "uvm_sequence_item",
                 "uvm_driver", "uvm_monitor", "uvm_agent", "uvm_env",
                 "uvm_test", "uvm_subscriber"):
        assert d.find_class(base) is not None, f"{base} missing from IR"


# --- the agent stack ------------------------------------------------------
EXPECTED_STACK = {
    "apb_seq_item":   "uvm_sequence_item",
    "apb_base_seq":   "uvm_sequence",
    "apb_sequencer":  "uvm_sequencer",
    "apb_driver":     "uvm_driver",
    "apb_monitor":    "uvm_monitor",
    "apb_coverage":   "uvm_subscriber",
    "apb_agent":      "uvm_agent",
    "apb_env":        "uvm_env",
    "apb_base_test":  "uvm_test",
}


@pytest.mark.parametrize("cls_name,base", sorted(EXPECTED_STACK.items()))
def test_agent_stack_inheritance(compiled, cls_name, base):
    _, res = compiled
    c = res.design.find_class(cls_name)
    assert c is not None, f"{cls_name} not in IR"
    assert c.base_class == base


def test_sequence_item_has_rand_fields_and_constraints(compiled):
    _, res = compiled
    item = res.design.find_class("apb_seq_item")
    rand_names = {v.name for v in item.rand_fields}
    assert {"m_dir", "m_addr", "m_wdata", "m_strb", "m_delay"} <= rand_names
    con_names = {c.name for c in item.constraints}
    # Legality, ranges, weighting and timing all constrained — not hardcoded.
    assert {"c_addr_aligned", "c_addr_range", "c_addr_dist",
            "c_strb", "c_delay", "c_wdata_dist"} <= con_names


def test_coverage_subscriber_carries_the_covergroup(compiled):
    _, res = compiled
    cov = res.design.find_class("apb_coverage")
    assert len(cov.covergroups) == 1
    cg = cov.covergroups[0]
    assert cg.name == "cg_apb"           # named property, not the anonymous type

    cps = {cp.name: cp for cp in cg.coverpoints}
    assert {"cp_dir", "cp_resp", "cp_addr", "cp_strb", "cp_wdata_corner"} <= set(cps)
    assert sum(1 for cp in cg.coverpoints if cp.is_cross) == 3
    assert len(cps["cp_addr"].bins) == 5


def test_driver_and_monitor_hold_the_virtual_interface(compiled):
    _, res = compiled
    for name in ("apb_driver", "apb_monitor"):
        c = res.design.find_class(name)
        vifs = [p for p in c.properties if p.is_virtual_interface]
        assert vifs, f"{name} has no virtual interface property"
        assert vifs[0].name == "m_vif"


def test_interface_modports_and_protocol_assertions(compiled):
    _, res = compiled
    unit = res.design.units["apb_if"]
    assert {m.name for m in unit.modports} == {"mst", "mon"}
    # SVA properties are located by the frontend (temporal semantics PLANNED).
    assert len(unit.properties) >= 3


def test_dut_is_elaborated_into_the_hierarchy(compiled):
    _, res = compiled
    paths = {n.path for n in res.design.all_instances()}
    assert "tb_top" in paths
    assert "tb_top.u_dut" in paths
    assert "tb_top.apb_bus" in paths


# --- lint scoping ---------------------------------------------------------
def test_lint_excludes_the_uvm_library(compiled, uvm_home):
    proj, res = compiled
    unscoped = LintEngine().check(res.design)
    scoped = LintEngine(scope_paths=[proj.root],
                        exclude_paths=[uvm_home]).check(res.design)
    assert len(unscoped) > len(scoped), "scoping had no effect"
    for f in scoped:
        assert f.location is None or str(uvm_home) not in f.location.file


def test_no_lint_errors_in_project_owned_code(compiled, uvm_home):
    proj, res = compiled
    findings = LintEngine(scope_paths=[proj.root],
                          exclude_paths=[uvm_home]).check(res.design)
    errors = [f for f in findings if f.severity_is_error]
    assert not errors, [f.format() for f in errors]
