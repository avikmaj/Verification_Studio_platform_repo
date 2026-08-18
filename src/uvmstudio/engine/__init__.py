"""UVM Verification Studio native simulation engine.

An event-driven SystemVerilog simulation kernel owned by this project:
four-state values, an IEEE 1800 stratified scheduler, an interpreter over
slang's *bound* (type-checked) AST, and our own VCD writer.

Status: EXPERIMENTAL, subset simulator. The supported subset is enumerated in
`interp.SUPPORTED`; anything outside it raises `UnsupportedFeature` loudly —
this engine never silently downgrades semantics. Correctness on the subset is
established by differential testing against Verilator, not by assertion.
"""

from .fourstate import FourState
from .kernel import Kernel, Signal
from .vcd_writer import VCDWriter
