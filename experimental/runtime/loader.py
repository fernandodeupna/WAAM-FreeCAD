from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..base import ExperimentalPlannerContext
from ..layer_by_layer_scaffold import write_scaffold_bundle


_ACTIVE_RUNTIME_ROOT_REL = "runtime/active"
_ACTIVE_PLANNER_REL = f"{_ACTIVE_RUNTIME_ROOT_REL}/planner.py"
_ACTIVE_HELPERS_REL = f"{_ACTIVE_RUNTIME_ROOT_REL}/helpers.py"
_ACTIVE_MANIFEST_REL = f"{_ACTIVE_RUNTIME_ROOT_REL}/manifest.json"
_ACTIVE_NOTES_REL = f"{_ACTIVE_RUNTIME_ROOT_REL}/notes.md"
_ACTIVE_PROMPT_CONTEXT_REL = f"{_ACTIVE_RUNTIME_ROOT_REL}/prompt_context.json"
_RUNTIME_FILE_KEYS = ("planner", "helpers", "manifest", "notes", "prompt_context")
_EDITABLE_RUNTIME_FILE_KEYS = ("planner", "helpers", "notes")
_DEFAULT_GOAL = "Create the next part-specific experimental improvement from the selected per-part runtime workspace."
_HOLE_FEATURE_TYPES = {"hole", "through_hole", "cylindrical_hole"}
_POCKET_FEATURE_TYPES = {"planar_pocket_floor"}
_FREEFORM_FEATURE_TYPES = {"freeform_area"}


