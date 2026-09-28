from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from Waam_tech.waamgen import generate_nc as waamgen_generate

ACTIVE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ACTIVE_ROOT / "manifest.json"
NOTES_PATH = ACTIVE_ROOT / "notes.md"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_manifest():
    if not MANIFEST_PATH.is_file():
        return {}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(payload) -> None:
    MANIFEST_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def baseline_output_path(context) -> Path:
    if context.baseline_nc_path:
        return Path(context.baseline_nc_path)
    return context.output_dir / "nc_files" / "waam_baseline.nc"


def experimental_output_path(context) -> Path:
    target = context.output_dir / "nc_files" / "waam_baseline_experimental.nc"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def read_baseline_text(context) -> str:
    baseline = baseline_output_path(context)
    if not baseline.is_file():
        return ""
    return baseline.read_text(encoding="utf-8")


def read_baseline_lines(context):
    text = read_baseline_text(context)
    if not text:
        return []
    return text.splitlines()


def write_experimental_text(context, text: str) -> Path:
    target = experimental_output_path(context)
    target.write_text(text, encoding="utf-8")
    return target


def write_experimental_lines(context, lines) -> Path:
    text = "\n".join(lines)
    if lines:
        text += "\n"
    return write_experimental_text(context, text)


def append_iteration_note(context, *, heading: str, lines) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Active Experiment Notes\n\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} ({_timestamp()})\n")
        for line in lines:
            handle.write(f"{line}\n")
        handle.write("\n")


def default_export_nc(context, *, max_iters: int = 1, geom_tol: float = 0.8) -> Path:
    target = experimental_output_path(context)
    waamgen_generate(
        context.plan_path,
        output_path=target,
        max_iters=max_iters,
        geom_tol=geom_tol,
        plot=False,
    )
    return target


def copy_baseline_to_experimental(context) -> Path:
    baseline = baseline_output_path(context)
    target = experimental_output_path(context)
    if baseline.is_file():
        shutil.copyfile(baseline, target)
        return target
    return default_export_nc(context)
