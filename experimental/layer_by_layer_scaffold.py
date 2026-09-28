from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from .base import ExperimentalPlannerContext
from ..waamgen import generate_nc as waamgen_generate


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "current-part"


def _load_json(path: Path) -> Mapping[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def derive_part_key(features: Mapping[str, object]) -> str:
    metadata = features.get("metadata") or {}
    work_obj = metadata.get("work_obj")
    if isinstance(work_obj, str) and work_obj.strip():
        return _slugify(work_obj)
    return "current-part"


def derive_part_key_from_path(features_path: Path) -> str:
    return derive_part_key(_load_json(features_path))


def _template_path() -> Path:
    return Path(__file__).resolve().parent / "knowledge" / "part-lessons" / "_template.md"


def _experimental_root(experimental_root: Path | str | None = None) -> Path:
    if experimental_root is not None:
        return Path(experimental_root).expanduser().resolve()
    return Path(__file__).resolve().parent


def _knowledge_root(experimental_root: Path | str | None = None) -> Path:
    return _experimental_root(experimental_root) / "knowledge" / "part-lessons"


def _promoted_root(experimental_root: Path | str | None = None) -> Path:
    return _knowledge_root(experimental_root) / "promoted"


def _archive_root(experimental_root: Path | str | None = None) -> Path:
    return _experimental_root(experimental_root) / "runtime" / "archive"


def _features_signature(features_path: Path) -> str:
    if not features_path.is_file():
        return ""
    return hashlib.sha1(features_path.read_bytes()).hexdigest()[:12]


def _workspace_key(
    context: ExperimentalPlannerContext,
    manifest: Mapping[str, object] | None = None,
) -> str:
    if manifest:
        workspace_key = str(manifest.get("workspace_key") or "").strip()
        if workspace_key:
            return workspace_key
    signature = _features_signature(context.features_path)
    if signature:
        return signature
    if context.run_id:
        return _slugify(context.run_id)
    return "current"


def _display_path(path: Path, *, experimental_root: Path | str | None = None) -> str:
    rel = path.resolve().relative_to(_experimental_root(experimental_root))
    return (Path("Waam_tech") / "experimental" / rel).as_posix()


def _lesson_bundle_paths(
    context: ExperimentalPlannerContext,
    *,
    experimental_root: Path | str | None = None,
    manifest: Mapping[str, object] | None = None,
) -> dict[str, Path | str]:
    part_key = context.part_key or derive_part_key(_load_json(context.features_path))
    workspace_key = _workspace_key(context, manifest)
    lesson_dir = _knowledge_root(experimental_root) / part_key / workspace_key
    lesson_path = lesson_dir / "lesson.md"
    context_path = lesson_dir / "context.json"
    return {
        "part_key": part_key,
        "workspace_key": workspace_key,
        "lesson_dir": lesson_dir,
        "lesson_path": lesson_path,
        "context_path": context_path,
        "lesson_display": _display_path(lesson_path, experimental_root=experimental_root),
        "context_display": _display_path(context_path, experimental_root=experimental_root),
    }


def _lesson_entries(experimental_root: Path | str | None = None) -> list[dict[str, str]]:
    sync_archived_runtime_lessons(experimental_root=experimental_root)
    entries: list[dict[str, str]] = []
    for context_path in sorted(_knowledge_root(experimental_root).rglob("context.json")):
        payload = _load_json(context_path)
        lesson_path = context_path.with_name("lesson.md")
        if not lesson_path.is_file():
            continue
        part_key = str(payload.get("part_key") or context_path.parent.parent.name or "").strip()
        workspace_key = str(payload.get("workspace_key") or context_path.parent.name or "").strip()
        if not part_key or not workspace_key:
            continue
        entries.append(
            {
                "part_key": part_key,
                "part_family": str(payload.get("part_family") or "").strip(),
                "workspace_key": workspace_key,
                "status": str(payload.get("status") or "").strip(),
                "lesson_display": _display_path(lesson_path, experimental_root=experimental_root),
                "updated_at": str(payload.get("updated_at") or payload.get("created_at") or "").strip(),
            }
        )
    return entries


def _promoted_markdown_paths(
    *parts: str,
    experimental_root: Path | str | None = None,
) -> list[Path]:
    root = _promoted_root(experimental_root).joinpath(*parts)
    if not root.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(root.glob("*.md")):
        if path.name.startswith("_") or path.name.lower() == "readme.md":
            continue
        paths.append(path)
    return paths


def sync_archived_runtime_lessons(experimental_root: Path | str | None = None) -> None:
    archive_root = _archive_root(experimental_root)
    if not archive_root.is_dir():
        return

    for manifest_path in sorted(archive_root.glob("*/*/manifest.json")):
        archive_dir = manifest_path.parent
        manifest = _load_json(manifest_path)
        part_key = str(manifest.get("part_key") or archive_dir.parent.name or "").strip()
        if not part_key:
            continue
        workspace_key = archive_dir.name
        lesson_dir = _knowledge_root(experimental_root) / part_key / workspace_key
        lesson_path = lesson_dir / "lesson.md"
        context_path = lesson_dir / "context.json"
        if lesson_path.is_file() and context_path.is_file():
            continue

        lesson_dir.mkdir(parents=True, exist_ok=True)
        notes_path = archive_dir / "notes.md"
        planner_path = archive_dir / "planner.py"
        helpers_path = archive_dir / "helpers.py"
        lesson_display = _display_path(lesson_path, experimental_root=experimental_root)
        context_display = _display_path(context_path, experimental_root=experimental_root)
        archive_display = _display_path(archive_dir, experimental_root=experimental_root)
        notes_display = _display_path(notes_path, experimental_root=experimental_root) if notes_path.is_file() else ""

        if notes_path.is_file():
            lesson_text = notes_path.read_text(encoding="utf-8")
            if "## Archived Source" not in lesson_text:
                lesson_text = lesson_text.rstrip() + "\n\n## Archived Source\n"
                lesson_text += f"- archive: `{archive_display}`\n"
                if notes_display:
                    lesson_text += f"- source notes: `{notes_display}`\n"
        else:
            lesson_text = (
                f"# Archived Experiment Lesson: {manifest.get('part_label') or part_key}\n\n"
                "## Archived Source\n"
                f"- archive: `{archive_display}`\n"
            )
        lesson_path.write_text(lesson_text.rstrip() + "\n", encoding="utf-8")

        context_payload = {
            "part_key": part_key,
            "part_label": str(manifest.get("part_label") or part_key.replace("-", " ").title()),
            "part_family": str(manifest.get("part_family") or ""),
            "workspace_key": workspace_key,
            "features_signature": str(manifest.get("features_signature") or ""),
            "status": "archived",
            "planner": "runtime_archive",
            "planner_module": _display_path(planner_path, experimental_root=experimental_root) if planner_path.is_file() else "",
            "planner_basis": str(manifest.get("planner_basis") or ""),
            "seed_source": str(manifest.get("seed_source") or ""),
            "seed_archive_path": archive_display,
            "created_at": str(manifest.get("created_at") or ""),
            "updated_at": str(manifest.get("updated_at") or manifest.get("last_exported_at") or ""),
            "artifacts": {
                "experimental_nc": str(manifest.get("experimental_nc") or ""),
                "baseline_nc": str(manifest.get("baseline_nc") or ""),
                "slice_plan": str(manifest.get("plan_path") or ""),
                "features": str(manifest.get("features_path") or ""),
            },
            "knowledge_paths": {
                "lesson": lesson_display,
                "context": context_display,
            },
            "archive_paths": {
                "archive_dir": archive_display,
                "notes": notes_display,
                "planner": _display_path(planner_path, experimental_root=experimental_root) if planner_path.is_file() else "",
                "helpers": _display_path(helpers_path, experimental_root=experimental_root) if helpers_path.is_file() else "",
            },
            "related_lessons": [],
            "recommended_reads": [item for item in [lesson_display, notes_display] if item],
            "next_goal": "Use this archived lesson as reference for future part-specific prompt iterations.",
        }
        context_path.write_text(json.dumps(context_payload, indent=2), encoding="utf-8")


def select_relevant_lessons(
    *,
    part_key: str,
    part_family: str,
    workspace_key: str,
    experimental_root: Path | str | None = None,
    limit: int = 4,
) -> list[str]:
    same_part: list[dict[str, str]] = []
    same_family: list[dict[str, str]] = []
    for entry in _lesson_entries(experimental_root):
        if entry["status"] != "archived":
            continue
        if entry["part_key"] == part_key and entry["workspace_key"] == workspace_key:
            continue
        if entry["part_key"] == part_key:
            same_part.append(entry)
        elif part_family and entry["part_family"] == part_family:
            same_family.append(entry)

    def _sort_key(entry: Mapping[str, str]) -> tuple[str, str]:
        return (str(entry.get("updated_at") or ""), str(entry.get("lesson_display") or ""))

    same_part.sort(key=_sort_key, reverse=True)
    same_family.sort(key=_sort_key, reverse=True)

    selected: list[str] = []
    for entry in same_part:
        if len(selected) >= limit:
            break
        selected.append(entry["lesson_display"])
    for entry in same_family:
        if len(selected) >= limit:
            break
        if entry["lesson_display"] in selected:
            continue
        selected.append(entry["lesson_display"])
    return selected


def select_promoted_lessons(
    *,
    part_key: str,
    experimental_root: Path | str | None = None,
    limit: int = 4,
) -> list[str]:
    selected: list[str] = []
    part_lesson = _promoted_root(experimental_root) / "parts" / f"{part_key}.md"
    if part_lesson.is_file():
        selected.append(_display_path(part_lesson, experimental_root=experimental_root))

    for path in _promoted_markdown_paths("strategies", experimental_root=experimental_root):
        if len(selected) >= limit:
            break
        display = _display_path(path, experimental_root=experimental_root)
        if display not in selected:
            selected.append(display)
    return selected


def write_scaffold_bundle(
    context: ExperimentalPlannerContext,
    exp_nc: Path,
    *,
    planner_key: str = "layer_by_layer",
    planner_module: str = "Waam_tech/experimental/layer_by_layer_scaffold.py",
    recommended_reads: list[str] | None = None,
    experimental_root: Path | str | None = None,
    manifest: Mapping[str, object] | None = None,
    next_goal: str = (
        "Create the next part-specific experimental improvement from the clean scaffold, "
        "not from stale hard-coded legacy part scripts."
    ),
) -> dict[str, object]:
    bundle_paths = _lesson_bundle_paths(
        context,
        experimental_root=experimental_root,
        manifest=manifest,
    )
    lesson_dir = Path(bundle_paths["lesson_dir"])
    lesson_dir.mkdir(parents=True, exist_ok=True)

    part_key = str(bundle_paths["part_key"])
    workspace_key = str(bundle_paths["workspace_key"])
    lesson_path = Path(bundle_paths["lesson_path"])
    context_path = Path(bundle_paths["context_path"])
    extra_reads = list(
        recommended_reads
        or [
            "Waam_tech/experimental/runtime/active/planner.py",
            "Waam_tech/experimental/runtime/active/helpers.py",
        ]
    )
    promoted_lessons = select_promoted_lessons(
        part_key=part_key,
        experimental_root=experimental_root,
    )
    related_lessons = select_relevant_lessons(
        part_key=part_key,
        part_family=context.part_family or str((manifest or {}).get("part_family") or ""),
        workspace_key=workspace_key,
        experimental_root=experimental_root,
    )
    future_reads: list[str] = []
    for item in [
        str(bundle_paths["lesson_display"]),
        str(bundle_paths["context_display"]),
        *promoted_lessons,
        *related_lessons,
        *extra_reads,
    ]:
        if item and item not in future_reads:
            future_reads.append(item)

    template = _template_path().read_text(encoding="utf-8")
    lesson_text = (
        template.replace("<part-name>", part_key.replace("-", " ").title())
        .replace("<slug-name>", part_key)
    )
    lesson_text = lesson_text.replace(
        "- geometry family: <thin-wall / bulk / multi-hole / topology-changing / curved / etc.>",
        f"- geometry family: {context.part_family or 'pending layer-by-layer classification'}",
    )
    lesson_text = lesson_text.replace(
        "- status: <active / archived / promoted / diagnostic>",
        "- status: active",
    )
    lesson_text = lesson_text.replace(
        "- which experimental planner / module is being used?",
        "- which experimental planner / module is being used?\n"
        f"- `{planner_module}`",
    )
    lesson_text = lesson_text.replace(
        "- which files were created or modified?",
        "- which files were created or modified?\n"
        f"- lesson: `{bundle_paths['lesson_display']}`\n"
        f"- lesson context: `{bundle_paths['context_display']}`\n"
        f"- experimental NC: `{exp_nc}`\n"
        f"- slice plan: `{context.plan_path}`\n"
        f"- features: `{context.features_path}`",
    )
    lesson_text = lesson_text.replace(
        "- what should a future model read first?",
        "- what should a future model read first?\n"
        + "\n".join(f"- `{item}`" for item in future_reads),
    )

    lesson_path.write_text(lesson_text, encoding="utf-8")

    scaffold_payload = {
        "part_key": part_key,
        "part_label": context.part_label or part_key.replace("-", " ").title(),
        "part_family": context.part_family or "",
        "workspace_key": workspace_key,
        "features_signature": _features_signature(context.features_path),
        "status": str((manifest or {}).get("status") or "active"),
        "planner": planner_key,
        "planner_module": planner_module,
        "planner_basis": "layer-by-layer material deposition",
        "seed_source": str((manifest or {}).get("seed_source") or ""),
        "seed_archive_path": str((manifest or {}).get("seed_archive_path") or ""),
        "created_at": str((manifest or {}).get("created_at") or ""),
        "updated_at": str((manifest or {}).get("updated_at") or ""),
        "artifacts": {
            "experimental_nc": str(exp_nc),
            "slice_plan": str(context.plan_path),
            "features": str(context.features_path),
        },
        "knowledge_paths": {
            "lesson": str(bundle_paths["lesson_display"]),
            "context": str(bundle_paths["context_display"]),
        },
        "promoted_lessons": promoted_lessons,
        "related_lessons": related_lessons,
        "recommended_reads": future_reads,
        "next_goal": next_goal,
    }
    context_path.write_text(json.dumps(scaffold_payload, indent=2), encoding="utf-8")
    return {
        "lesson_path": str(lesson_path),
        "context_path": str(context_path),
        "lesson_display": str(bundle_paths["lesson_display"]),
        "context_display": str(bundle_paths["context_display"]),
        "promoted_lessons": promoted_lessons,
        "related_lessons": related_lessons,
        "recommended_reads": future_reads,
        "part_key": part_key,
        "workspace_key": workspace_key,
    }


class LayerByLayerExperimentalPlanner:
    key = "layer_by_layer"
    label = "Layer-by-layer experimental NC scaffold"
    output_filename = "waam_baseline_experimental.nc"
    scaffold_dirname = "knowledge/part-lessons"

    def export_nc(self, context: ExperimentalPlannerContext) -> Path:
        nc_dir = context.output_dir / "nc_files"
        nc_dir.mkdir(parents=True, exist_ok=True)
        target = nc_dir / self.output_filename
        waamgen_generate(
            context.plan_path,
            output_path=target,
            max_iters=1,
            geom_tol=0.8,
            plot=False,
        )
        write_scaffold_bundle(context, target)
        return target
