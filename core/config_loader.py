from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .paths import get_workspace_root


_DEFAULT_CONFIG_NAME = "standard_waam.json"
class ConfigError(ValueError):
    """Raised when required config values are missing or invalid."""


@dataclass(frozen=True)
class PostCommands:
    torch_on: str
    torch_off: str
    gas_on: str
    gas_off: str
    wire_feed: str
    travel_speed: str


@dataclass(frozen=True)
class DepositionParams:
    bead_width_mm: float
    layer_height_mm: float
    travel_speed_mm_min: float
    wire_feed_mm_min: float
    dwell_per_layer_s: float


@dataclass(frozen=True)
class MachineLimits:
    max_feed_xy: float
    max_wire_feed_mm_min: float


@dataclass(frozen=True)
class ValidatedConfig:
    raw: Dict[str, Any]
    units: str
    wcs: str
    post: PostCommands
    deposition: DepositionParams
    machine_limits: MachineLimits


def _default_config_path() -> Path:
    return get_workspace_root() / "config" / _DEFAULT_CONFIG_NAME


def _iter_config_candidates(user_path: Optional[str]) -> Iterable[Path]:
    if user_path:
        yield Path(user_path)

    for key in ("Waam_CONFIG_PATH", "WAAM_CONFIG_PATH"):
        env_path = os.environ.get(key)
        if env_path:
            yield Path(env_path)

    try:
        import FreeCAD as App  # type: ignore

        for getter in ("getUserAppDataDir", "getUserCachePath"):
            func = getattr(App, getter, None)
            if callable(func):
                try:
                    base = Path(func())
                except Exception:
                    continue
                if base:
                    yield base / "Mod" / "WAAM" / _DEFAULT_CONFIG_NAME
                    yield base / "WAAM" / _DEFAULT_CONFIG_NAME
    except Exception:
        pass

    yield Path.home() / ".FreeCAD" / "WAAM" / _DEFAULT_CONFIG_NAME
    yield _default_config_path()


def resolve_standard_config_path(path: Optional[str] = None) -> Path:
    """
    Locate the path to standard_waam.json following the default search order.
    """
    for candidate in _iter_config_candidates(path):
        try_path = Path(candidate).expanduser()
        if try_path.is_file():
            return try_path
    raise FileNotFoundError("WAAM config not found in any known location.")


def load_standard_config(path: Optional[str] = None) -> Dict[str, Any]:
    """
    Load the WAAM configuration from disk.

    Search order:
    1. Explicit `path` argument (if provided).
    2. Environment overrides (`Waam_CONFIG_PATH` / `WAAM_CONFIG_PATH`).
    3. User-specific FreeCAD config directories.
    4. Shipped default at `WAAM/config/standard_waam.json`.
    """
    cfg_path = resolve_standard_config_path(path)
    with cfg_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _require_str(mapping: Mapping[str, Any], key: str, *, label: str) -> str:
    val = mapping.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"{label} '{key}' is required and must be a non-empty string.")
    return val.strip()


def _require_float(mapping: Mapping[str, Any], key: str, *, label: str, min_value: float = 0.0) -> float:
    val = mapping.get(key)
    try:
        num = float(val)
    except Exception:
        raise ConfigError(f"{label} '{key}' must be a number.")
    if num <= min_value:
        raise ConfigError(f"{label} '{key}' must be greater than {min_value}.")
    return num


def validate_post_config(post: Mapping[str, Any]) -> PostCommands:
    """Validate required post commands for torch/gas/wire control."""
    if not isinstance(post, Mapping):
        raise ConfigError("Post block must be a mapping.")
    return PostCommands(
        torch_on=_require_str(post, "torch_on", label="post"),
        torch_off=_require_str(post, "torch_off", label="post"),
        gas_on=_require_str(post, "gas_on", label="post"),
        gas_off=_require_str(post, "gas_off", label="post"),
        wire_feed=_require_str(post, "wire_feed", label="post"),
        travel_speed=_require_str(post, "travel_speed", label="post"),
    )


def validate_deposition_params(proc: Mapping[str, Any]) -> DepositionParams:
    """Validate deposition process parameters needed for NC generation."""
    if not isinstance(proc, Mapping):
        raise ConfigError("Process deposition block must be a mapping.")
    return DepositionParams(
        bead_width_mm=_require_float(proc, "bead_width_mm", label="deposition", min_value=0.0),
        layer_height_mm=_require_float(proc, "layer_height_mm", label="deposition", min_value=0.0),
        travel_speed_mm_min=_require_float(proc, "travel_speed_mm_min", label="deposition", min_value=0.0),
        wire_feed_mm_min=_require_float(proc, "wire_feed_mm_min", label="deposition", min_value=0.0),
        dwell_per_layer_s=_require_float(proc, "dwell_per_layer_s", label="deposition", min_value=0.0),
    )


def _validate_machine_limits(cfg: Mapping[str, Any]) -> MachineLimits:
    machine = cfg.get("machine") or {}
    return MachineLimits(
        max_feed_xy=_require_float(machine, "max_feed_xy", label="machine", min_value=0.0),
        max_wire_feed_mm_min=_require_float(machine, "max_wire_feed_mm_min", label="machine", min_value=0.0),
    )


def load_validated_config(path: Optional[str] = None) -> ValidatedConfig:
    """Load standard config and enforce required units, WCS, post, and process ranges."""
    cfg = load_standard_config(path)
    units = _require_str(cfg, "units", label="config")
    wcs = _require_str(cfg, "wcs", label="config")

    post_block = cfg.get("post") or cfg.get("machine", {}).get("post") or cfg.get("torch") or {}
    post = validate_post_config(post_block)

    proc = (cfg.get("process") or {}).get("deposition") or {}
    deposition = validate_deposition_params(proc)

    machine_limits = _validate_machine_limits(cfg)

    return ValidatedConfig(
        raw=cfg,
        units=units,
        wcs=wcs,
        post=post,
        deposition=deposition,
        machine_limits=machine_limits,
    )
