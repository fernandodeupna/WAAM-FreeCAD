"""WAAM workbench package.

This reduced build keeps only the core runtime needed for the WAAM
workflow: WCS, STEP export, slice-first planning, minimal mesh prep,
NC generation, torch viewing, and the experimental runtime sandbox.
"""

from __future__ import annotations

__all__ = ["__version__", "generate_plan_and_nc", "run_pipeline_stepwise"]

__version__ = "0.2.0"


def generate_plan_and_nc(*_args, **_kwargs):
    """Stub kept for backwards compatibility with the legacy pipeline entry."""
    raise RuntimeError(
        "This WAAM build no longer includes the automated legacy pipeline. "
        "Use the dedicated commands to create a slice plan and generate NC."
    )


def run_pipeline_stepwise(*args, **kwargs):
    """Alias maintained for callers that previously invoked run_pipeline_stepwise."""
    return generate_plan_and_nc(*args, **kwargs)
