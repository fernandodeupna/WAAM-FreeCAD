from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency for headless runs
    np = None  # type: ignore[assignment]

from ..core.config_loader import load_standard_config


_WORD_RE = re.compile(r"([A-Z])([-+]?[0-9]*\.?[0-9]+)")
_PAREN_COMMENT_RE = re.compile(r"\(.*?\)")

DEFAULT_TORCH_ON_CODES = frozenset({"M3", "M62", "M70"})
DEFAULT_TORCH_OFF_CODES = frozenset({"M5", "M63", "M71", "M30"})


def _normalise_code_set(codes: Optional[Iterable[str]], fallback: Iterable[str]) -> frozenset[str]:
    if not codes:
        return frozenset(code.upper() for code in fallback)
    if isinstance(codes, str):
        values = [codes]
    else:
        values = codes
    cleaned = {str(code).strip().upper() for code in values if str(code).strip()}
    if cleaned:
        return frozenset(cleaned)
    return frozenset(code.upper() for code in fallback)


@lru_cache(maxsize=1)
def _config_torch_codes() -> Tuple[Optional[Iterable[str]], Optional[Iterable[str]]]:
    try:
        cfg = load_standard_config()
    except Exception:
        return None, None
    viewer_cfg = cfg.get("viewer", {})
    return viewer_cfg.get("torch_on_codes"), viewer_cfg.get("torch_off_codes")


def resolve_torch_codes(
    torch_on_codes: Optional[Iterable[str]] = None,
    torch_off_codes: Optional[Iterable[str]] = None,
    *,
    allow_config: bool = True,
) -> Tuple[frozenset[str], frozenset[str]]:
    config_on: Optional[Iterable[str]] = None
    config_off: Optional[Iterable[str]] = None
    if allow_config and (torch_on_codes is None or torch_off_codes is None):
        config_on, config_off = _config_torch_codes()

    effective_on = torch_on_codes if torch_on_codes is not None else config_on
    effective_off = torch_off_codes if torch_off_codes is not None else config_off

    return (
        _normalise_code_set(effective_on, DEFAULT_TORCH_ON_CODES),
        _normalise_code_set(effective_off, DEFAULT_TORCH_OFF_CODES),
    )


def _normalise_code(letter: str, value: str) -> str:
    try:
        numeric = int(round(float(value)))
    except ValueError:
        return f"{letter}{value}"
    return f"{letter}{numeric}"


def clean_nc_line(line: str) -> str:
    """Strip comments/whitespace and uppercase a raw NC line."""
    line = _PAREN_COMMENT_RE.sub("", line)
    if ";" in line:
        line = line.split(";", 1)[0]
    return line.strip().upper()


def iter_clean_lines(text: str) -> Iterable[str]:
    for raw_line in text.splitlines():
        cleaned = clean_nc_line(raw_line)
        if cleaned:
            yield cleaned


def _parse_nc_iter(
    lines: Iterable[str],
    *,
    torch_on_codes: Optional[Iterable[str]] = None,
    torch_off_codes: Optional[Iterable[str]] = None,
    allow_config: bool = True,
) -> Tuple[Sequence[Sequence[float]], Sequence[bool]]:
    on_codes, off_codes = resolve_torch_codes(torch_on_codes, torch_off_codes, allow_config=allow_config)

    current = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    points: List[Tuple[float, float, float]] = [(
        current["X"],
        current["Y"],
        current["Z"],
    )]
    deposition_segments: List[bool] = []

    torch_on = False
    torch_codes_seen = False

    for raw_line in lines:
        line = clean_nc_line(raw_line)
        if not line:
            continue

        words = list(_WORD_RE.findall(line))
        if not words:
            continue

        g_codes = []
        m_codes = []
        coord_updates = {}

        for letter, value in words:
            if letter == "G":
                g_codes.append(_normalise_code(letter, value))
            elif letter == "M":
                code = _normalise_code(letter, value)
                m_codes.append(code)
            elif letter in ("X", "Y", "Z"):
                try:
                    coord_updates[letter] = float(value)
                except ValueError:
                    continue

        if m_codes:
            if any(code in on_codes for code in m_codes):
                torch_on = True
                torch_codes_seen = True
            if any(code in off_codes for code in m_codes):
                torch_on = False
                torch_codes_seen = True

        if not coord_updates:
            continue

        for axis, value in coord_updates.items():
            current[axis] = value

        next_point = (
            current["X"],
            current["Y"],
            current["Z"],
        )

        if next_point == points[-1]:
            continue

        rapid_move = any(code in {"G0"} for code in g_codes)
        linear_move = any(code in {"G1"} for code in g_codes)
        is_deposition = False

        if rapid_move:
            is_deposition = False
        elif linear_move:
            if torch_codes_seen:
                is_deposition = torch_on
            else:
                is_deposition = torch_on or not m_codes
        else:
            is_deposition = torch_on

        points.append(next_point)
        deposition_segments.append(is_deposition)

    if len(points) < 2:
        raise ValueError("No valid tool moves detected in NC file.")

    if np is None:
        return points, deposition_segments
    return np.array(points, dtype=float), np.array(deposition_segments, dtype=bool)


def parse_nc_text(
    nc_text: str,
    *,
    torch_on_codes: Optional[Iterable[str]] = None,
    torch_off_codes: Optional[Iterable[str]] = None,
    allow_config: bool = True,
) -> Tuple[Sequence[Sequence[float]], Sequence[bool]]:
    return _parse_nc_iter(
        nc_text.splitlines(),
        torch_on_codes=torch_on_codes,
        torch_off_codes=torch_off_codes,
        allow_config=allow_config,
    )


def parse_nc_file(
    nc_path: Path | str,
    *,
    torch_on_codes: Optional[Iterable[str]] = None,
    torch_off_codes: Optional[Iterable[str]] = None,
    allow_config: bool = True,
) -> Tuple[Sequence[Sequence[float]], Sequence[bool]]:
    path = Path(nc_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        return _parse_nc_iter(
            handle,
            torch_on_codes=torch_on_codes,
            torch_off_codes=torch_off_codes,
            allow_config=allow_config,
        )


def split_moves(
    points: Sequence[Sequence[float]],
    deposition_segments: Sequence[bool],
) -> Tuple[List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]], List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]]:
    travel: List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = []
    deposition: List[Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = []
    for idx, is_deposition in enumerate(deposition_segments):
        start = tuple(float(v) for v in points[idx])
        end = tuple(float(v) for v in points[idx + 1])
        if is_deposition:
            deposition.append((start, end))
        else:
            travel.append((start, end))
    return travel, deposition
