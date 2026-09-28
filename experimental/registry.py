from __future__ import annotations

from .runtime.loader import RuntimeActiveExperimentalPlanner


_PLANNERS = {
    "runtime_active": RuntimeActiveExperimentalPlanner(),
}
_PLANNER_ALIASES = {}


def get_experimental_planner(key: str):
    actual_key = _PLANNER_ALIASES.get(key, key)
    return _PLANNERS.get(actual_key)


def select_experimental_planner(*, plan_path=None, features_path=None, fused_operations_path=None):
    """
    Auto-select the per-part runtime workspace planner.

    Important rule:
    - auto mode should always route through a part-scoped workspace under
      Waam_tech/experimental/runtime/parts/<part_key>/<features_signature>/.
    - runtime export keeps runtime/active/ only as a disposable compatibility
      mirror of the selected part workspace.
    - legacy planners remain available only by explicit key.
    """
    return _PLANNERS.get("runtime_active")


def list_experimental_planners():
    seen = set()
    planners = []
    for planner in _PLANNERS.values():
        ident = getattr(planner, "key", id(planner))
        if ident in seen:
            continue
        seen.add(ident)
        planners.append(planner)
    return planners
