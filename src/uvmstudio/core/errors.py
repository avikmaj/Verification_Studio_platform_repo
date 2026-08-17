"""Exception hierarchy for UVM Verification Studio.

Every failure mode in the platform maps to exactly one of these. The CLI turns
them into a stable exit code so that CI can branch on the *class* of failure
rather than parsing text.
"""

from __future__ import annotations


class StudioError(Exception):
    """Base class for all UVM Verification Studio errors."""

    exit_code: int = 1


class ProjectError(StudioError):
    """Project file missing, malformed, or internally inconsistent."""

    exit_code = 2


class FrontendError(StudioError):
    """SystemVerilog frontend could not be constructed or invoked."""

    exit_code = 3


class CompileError(StudioError):
    """Source failed to parse / elaborate. Diagnostics carry the detail."""

    exit_code = 4


class SimulatorError(StudioError):
    """Simulator backend unavailable, misconfigured, or failed to build."""

    exit_code = 5


class BackendUnavailable(SimulatorError):
    """Requested backend is not installed on this machine.

    Distinct from SimulatorError so callers can degrade gracefully (e.g. skip a
    differential comparison) instead of reporting a false failure.
    """

    exit_code = 6


class RegressionError(StudioError):
    """Regression database or orchestration failure."""

    exit_code = 7


class UnsupportedFeature(StudioError):
    """A construct is recognised but not implemented by this component.

    Raising this is mandatory. Silently degrading unsupported SystemVerilog
    semantics is forbidden by the platform's engineering rules.
    """

    exit_code = 8


class ReproducibilityError(StudioError):
    """A run could not be reproduced, or its metadata is incomplete."""

    exit_code = 9
