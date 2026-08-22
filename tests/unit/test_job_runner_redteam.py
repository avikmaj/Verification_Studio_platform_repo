"""Executed red-team of the untrusted-project boundary (job runner).

A hostile `uvmstudio.yaml` — whether it arrives through a future upload
endpoint or by onboarding an untrusted third-party VIP locally — must not be
able to read files outside its own root, exfiltrate server env vars, or point
the UVM path anywhere but the server's. The API loads every project
`confine=True`; local dev defaults `confine=False` (unchanged).

Every test here is the attack, executed. RT-J-00N ids match
RED_TEAM_PLATFORM.md.
"""

from __future__ import annotations

import os

import pytest

from uvmstudio.core.errors import ProjectError
from uvmstudio.core.project import Project


def _write(tmp, yaml_text: str):
    (tmp / "uvmstudio.yaml").write_text(yaml_text)
    (tmp / "dummy.sv").write_text("module tb; endmodule\n")
    return tmp


# ---------------------------------------------------------------------------
# RT-J-001 — path escape (absolute, traversal, symlink) is refused confined
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("files", [
    "[/etc/passwd]",
    '["../../../../etc/hostname"]',
])
def test_rtj001_source_path_escape_blocked_confined(tmp_path, files):
    _write(tmp_path, f"name: e\ntop: tb\nfilesets: [{{name: rtl, files: {files}}}]\n")
    p = Project.load(tmp_path, confine=True)
    with pytest.raises(ProjectError, match="outside the project root"):
        p.source_files()


def test_rtj001_symlink_escape_blocked_confined(tmp_path):
    _write(tmp_path, "name: e\ntop: tb\n"
                     "filesets: [{name: rtl, files: [sneaky.sv]}]\n")
    os.symlink("/etc/hostname", tmp_path / "sneaky.sv")
    p = Project.load(tmp_path, confine=True)
    with pytest.raises(ProjectError, match="outside the project root"):
        p.source_files()


def test_rtj001_include_dir_escape_blocked_confined(tmp_path):
    _write(tmp_path, "name: e\ntop: tb\n"
                     "filesets: [{name: rtl, files: [dummy.sv], "
                     "include_dirs: [/etc]}]\n")
    p = Project.load(tmp_path, confine=True)
    with pytest.raises(ProjectError, match="outside the project root"):
        p.include_dirs()


# ---------------------------------------------------------------------------
# RT-J-002 — env-var exfil through a "not found" error is refused confined
# ---------------------------------------------------------------------------

def test_rtj002_env_exfil_refused_without_reading_value(tmp_path, monkeypatch):
    monkeypatch.setenv("UVMSTUDIO_SECRET", "SECRET-TOKEN-abc123")
    _write(tmp_path, "name: e\ntop: tb\n"
                     'filesets: [{name: rtl, files: ["${UVMSTUDIO_SECRET}.sv"]}]\n')
    with pytest.raises(ProjectError) as exc:
        Project.load(tmp_path, confine=True)
    # refused BY NAME, and the value must never appear in the error
    assert "UVMSTUDIO_SECRET" in str(exc.value)
    assert "SECRET-TOKEN" not in str(exc.value)


# ---------------------------------------------------------------------------
# RT-J-003 — yaml-chosen uvm_home is refused; only the server's is allowed
# ---------------------------------------------------------------------------

def test_rtj003_arbitrary_uvm_home_blocked_confined(tmp_path, monkeypatch):
    monkeypatch.setenv("UVM_HOME", "/opt/uvm-core/src")
    _write(tmp_path, "name: e\ntop: tb\nuvm_home: /etc\n"
                     "filesets: [{name: rtl, files: [dummy.sv]}]\n")
    with pytest.raises(ProjectError, match="confined mode uvm_home"):
        Project.load(tmp_path, confine=True)


def test_rtj003_server_uvm_home_reference_allowed_confined(tmp_path, monkeypatch):
    monkeypatch.setenv("UVM_HOME", str(tmp_path / "uvmsrc"))
    (tmp_path / "uvmsrc").mkdir()
    _write(tmp_path, "name: e\ntop: tb\nuvm_home: ${UVM_HOME}\n"
                     "filesets: [{name: rtl, files: [dummy.sv]}]\n")
    p = Project.load(tmp_path, confine=True)
    assert p.resolved_uvm_home() == (tmp_path / "uvmsrc").resolve()


# ---------------------------------------------------------------------------
# Unconfined (local dev) keeps the old behavior — the fix is opt-in per load
# ---------------------------------------------------------------------------

def test_unconfined_absolute_path_still_allowed(tmp_path):
    # local dev may legitimately reference a shared IP path; confine is the
    # API's choice, not forced on the library
    shared = tmp_path / "shared.sv"
    shared.write_text("module extra; endmodule\n")
    _write(tmp_path, f"name: e\ntop: tb\n"
                     f"filesets: [{{name: rtl, files: [dummy.sv, {shared}]}}]\n")
    p = Project.load(tmp_path, confine=False)
    assert any(str(f) == str(shared.resolve()) for f in p.source_files())


# ---------------------------------------------------------------------------
# The seeded projects must still load under the API's confined boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proj", ["golden_apb", "oran_ecpri"])
def test_seeded_projects_load_confined(proj, monkeypatch):
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "examples" / proj
    if not (root / "uvmstudio.yaml").exists():
        pytest.skip(f"{proj} not present")
    monkeypatch.setenv("UVM_HOME", os.environ.get("UVM_HOME", "/opt/uvm-core/src"))
    p = Project.load(root, confine=True)
    assert p.source_files()      # resolves without escaping root
