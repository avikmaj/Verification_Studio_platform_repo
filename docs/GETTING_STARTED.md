# Getting Started — UVM Verification Studio

What this is: a verification platform that compiles and runs real
SystemVerilog and real Accellera UVM, with regression, functional coverage,
waveforms, lint, reproducibility records, a web dashboard and a cloud
execution backend. Nothing is simulated by inference — PASS always means a
simulator ran and produced named evidence.

Three ways to use it, from zero-install to full local. Pick one; they all
drive the same engine.

---

## Path A — Browser only (nothing to install)

1. Open the dashboard: **https://uvm-verification-studio.vercel.app**
2. In the header field paste the API URL:
   `https://uvmstudio-api-production.up.railway.app` and press **Connect**.
3. Paste the bearer token (ask the deployment owner; it is set as
   `UVMSTUDIO_API_TOKEN` in Railway).
4. You can now browse every project in the workspace — elaborated design
   hierarchy and classes, lint findings, coverage with holes, regression
   history with failure clustering, waveform summaries — and submit
   compile / lint / build / regress jobs with live streaming logs.

UVM regressions run even on the free 1 GB container: the platform detects
the memory limit and switches to its measured low-memory build mode
automatically.

---

## Path B — Windows (PowerShell, no WSL needed)

One-time setup, about five minutes:

```powershell
# 1. Python (skip if `py --version` already works)
winget install Python.Python.3.12
# close and reopen PowerShell after this — PATH updates need a new window

# 2. Get the platform
cd $env:USERPROFILE\Downloads
git clone https://github.com/avikmaj/Verification_Studio_platform_repo.git vs-platform
cd vs-platform
py -m pip install -e .

# 3. Point at the cloud deployment (once, persists across windows)
setx UVMSTUDIO_API_URL "https://uvmstudio-api-production.up.railway.app"
setx UVMSTUDIO_API_TOKEN "<token from the deployment owner>"
# reopen PowerShell once more so setx takes effect
```

Daily use — simulation executes in the cloud, results stream to your
terminal:

```powershell
cd $env:USERPROFILE\Downloads\vs-platform
py -m uvmstudio.cli.main regress -p examples\golden_apb --backend remote --tier L1
```

Frontend-only commands (compile, elaborate, lint) run fully locally on
Windows — no simulator install needed:

```powershell
py -m uvmstudio.cli.main compile -p examples\golden_apb
py -m uvmstudio.cli.main lint    -p examples\golden_apb
```

If you have WSL with Verilator inside it, local simulation works too:
`--exec-host wsl` dispatches the backend into the distro and translates
paths automatically.

---

## Path C — Linux (full local: build, simulate, coverage, waves)

Prerequisites: Python 3.10+, git, a C++ toolchain (`g++ make perl ccache`),
`libz-dev liblz4-dev libzstd-dev`, and z3.

```bash
# 1. Get the platform
git clone https://github.com/avikmaj/Verification_Studio_platform_repo.git
cd Verification_Studio_platform_repo
pip install -e .          # (--break-system-packages on Debian/Ubuntu)

# 2. Verilator 5.050 from source (distro packages are too old for UVM-era
#    class support; ~25 min on 2 cores, once)
git clone --branch v5.050 https://github.com/verilator/verilator /tmp/verilator
(cd /tmp/verilator && autoconf && ./configure --prefix=/opt/verilator-5.050 \
   && make -j$(nproc) && sudo make install)
export PATH=/opt/verilator-5.050/bin:$PATH

# 3. Accellera UVM 2020.3.1 (pinned — reproducibility depends on it)
git clone --depth 1 --branch 2020.3.1 \
    https://github.com/accellera-official/uvm-core.git /opt/uvm-core
export UVM_HOME=/opt/uvm-core/src
```

Verify the install with the golden acceptance project:

```bash
uvmstudio regress -p examples/golden_apb --tier L2    # expect 6/6 PASS
uvmstudio coverage -p examples/golden_apb             # functional + code cov
uvmstudio waves    -p examples/golden_apb             # waveform summary
uvmstudio report   -p examples/golden_apb             # last regression report
```

On a machine with under 2 GB of RAM the low-memory build mode engages
automatically (`UVMSTUDIO_LOW_MEMORY=1` forces it anywhere). The native
engine needs no toolchain at all for the supported RTL subset:
`uvmstudio regress -p <proj> -b native`.

---

## Bringing your own testbench (proven on a real suite)

If your UVM environment already runs `run_test()` from a top module, the
platform needs exactly **one file** added to your repo — `uvmstudio.yaml`
listing sources, top, and tests. No source modifications. This was proven
on the 13-component O-RAN VIP Suite
(`github.com/avikmaj/Oran-vip-suite-ral-package`): one yaml, 13/13 PASS.

Minimal shape:

```yaml
name: my_vip
top: tb_top
language_standard: "1800-2017"
timescale: "1ns/1ps"
uvm_home: ${UVM_HOME}

filesets:
  - name: tb
    files: [path/to/my_pkg.sv, path/to/tb_top.sv]
    include_dirs: [path/to]
    defines: [UVM_NO_DPI]

default_backend: verilator
coverage: true
waves: on_fail

tests:
  - { name: my_smoke_test,  tier: L0, seeds: 1 }
  - { name: my_random_test, tier: L1, seeds: 3 }
  - { name: my_error_test,  tier: L2, seeds: 2 }
```

Then: `uvmstudio regress -p /path/to/your/repo --tier L2`.

Starting from nothing instead? `uvmstudio init my_vip` scaffolds a project,
or copy `examples/golden_apb` (APB4) / `examples/oran_ecpri` (eCPRI
transport, vplan-traced, 100% functional coverage) as references.

---

## The rules the platform enforces (so you don't have to)

- **PASS requires evidence.** A run is PASS only when the simulator executed
  and the log contains named positive evidence (UVM report summary with
  0 UVM_ERROR / 0 UVM_FATAL, `$finish` reached). No evidence → NOT_VERIFIED,
  and NOT_VERIFIED is never converted to PASS.
- **Negative tests pass by DETECTION.** An error test passes when the
  injected violation was caught, not when the test merely ran.
- **Seeds reproduce.** Every run records seed, tool versions, git state and
  the exact command in `repro.json`; `uvmstudio reproduce` re-runs it and
  refuses if the sources changed.
- **Capabilities are honest.** `uvmstudio capabilities` and
  `docs/FEATURE_STATUS.md` classify every feature SUPPORTED / PARTIALLY /
  EXPERIMENTAL / PLANNED / UNSUPPORTED, with measured evidence and 25+
  logged defects. Unsupported constructs fail loudly, never silently.

## Where to go next

- `docs/FEATURE_STATUS.md` — what works, with proof
- `docs/DEPLOYMENT.md` — running your own Vercel + Railway deployment
- `docs/ARCHITECTURE.md` — the plugin seams (ISimulator, ISVFrontend, …)
- `docs/ROADMAP.md` — what is deliberately not built yet
