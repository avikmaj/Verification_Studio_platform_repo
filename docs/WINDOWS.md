# Windows and PowerShell

UVM Verification Studio is drivable from Windows PowerShell. This document is
precise about what runs natively and what is dispatched, because "works on
Windows" is exactly the kind of claim that gets overstated.

## What runs natively on Windows

Everything that is pure Python:

| capability | native Windows |
|---|---|
| project model (`uvmstudio.yaml`) | yes |
| SystemVerilog frontend (slang via pyslang) | yes — pyslang ships Windows wheels |
| compile / elaborate / diagnostics | yes |
| design IR, hierarchy, class/covergroup extraction | yes |
| lint engine | yes |
| coverage database read / merge / report | yes |
| waveform: VCD read | yes |
| waveform: FST read | needs `fst2vcd` on `PATH` (GTKWave) |
| regression DB, orchestration, reports | yes |
| reproducibility records | yes |
| CI generation | yes |

## What is dispatched

Verilator has no supported native Windows build. Rather than pretend otherwise,
the backend declares an **execution host** and translates paths across the
boundary.

```
Windows host (PowerShell)                    WSL distro
  uvmstudio (Python)      ──wsl.exe──▶       verilator, simv
  C:\work\vip\tb.sv       ──translate──▶     /mnt/c/work/vip/tb.sv
```

Detection is automatic: on Windows, if `wsl.exe` reports at least one installed
distro, the Verilator backend uses `ExecHost.WSL`. If WSL is absent, the backend
reports itself **unavailable** — it does not fail obscurely later or silently
skip the run.

Override explicitly when needed:

```powershell
uvmstudio regress -p . --tier L1 --exec-host wsl --wsl-distro Ubuntu-24.04
```

or via environment:

```powershell
$env:UVMSTUDIO_WSL_DISTRO = "Ubuntu-24.04"
$env:UVMSTUDIO_VERILATOR  = "/opt/verilator-5.050/bin/verilator"
```

## Setup

```powershell
# 1. Python side (native Windows)
py -3 -m pip install -e .
uvmstudio env          # confirms frontend + whether WSL is visible

# 2. Simulator side (inside WSL)
wsl -- sudo apt-get update
wsl -- sudo apt-get install -y verilator z3 gtkwave git build-essential

# 3. Accellera UVM (inside WSL, or on a drive both can see)
wsl -- git clone --depth 1 https://github.com/accellera-official/uvm-core.git ~/uvm-core
$env:UVM_HOME = "\\wsl$\Ubuntu\home\<you>\uvm-core\src"   # or a C:\ path

uvmstudio env          # should now show verilator on wsl(<distro>)
```

## Path rules

The translation layer handles drive-letter paths. It **rejects UNC paths**
(`\\server\share\...`) rather than mangling them, because there is no `/mnt/`
equivalent — map the share to a drive letter, or copy sources local.

| host path | inside WSL |
|---|---|
| `C:\work\vip\tb.sv` | `/mnt/c/work/vip/tb.sv` |
| `D:\a b\c.sv` | `/mnt/d/a b/c.sv` |
| `\\server\share\x.sv` | **rejected with a clear error** |

Keep the project on a Windows drive that WSL can see (`/mnt/<drive>`), or keep
it entirely inside the WSL filesystem and drive it from `wsl -- uvmstudio ...`.
Mixing the two halves — sources on `\\wsl$\` accessed from Windows Python — is
slow and is not a supported configuration.

## Process control

POSIX process groups do not exist on Windows. The process manager uses
`CREATE_NEW_PROCESS_GROUP` and tears down a timed-out simulation with
`taskkill /PID <pid> /T /F`, which is the only reliable way to kill a process
tree there. A hung simulation therefore does not leak worker processes on
either platform.

## PowerShell entry point

`uvmstudio ci cli` generates `uvmstudio-ci.ps1` alongside the POSIX
`uvmstudio-ci.sh`. Both run the same commands, so a pipeline cannot drift from
what an engineer runs locally.

```powershell
.\uvmstudio-ci.ps1 -Project . -Tier L1 -Seed 1 -Jobs 4
.\uvmstudio-ci.ps1 -Project . -Tier L2 -ExecHost wsl -WslDistro Ubuntu-24.04
```

It returns the CLI's exit code, so `if ($LASTEXITCODE -ne 0)` works in any
Windows CI agent.

## Commercial simulators

VCS, Questa and Xcelium adapters are `PLANNED`. Questa has native Windows
builds and will use `ExecHost.NATIVE`; VCS and Xcelium are Linux-only and will
use `ExecHost.WSL` or `ExecHost.REMOTE`. Because the execution host is part of
the `ISimulator` contract rather than baked into a backend, adding them does not
change any caller.
