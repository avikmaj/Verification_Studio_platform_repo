# Third-party components

No third-party source is vendored into this repository. Everything below is a
runtime or build dependency, resolved by the package manager or fetched by the
user. Versions are the ones this platform was developed and measured against.

## Python runtime dependencies

| component | version | licence | role | link |
|---|---|---|---|---|
| slang / pyslang | 11.0.0 | MIT | SystemVerilog frontend: preprocess, parse, type check, elaborate | https://github.com/MikePopoloski/slang |
| Z3 (`z3-solver`) | 5.1.0 | MIT | SMT backend for the planned native constraint engine; also used by Verilator for `randomize()` | https://github.com/Z3Prover/z3 |
| PyYAML | >=6.0 | MIT | project file parsing | https://pyyaml.org |
| Jinja2 | >=3.1 | BSD-3-Clause | report/CI templating | https://palletsprojects.com/p/jinja/ |
| pytest | >=8.0 (dev) | MIT | test runner | https://pytest.org |

## External tools (not redistributed)

| component | version | licence | role | link |
|---|---|---|---|---|
| Verilator | 5.050 | LGPL-3.0 / Artistic-2.0 | first `ISimulator` backend and differential reference | https://github.com/verilator/verilator |
| `verilator_coverage` | 5.050 | LGPL-3.0 / Artistic-2.0 | coverage cross-check reference | https://verilator.org |
| GTKWave (`fst2vcd`) | 3.3.x | GPL-2.0-or-later | FST → VCD conversion for the FST reader | https://gtkwave.sourceforge.net |
| Accellera UVM (`uvm-core`) | 2020.3.1 | Apache-2.0 | the real UVM library; located, never reimplemented | https://github.com/accellera-official/uvm-core |
| Git | 2.x | GPL-2.0 | reproducibility metadata | https://git-scm.com |

## Licence interaction notes

- Verilator and GTKWave are invoked as **separate processes**. This project
  neither links against nor redistributes them, so their GPL/LGPL terms do not
  propagate to this Apache-2.0 codebase. Anyone redistributing a bundle that
  *includes* those binaries must comply with their licences directly.
- Accellera UVM is Apache-2.0 and is fetched by the user; it is not vendored.
- slang and Z3 are MIT and are consumed as Python wheels.

## Planned components (not yet integrated)

| component | licence | intended role |
|---|---|---|
| Surelog | Apache-2.0 | alternative frontend via UHDM |
| UHDM | Apache-2.0 | design interchange model |
| Verible | Apache-2.0 | formatter and style lint, LSP |
| Monaco Editor | MIT | IDE editor surface |
| Tauri | MIT / Apache-2.0 | desktop shell |
