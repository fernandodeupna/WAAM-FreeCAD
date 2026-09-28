"""WAAM slicing and compact export helpers."""

from __future__ import annotations

from .step_to_waam_dsl import DSL_VERSION, build_waam_dsl, write_waam_dsl
from .slice_plan import (
    DEFAULT_BEAD_WIDTH_MM,
    DEFAULT_LAYER_HEIGHT_MM,
    DEFAULT_OVERLAP_PCT,
    SlicePlanArtifacts,
    write_slice_plan_for_document,
)

__all__ = [
    "build_waam_dsl",
    "write_waam_dsl",
    "DSL_VERSION",
    "DEFAULT_BEAD_WIDTH_MM",
    "DEFAULT_LAYER_HEIGHT_MM",
    "DEFAULT_OVERLAP_PCT",
    "SlicePlanArtifacts",
    "write_slice_plan_for_document",
]