def _experimental_root(experimental_root: Path | str | None = None) -> Path:
    if experimental_root is not None:
        return Path(experimental_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _paths(experimental_root: Path | str | None = None) -> dict[str, Path]:
    root = _experimental_root(experimental_root)
    runtime_root = root / "runtime"
    active_root = runtime_root / "active"
    return {
        "experimental_root": root,
        "runtime_root": runtime_root,
        "active_root": active_root,
        "parts_root": runtime_root / "parts",
        "archive_root": runtime_root / "archive",
        "active_part": root / "ACTIVE_PART.json",
        "planner": active_root / "planner.py",
        "helpers": active_root / "helpers.py",
        "manifest": active_root / "manifest.json",
        "notes": active_root / "notes.md",
        "prompt_context": active_root / "prompt_context.json",
        "active_init": active_root / "__init__.py",
        "runtime_init": runtime_root / "__init__.py",
    }


def _read_json(path: Path, default: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _timestamp_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return _timestamp_slug().lower()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "current-part"


def _load_features(features_path: Path) -> dict[str, Any]:
    return _read_json(features_path, {})


def _features_signature(features_path: Path) -> str:
    if not features_path.is_file():
        return ""
    return hashlib.sha1(features_path.read_bytes()).hexdigest()[:12]


def _workspace_key_for_context(context: ExperimentalPlannerContext) -> str:
    signature = _features_signature(context.features_path)
    if signature:
        return signature
    if context.run_id:
        return _slugify(context.run_id)
    return "current"


def _workspace_relpath(part_key: str, workspace_key: str) -> Path:
    return Path("runtime") / "parts" / part_key / workspace_key


def _workspace_paths_from_rel(paths: Mapping[str, Path], workspace_rel: Path | str) -> dict[str, Path | str]:
    rel_path = Path(str(workspace_rel).strip())
    workspace_root = (paths["experimental_root"] / rel_path).resolve()
    return {
        "workspace_root": workspace_root,
        "workspace_rel": rel_path.as_posix(),
        "planner": workspace_root / "planner.py",
        "helpers": workspace_root / "helpers.py",
        "manifest": workspace_root / "manifest.json",
        "notes": workspace_root / "notes.md",
        "prompt_context": workspace_root / "prompt_context.json",
        "planner_rel": (rel_path / "planner.py").as_posix(),
        "helpers_rel": (rel_path / "helpers.py").as_posix(),
        "manifest_rel": (rel_path / "manifest.json").as_posix(),
        "notes_rel": (rel_path / "notes.md").as_posix(),
        "prompt_context_rel": (rel_path / "prompt_context.json").as_posix(),
    }


def _workspace_paths(paths: Mapping[str, Path], part_key: str, workspace_key: str) -> dict[str, Path | str]:
    return _workspace_paths_from_rel(paths, _workspace_relpath(part_key, workspace_key))


def _display_runtime_path(rel_path: str) -> str:
    return (Path("Waam_tech") / "experimental" / Path(rel_path)).as_posix()


def _allowed_files_for_workspace(workspace_paths: Mapping[str, Path | str]) -> list[str]:
    return [
        _display_runtime_path(str(workspace_paths["planner_rel"])),
        _display_runtime_path(str(workspace_paths["helpers_rel"])),
        _display_runtime_path(str(workspace_paths["notes_rel"])),
        _display_runtime_path(str(workspace_paths["manifest_rel"])),
        _display_runtime_path(str(workspace_paths["prompt_context_rel"])),
    ]


def _active_workspace_rel(active_state: Mapping[str, Any] | None) -> str:
    if not active_state:
        return ""
    workspace_rel = str(active_state.get("runtime_workspace_path") or "").strip()
    if workspace_rel:
        return workspace_rel
    part_key = _active_part_key(active_state)
    workspace_key = str(active_state.get("workspace_key") or "").strip()
    if part_key and workspace_key:
        return _workspace_relpath(part_key, workspace_key).as_posix()
    return ""


def _active_part_key(active_state: Mapping[str, Any] | None) -> str:
    if not active_state:
        return ""
    return str(active_state.get("active_part_key") or active_state.get("part_key") or "").strip()


def _copy_runtime_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _sync_active_edits_to_workspace(
    paths: Mapping[str, Path],
    active_state: Mapping[str, Any] | None,
) -> dict[str, Path | str] | None:
    workspace_rel = _active_workspace_rel(active_state)
    if not workspace_rel:
        return None
    workspace = _workspace_paths_from_rel(paths, workspace_rel)
    workspace_root = Path(workspace["workspace_root"]).resolve()
    if paths["parts_root"].resolve() not in workspace_root.parents:
        return None
    workspace_root.mkdir(parents=True, exist_ok=True)
    for key in _EDITABLE_RUNTIME_FILE_KEYS:
        source = paths[key]
        target = Path(workspace[key])
        if source.is_file():
            _copy_runtime_file(source, target)
    return workspace


def _mirror_workspace_to_active(
    paths: Mapping[str, Path],
    workspace_paths: Mapping[str, Path | str],
) -> None:
    paths["active_root"].mkdir(parents=True, exist_ok=True)
    for key in _RUNTIME_FILE_KEYS:
        source = Path(workspace_paths[key])
        target = paths[key]
        if source.is_file():
            _copy_runtime_file(source, target)


def _runtime_snapshot_sources(paths: Mapping[str, Path]) -> dict[str, Path]:
    return {key: paths[key] for key in _RUNTIME_FILE_KEYS}


def _archive_runtime_snapshot(
    paths: Mapping[str, Path],
    *,
    part_key: str,
    source_files: Mapping[str, Path | str],
    active_state: Mapping[str, Any] | None,
) -> Path | None:
    if not part_key:
        return None
    archive_dir = paths["archive_root"] / part_key / _timestamp_slug()
    archive_dir.mkdir(parents=True, exist_ok=True)
    for key in _RUNTIME_FILE_KEYS:
        source = source_files.get(key)
        if source is None:
            continue
        source_path = Path(source)
        if source_path.is_file():
            shutil.copy2(source_path, archive_dir / source_path.name)
    if active_state:
        _write_json(archive_dir / "ACTIVE_PART.json", active_state)
    return archive_dir


def _archive_workspace_snapshot(
    paths: Mapping[str, Path],
    workspace_paths: Mapping[str, Path | str],
    manifest: Mapping[str, Any],
    active_state: Mapping[str, Any] | None,
) -> Path | None:
    return _archive_runtime_snapshot(
        paths,
        part_key=str(manifest.get("part_key") or _active_part_key(active_state)).strip(),
        source_files=workspace_paths,
        active_state=active_state,
    )


def _archive_active_mirror_snapshot(
    paths: Mapping[str, Path],
    manifest: Mapping[str, Any],
    active_state: Mapping[str, Any] | None,
) -> Path | None:
    part_key = str(manifest.get("part_key") or _active_part_key(active_state)).strip()
    return _archive_runtime_snapshot(
        paths,
        part_key=part_key,
        source_files=_runtime_snapshot_sources(paths),
        active_state=active_state,
    )


def _archive_rel_display(paths: Mapping[str, Path], archive_dir: Path) -> str:
    rel_path = archive_dir.resolve().relative_to(paths["experimental_root"].resolve())
    return _display_runtime_path(rel_path.as_posix())


def _select_seed_archive(
    paths: Mapping[str, Path],
    *,
    part_key: str,
    features_signature: str,
) -> dict[str, Any]:
    part_archive_root = paths["archive_root"] / part_key
    if not part_archive_root.is_dir():
        return {
            "seed_source": "clean_template",
            "seed_archive_path": "",
        }

    exact_match: tuple[Path, dict[str, Any]] | None = None
    part_match: tuple[Path, dict[str, Any]] | None = None
    for manifest_path in sorted(part_archive_root.glob("*/manifest.json"), reverse=True):
        manifest = _read_json(manifest_path, {})
        archive_dir = manifest_path.parent
        if str(manifest.get("part_key") or "").strip() != part_key:
            continue
        if not (archive_dir / "planner.py").is_file() or not (archive_dir / "helpers.py").is_file():
            continue
        manifest_signature = str(manifest.get("features_signature") or "").strip()
        if features_signature and manifest_signature == features_signature:
            exact_match = (archive_dir, manifest)
            break
        if part_match is None:
            part_match = (archive_dir, manifest)

    if exact_match is not None:
        archive_dir, _manifest = exact_match
        return {
            "seed_source": "archive_exact_signature",
            "seed_archive_path": _archive_rel_display(paths, archive_dir),
            "archive_dir": archive_dir,
        }
    if part_match is not None:
        archive_dir, _manifest = part_match
        return {
            "seed_source": "archive_same_part",
            "seed_archive_path": _archive_rel_display(paths, archive_dir),
            "archive_dir": archive_dir,
        }
    return {
        "seed_source": "clean_template",
        "seed_archive_path": "",
    }


def _seed_workspace_runtime(
    workspace_paths: Mapping[str, Path | str],
    seed_choice: Mapping[str, Any],
) -> None:
    archive_dir = seed_choice.get("archive_dir")
    if isinstance(archive_dir, Path):
        planner_src = archive_dir / "planner.py"
        helpers_src = archive_dir / "helpers.py"
        if planner_src.is_file():
            _copy_runtime_file(planner_src, Path(workspace_paths["planner"]))
        else:
            Path(workspace_paths["planner"]).write_text(_default_planner_source(), encoding="utf-8")
        if helpers_src.is_file():
            _copy_runtime_file(helpers_src, Path(workspace_paths["helpers"]))
        else:
            Path(workspace_paths["helpers"]).write_text(_default_helpers_source(), encoding="utf-8")
        return

    Path(workspace_paths["planner"]).write_text(_default_planner_source(), encoding="utf-8")
    Path(workspace_paths["helpers"]).write_text(_default_helpers_source(), encoding="utf-8")


def _part_label_from_active_state(active_state: Mapping[str, Any] | None) -> str:
    if not active_state:
        return ""
    for key in ("active_part_label", "part_label", "active_part"):
        value = active_state.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value).stem.strip()
    return ""


def infer_part_family_from_features_doc(features: Mapping[str, Any]) -> tuple[str, list[str]]:
    feat_list = list(features.get("features") or [])
    metadata = features.get("metadata") or {}
    mesh_analysis = metadata.get("mesh_analysis") or {}
    thin_wall_samples = list((mesh_analysis.get("thin_walls") or {}).get("samples") or [])
    hole_count = 0
    pocket_count = 0
    freeform_count = 0
    thin_wall_risk = False
    through_hole_count = 0
    for feat in feat_list:
        if not isinstance(feat, Mapping):
            continue
        ftype = str(feat.get("type") or "").strip().lower()
        if ftype in _HOLE_FEATURE_TYPES:
            hole_count += 1
            if ftype == "through_hole":
                through_hole_count += 1
        elif ftype in _POCKET_FEATURE_TYPES:
            pocket_count += 1
        elif ftype in _FREEFORM_FEATURE_TYPES:
            freeform_count += 1
        risk_tags = feat.get("risk_tags") or []
        if isinstance(risk_tags, list) and "thin_wall_neighbor" in risk_tags:
            thin_wall_risk = True

    thin_wall_signal = bool(thin_wall_samples) or thin_wall_risk
    void_feature_count = hole_count + pocket_count

    signals: list[str] = []
    if thin_wall_signal:
        signals.append("thin_wall_signal")
    if hole_count:
        signals.append("hole_features")
    if through_hole_count:
        signals.append("through_holes")
    if pocket_count:
        signals.append("pocket_features")
    if freeform_count:
        signals.append("freeform_regions")

    if thin_wall_signal and void_feature_count:
        return "hybrid_mixed", signals
    if thin_wall_signal:
        return "thin_wall_frame", signals
    if hole_count >= 3 and pocket_count == 0 and freeform_count == 0:
        return "multi_hole_plate", signals
    if void_feature_count:
        return "topology_transition_void_protection", signals
    if freeform_count:
        return "hybrid_mixed", signals
    return "bulk_fill", signals


def infer_part_family_from_features_path(features_path: Path) -> tuple[str, list[str]]:
    return infer_part_family_from_features_doc(_load_features(features_path))


def derive_part_identity_from_features_path(
    features_path: Path,
    *,
    step_path: Path | None = None,
    active_state: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    features = _load_features(features_path)
    metadata = features.get("metadata") or {}
    work_obj = metadata.get("work_obj")
    label = ""
    if isinstance(work_obj, str) and work_obj.strip():
        label = Path(work_obj).stem.strip()
    if not label and step_path is not None:
        label = step_path.stem.strip()
    if not label and active_state:
        label = _part_label_from_active_state(active_state)
    if not label:
        label = "Current Part"
    return label, _slugify(label)


def derive_part_profile_from_features_path(
    features_path: Path,
    *,
    step_path: Path | None = None,
    active_state: Mapping[str, Any] | None = None,
) -> tuple[str, str, str, list[str]]:
    label, key = derive_part_identity_from_features_path(
        features_path,
        step_path=step_path,
        active_state=active_state,
    )
    family, signals = infer_part_family_from_features_path(features_path)
    return label, key, family, signals


def load_active_part_state(*, experimental_root: Path | str | None = None) -> dict[str, Any]:
    return _read_json(_paths(experimental_root)["active_part"], {})


def load_active_manifest(*, experimental_root: Path | str | None = None) -> dict[str, Any]:
    return _read_json(_paths(experimental_root)["manifest"], {})


def _ensure_runtime_layout(paths: Mapping[str, Path]) -> None:
    paths["active_root"].mkdir(parents=True, exist_ok=True)
    paths["parts_root"].mkdir(parents=True, exist_ok=True)
    paths["archive_root"].mkdir(parents=True, exist_ok=True)
    for key in ("runtime_init", "active_init"):
        if not paths[key].exists():
            paths[key].write_text("", encoding="utf-8")


def _active_goal(
    active_state: Mapping[str, Any],
    current_manifest: Mapping[str, Any],
    *,
    part_key: str,
) -> str:
    goal = current_manifest.get("goal")
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    active_part_key = str(active_state.get("active_part_key") or active_state.get("part_key") or "").strip()
    if active_part_key == part_key:
        for key in ("goal", "notes"):
            note = active_state.get(key)
            if isinstance(note, str) and note.strip():
                return note.strip()
    return _DEFAULT_GOAL


def _expected_experimental_nc(context: ExperimentalPlannerContext) -> Path:
    return context.output_dir / "nc_files" / "waam_baseline_experimental.nc"


def _default_planner_source() -> str:
    return """from __future__ import annotations

from .helpers import append_iteration_note, copy_baseline_to_experimental, load_manifest


class RuntimeActivePlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"

    def export_nc(self, context):
        manifest = load_manifest()
        goal = manifest.get("goal") or "No experimental goal recorded."
        append_iteration_note(
            context,
            heading="Scaffold export",
            lines=[
                f"- part: {context.part_key or manifest.get('part_key') or 'current-part'}",
                f"- goal: {goal}",
                "- change: copied the current waam_baseline.nc into waam_baseline_experimental.nc",
                "- expected improvement: provide a safe editable starting point for the next iteration",
            ],
        )
        return copy_baseline_to_experimental(context)
"""


def _default_helpers_source() -> str:
    return """from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from Waam_tech.waamgen import generate_nc as waamgen_generate

ACTIVE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ACTIVE_ROOT / "manifest.json"
NOTES_PATH = ACTIVE_ROOT / "notes.md"

Segment1D = Tuple[float, float]


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
    text = "\\n".join(lines)
    if lines:
        text += "\\n"
    return write_experimental_text(context, text)


def append_iteration_note(context, *, heading: str, lines) -> None:
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not NOTES_PATH.exists():
        NOTES_PATH.write_text("# Active Experiment Notes\\n\\n", encoding="utf-8")
    with NOTES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"## {heading} ({_timestamp()})\\n")
        for line in lines:
            handle.write(f"{line}\\n")
        handle.write("\\n")


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


def loop_y_segments_at_x(loop: Sequence[Sequence[float]], x_value: float) -> List[Segment1D]:
    pts = [(float(p[0]), float(p[1])) for p in loop if len(p) >= 2]
    if len(pts) < 3:
        return []
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    hits: List[float] = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if abs(x2 - x1) < 1e-9:
            continue
        if (x1 <= x_value < x2) or (x2 <= x_value < x1):
            t = (x_value - x1) / (x2 - x1)
            hits.append(y1 + t * (y2 - y1))
    hits.sort()
    return [(hits[i], hits[i + 1]) for i in range(0, len(hits) - 1, 2)]


def subtract_segments(
    base_segments: Iterable[Segment1D],
    cut_segments: Iterable[Segment1D],
    *,
    min_len: float = 0.25,
) -> List[Segment1D]:
    pieces = [(float(a), float(b)) for a, b in base_segments if float(b) - float(a) > min_len]
    for cut_a, cut_b in cut_segments:
        next_pieces: List[Segment1D] = []
        for seg_a, seg_b in pieces:
            lo = max(seg_a, float(cut_a))
            hi = min(seg_b, float(cut_b))
            if hi <= lo:
                next_pieces.append((seg_a, seg_b))
                continue
            if seg_a < lo and lo - seg_a > min_len:
                next_pieces.append((seg_a, lo))
            if hi < seg_b and seg_b - hi > min_len:
                next_pieces.append((hi, seg_b))
        pieces = next_pieces
    return pieces


def copy_baseline_to_experimental(context) -> Path:
    baseline = baseline_output_path(context)
    target = experimental_output_path(context)
    if baseline.is_file():
        shutil.copyfile(baseline, target)
        return target
    return default_export_nc(context)
"""


def _default_notes_text(
    part_label: str,
    goal: str,
    *,
    planner_path: str,
    helpers_path: str,
    seed_source: str = "clean_template",
    seed_archive_path: str = "",
) -> str:
    text = (
        f"# Active Experiment Notes: {part_label}\n\n"
        "## Goal\n"
        f"{goal}\n\n"
        "## Contract\n"
        "- Edit only `Waam_tech/experimental/`.\n"
        "- Keep `waam_baseline.nc` as the control output.\n"
        "- Write candidate changes into `waam_baseline_experimental.nc`.\n\n"
        "## Current Hypothesis\n"
        "- Start from the baseline NC copy and add only part-specific experimental logic.\n"
        f"- Keep changes local to `{planner_path}` and `{helpers_path}`.\n"
    )
    if seed_source == "archive_exact_signature":
        text += (
            "\n## Seed\n"
            "- source: latest archived run with the same features signature\n"
            f"- archive: `{seed_archive_path}`\n"
        )
    elif seed_source == "archive_same_part":
        text += (
            "\n## Seed\n"
            "- source: latest archived run for the same part key\n"
            f"- archive: `{seed_archive_path}`\n"
        )
    else:
        text += (
            "\n## Seed\n"
            "- source: clean runtime template\n"
        )
    return text


def _build_manifest(
    context: ExperimentalPlannerContext,
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    part_family_signals: list[str],
    goal: str,
    workspace_key: str,
    workspace_paths: Mapping[str, Path | str],
    seed_source: str,
    seed_archive_path: str,
    iteration: int,
    current_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    experimental_nc = _expected_experimental_nc(context)
    created_at = current_manifest.get("created_at") or _timestamp_iso()
    planner_display = _display_runtime_path(str(workspace_paths["planner_rel"]))
    return {
        "part_key": part_key,
        "part_label": part_label,
        "part_family": part_family,
        "part_family_signals": list(part_family_signals),
        "status": str(current_manifest.get("status") or "iterating"),
        "iteration": int(current_manifest.get("iteration") or iteration),
        "workspace_key": workspace_key,
        "runtime_workspace_path": str(workspace_paths["workspace_rel"]),
        "planner_basis": "layer-by-layer material deposition",
        "active_planner": _ACTIVE_PLANNER_REL,
        "active_planner_mirror": _ACTIVE_PLANNER_REL,
        "runtime_planner_path": str(workspace_paths["planner_rel"]),
        "planner_module": planner_display,
        "source_step": str(context.step_path) if context.step_path else "",
        "features_path": str(context.features_path),
        "features_signature": _features_signature(context.features_path),
        "plan_path": str(context.plan_path),
        "baseline_nc": str(context.baseline_nc_path) if context.baseline_nc_path else "",
        "experimental_nc": str(experimental_nc),
        "allowed_files": _allowed_files_for_workspace(workspace_paths),
        "goal": goal,
        "seed_source": str(current_manifest.get("seed_source") or seed_source or "clean_template"),
        "seed_archive_path": str(current_manifest.get("seed_archive_path") or seed_archive_path or ""),
        "last_review_input": str(current_manifest.get("last_review_input") or ""),
        "run_id": context.run_id or new_run_id(),
        "created_at": created_at,
        "updated_at": _timestamp_iso(),
    }


def _build_prompt_context(
    context: ExperimentalPlannerContext,
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    manifest: Mapping[str, Any],
    active_state: Mapping[str, Any],
    paths: Mapping[str, Path],
    workspace_paths: Mapping[str, Path | str],
    knowledge_bundle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    planner_display = _display_runtime_path(str(workspace_paths["planner_rel"]))
    helpers_display = _display_runtime_path(str(workspace_paths["helpers_rel"]))
    current_lesson = str((knowledge_bundle or {}).get("lesson_display") or "")
    current_context = str((knowledge_bundle or {}).get("context_display") or "")
    promoted_lessons = list((knowledge_bundle or {}).get("promoted_lessons") or [])
    related_lessons = list((knowledge_bundle or {}).get("related_lessons") or [])
    recommended_reads: list[str] = []
    for item in [
        current_lesson,
        current_context,
        *promoted_lessons,
        *related_lessons,
        planner_display,
        helpers_display,
        "Waam_tech/experimental/ACTIVE_PART.json",
    ]:
        if item and item not in recommended_reads:
            recommended_reads.append(item)
    return {
        "active_part": active_state,
        "part_label": part_label,
        "part_key": part_key,
        "part_family": part_family,
        "part_family_signals": list(manifest.get("part_family_signals") or []),
        "planner_basis": "layer-by-layer material deposition",
        "allowed_edit_root": _display_runtime_path(str(workspace_paths["workspace_rel"])) + "/",
        "allowed_files": _allowed_files_for_workspace(workspace_paths),
        "artifacts": {
            "step_path": str(context.step_path) if context.step_path else "",
            "features_path": str(context.features_path),
            "plan_path": str(context.plan_path),
            "baseline_nc_path": str(context.baseline_nc_path) if context.baseline_nc_path else "",
            "experimental_nc_path": str(_expected_experimental_nc(context)),
        },
        "workspace_runtime_files": {
            "workspace_root": str(workspace_paths["workspace_root"]),
            "planner": str(workspace_paths["planner"]),
            "helpers": str(workspace_paths["helpers"]),
            "manifest": str(workspace_paths["manifest"]),
            "notes": str(workspace_paths["notes"]),
        },
        "active_runtime_mirror_files": {
            "planner": str(paths["planner"]),
            "helpers": str(paths["helpers"]),
            "manifest": str(paths["manifest"]),
            "notes": str(paths["notes"]),
            "prompt_context": str(paths["prompt_context"]),
        },
        "knowledge_lessons": {
            "current_lesson": current_lesson,
            "current_context": current_context,
            "promoted_lessons": promoted_lessons,
            "relevant_archived_lessons": related_lessons,
        },
        "recommended_reads": recommended_reads,
        "manifest": manifest,
        "next_goal": f"Edit `{planner_display}` and `{helpers_display}` for this part-specific iteration, then rerun FreeCAD.",
    }


def _sync_active_part(
    paths: Mapping[str, Path],
    *,
    part_label: str,
    part_key: str,
    part_family: str,
    part_family_signals: list[str],
    workspace_key: str,
    workspace_paths: Mapping[str, Path | str],
    goal: str,
    current_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    active_payload = {
        "active_part_label": part_label,
        "active_part_key": part_key,
        "active_part_family": part_family,
        "active_part_family_signals": list(part_family_signals),
        "workspace_key": workspace_key,
        "runtime_workspace_path": str(workspace_paths["workspace_rel"]),
        "runtime_planner_path": str(workspace_paths["planner_rel"]),
        "active_planner_mirror": _ACTIVE_PLANNER_REL,
        "status": str(current_manifest.get("status") or "iterating"),
        "goal": goal,
    }
    _write_json(paths["active_part"], active_payload)
    return active_payload


def ensure_active_runtime(
    context: ExperimentalPlannerContext,
    *,
    experimental_root: Path | str | None = None,
) -> dict[str, Any]:
    paths = _paths(experimental_root)
    _ensure_runtime_layout(paths)
    active_state = load_active_part_state(experimental_root=experimental_root)
    active_workspace_rel = _active_workspace_rel(active_state)
    active_manifest = load_active_manifest(experimental_root=experimental_root)
    if active_workspace_rel:
        _sync_active_edits_to_workspace(paths, active_state)
    part_label, part_key, part_family, part_family_signals = derive_part_profile_from_features_path(
        context.features_path,
        step_path=context.step_path,
        active_state=active_state,
    )
    if not context.part_key:
        context.part_key = part_key
    if not context.part_label:
        context.part_label = part_label
    if not context.part_family:
        context.part_family = part_family
    if context.part_family_signals is None:
        context.part_family_signals = list(part_family_signals)

    features_signature = _features_signature(context.features_path)
    workspace_key = _workspace_key_for_context(context)
    workspace_paths = _workspace_paths(paths, part_key, workspace_key)
    target_workspace_rel = str(workspace_paths["workspace_rel"])

    if active_workspace_rel and active_workspace_rel != target_workspace_rel:
        active_workspace_paths = _workspace_paths_from_rel(paths, active_workspace_rel)
        active_workspace_manifest = _read_json(Path(active_workspace_paths["manifest"]), active_manifest)
        _archive_workspace_snapshot(
            paths,
            active_workspace_paths,
            active_workspace_manifest,
            active_state,
        )
    elif not active_workspace_rel and any(paths[key].is_file() for key in _RUNTIME_FILE_KEYS):
        _archive_active_mirror_snapshot(paths, active_manifest, active_state)

    Path(workspace_paths["workspace_root"]).mkdir(parents=True, exist_ok=True)
    current_manifest = _read_json(Path(workspace_paths["manifest"]), {})
    goal = _active_goal(active_state, current_manifest, part_key=part_key)
    seed_source = str(current_manifest.get("seed_source") or "")
    seed_archive_path = str(current_manifest.get("seed_archive_path") or "")

    if not current_manifest:
        seed_choice = _select_seed_archive(
            paths,
            part_key=part_key,
            features_signature=features_signature,
        )
        seed_source = str(seed_choice.get("seed_source") or "clean_template")
        seed_archive_path = str(seed_choice.get("seed_archive_path") or "")
        _seed_workspace_runtime(workspace_paths, seed_choice)
    else:
        if not Path(workspace_paths["planner"]).is_file():
            Path(workspace_paths["planner"]).write_text(_default_planner_source(), encoding="utf-8")
        if not Path(workspace_paths["helpers"]).is_file():
            Path(workspace_paths["helpers"]).write_text(_default_helpers_source(), encoding="utf-8")

    if not Path(workspace_paths["notes"]).exists():
        Path(workspace_paths["notes"]).write_text(
            _default_notes_text(
                part_label,
                goal,
                planner_path=_display_runtime_path(str(workspace_paths["planner_rel"])),
                helpers_path=_display_runtime_path(str(workspace_paths["helpers_rel"])),
                seed_source=seed_source or "clean_template",
                seed_archive_path=seed_archive_path,
            ),
            encoding="utf-8",
        )

    current_manifest = _read_json(Path(workspace_paths["manifest"]), current_manifest)

    manifest = _build_manifest(
        context,
        part_label=part_label,
        part_key=part_key,
        part_family=part_family,
        part_family_signals=part_family_signals,
        goal=goal,
        workspace_key=workspace_key,
        workspace_paths=workspace_paths,
        seed_source=seed_source or "clean_template",
        seed_archive_path=seed_archive_path,
        iteration=1,
        current_manifest=current_manifest,
    )
    active_state = _sync_active_part(
        paths,
        part_label=part_label,
        part_key=part_key,
        part_family=part_family,
        part_family_signals=part_family_signals,
        workspace_key=workspace_key,
        workspace_paths=workspace_paths,
        goal=goal,
        current_manifest=manifest,
    )
    _write_json(Path(workspace_paths["manifest"]), manifest)
    knowledge_bundle = write_scaffold_bundle(
        context,
        _expected_experimental_nc(context),
        planner_key="runtime_active",
        planner_module=_display_runtime_path(str(workspace_paths["planner_rel"])),
        recommended_reads=[
            _display_runtime_path(str(workspace_paths["planner_rel"])),
            _display_runtime_path(str(workspace_paths["helpers_rel"])),
        ],
        experimental_root=paths["experimental_root"],
        manifest=manifest,
        next_goal=(
            f"Edit `{_display_runtime_path(str(workspace_paths['planner_rel']))}` and "
            f"`{_display_runtime_path(str(workspace_paths['helpers_rel']))}`, then rerun the experimental NC button."
        ),
    )
    _write_json(
        Path(workspace_paths["prompt_context"]),
        _build_prompt_context(
            context,
            part_label=part_label,
            part_key=part_key,
            part_family=part_family,
            manifest=manifest,
            active_state=active_state,
            paths=paths,
            workspace_paths=workspace_paths,
            knowledge_bundle=knowledge_bundle,
        ),
    )
    _mirror_workspace_to_active(paths, workspace_paths)
    return manifest


def _load_module_from_path(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load experimental runtime module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_active_runtime_planner(
    context: ExperimentalPlannerContext,
    *,
    experimental_root: Path | str | None = None,
):
    paths = _paths(experimental_root)
    active_state = load_active_part_state(experimental_root=experimental_root)
    workspace_rel = _active_workspace_rel(active_state)
    if not workspace_rel:
        active_manifest = load_active_manifest(experimental_root=experimental_root)
        workspace_rel = str(active_manifest.get("runtime_workspace_path") or "").strip()
    if not workspace_rel:
        raise RuntimeError(
            "Experimental planner selection was blocked because no active runtime workspace was recorded."
        )
    workspace_paths = _workspace_paths_from_rel(paths, workspace_rel)
    manifest = _read_json(Path(workspace_paths["manifest"]), {})
    planner_rel = str(manifest.get("runtime_planner_path") or workspace_paths["planner_rel"])
    planner_path = (paths["experimental_root"] / planner_rel).resolve()
    parts_root = paths["parts_root"].resolve()
    if parts_root not in planner_path.parents:
        raise RuntimeError(
            "Experimental planner selection was blocked because the active planner is outside "
            "Waam_tech/experimental/runtime/parts/."
        )
    if manifest.get("part_key") != context.part_key:
        raise RuntimeError(
            f"Experimental planner mismatch: active runtime planner is for '{manifest.get('part_key')}', "
            f"but the current part is '{context.part_key}'."
        )
    package_name = f"_waam_runtime_workspace_{hash(str(workspace_paths['workspace_root'])) & 0xFFFFFFFF:x}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(workspace_paths["workspace_root"])]
    sys.modules[package_name] = package
    sys.modules.pop(f"{package_name}.helpers", None)
    sys.modules.pop(f"{package_name}.planner", None)
    _load_module_from_path(f"{package_name}.helpers", Path(workspace_paths["helpers"]))
    module = _load_module_from_path(f"{package_name}.planner", Path(workspace_paths["planner"]))
    if hasattr(module, "build_planner"):
        return module.build_planner()
    planner_cls = getattr(module, "RuntimeActivePlanner", None)
    if planner_cls is not None:
        return planner_cls()
    planner = getattr(module, "planner", None)
    if planner is not None:
        return planner
    raise RuntimeError("Runtime active planner.py must define RuntimeActivePlanner, planner, or build_planner().")


def finalize_runtime_export(
    context: ExperimentalPlannerContext,
    target: Path,
    *,
    experimental_root: Path | str | None = None,
) -> dict[str, Any]:
    paths = _paths(experimental_root)
    active_state = load_active_part_state(experimental_root=experimental_root)
    workspace_rel = _active_workspace_rel(active_state)
    if not workspace_rel:
        workspace_rel = str(load_active_manifest(experimental_root=experimental_root).get("runtime_workspace_path") or "").strip()
    if not workspace_rel:
        raise RuntimeError("Unable to finalize the experimental export because no runtime workspace was recorded.")
    workspace_paths = _workspace_paths_from_rel(paths, workspace_rel)
    manifest = _read_json(Path(workspace_paths["manifest"]), {})
    manifest["experimental_nc"] = str(target)
    manifest["last_exported_at"] = _timestamp_iso()
    manifest["run_id"] = context.run_id or manifest.get("run_id") or new_run_id()
    _write_json(Path(workspace_paths["manifest"]), manifest)
    knowledge_bundle = write_scaffold_bundle(
        context,
        target,
        planner_key="runtime_active",
        planner_module=_display_runtime_path(str(workspace_paths["planner_rel"])),
        recommended_reads=[
            _display_runtime_path(str(workspace_paths["planner_rel"])),
            _display_runtime_path(str(workspace_paths["helpers_rel"])),
        ],
        experimental_root=paths["experimental_root"],
        manifest=manifest,
        next_goal=(
            "Compare waam_baseline.nc and waam_baseline_experimental.nc, then refine "
            f"`{_display_runtime_path(str(workspace_paths['planner_rel']))}` for the next iteration."
        ),
    )
    _write_json(
        Path(workspace_paths["prompt_context"]),
        _build_prompt_context(
            context,
            part_label=str(manifest.get("part_label") or context.part_label or context.part_key),
            part_key=str(manifest.get("part_key") or context.part_key),
            part_family=str(manifest.get("part_family") or context.part_family or "bulk_fill"),
            manifest=manifest,
            active_state=active_state,
            paths=paths,
            workspace_paths=workspace_paths,
            knowledge_bundle=knowledge_bundle,
        ),
    )
    _mirror_workspace_to_active(paths, workspace_paths)
    return manifest


class RuntimeActiveExperimentalPlanner:
    key = "runtime_active"
    label = "Active runtime experimental NC"
    output_filename = "waam_baseline_experimental.nc"
    scaffold_dirname = "knowledge/part-lessons"

    def __init__(self, *, experimental_root: Path | str | None = None) -> None:
        self._experimental_root = experimental_root

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        manifest = ensure_active_runtime(context, experimental_root=self._experimental_root)
        context.part_key = str(manifest.get("part_key") or context.part_key)
        context.part_label = str(manifest.get("part_label") or context.part_label)
        context.part_family = str(manifest.get("part_family") or context.part_family)
        if context.part_family_signals is None:
            signals = manifest.get("part_family_signals") or []
            context.part_family_signals = list(signals) if isinstance(signals, list) else []
        planner = _load_active_runtime_planner(context, experimental_root=self._experimental_root)
        target = Path(planner.export_nc(context))
        finalize_runtime_export(context, target, experimental_root=self._experimental_root)
        return target
