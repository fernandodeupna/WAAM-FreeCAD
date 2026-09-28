from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any


AUTOMATION_DIR = Path(__file__).resolve().parent
WAAM_TECH_DIR = AUTOMATION_DIR.parent
INPUT_STEP_DIR = WAAM_TECH_DIR / "input_step"
STEP_FILES_DIR = INPUT_STEP_DIR / "step_files"
CONFIG_PATH = AUTOMATION_DIR / "run_waam_headless.json"
SHARED_PART_STEP_PATH = INPUT_STEP_DIR / "part.step"
IMAGE_LAYERS_DIR = WAAM_TECH_DIR / "output" / "image_layers"
TARGET_LAYER_COUNT = 20
EXPERIMENTAL_NC_FILENAME = "waam_baseline_experimental.nc"
_ALLOWED_STEP_SUFFIXES = {".step", ".STEP", ".stp", ".STP"}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _available_step_filenames() -> list[str]:
    if not STEP_FILES_DIR.is_dir():
        return []
    names: list[str] = []
    for path in sorted(STEP_FILES_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.suffix not in _ALLOWED_STEP_SUFFIXES:
            continue
        names.append(path.name)
    return names


def _default_headless_config() -> dict[str, str]:
    available = _available_step_filenames()
    default_name = available[0] if available else ""
    return {
        "step_filename": default_name,
    }


def _ensure_headless_config(path: Path) -> dict[str, str]:
    if path.is_file():
        return _load_json(path)
    payload = _default_headless_config()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    chosen = payload.get("step_filename") or "<set-a-step-file-name>"
    print(f"[WAAM] Created local headless config at {path} with step_filename={chosen}")
    return payload


def _resolve_step_path() -> Path:
    config = _ensure_headless_config(CONFIG_PATH)
    filename = str(config.get("step_filename") or "").strip()
    if not filename:
        raise RuntimeError(f"Missing 'step_filename' in config: {CONFIG_PATH}")
    candidate = Path(filename)
    if candidate.parent != Path("."):
        raise RuntimeError("Config step_filename must be a filename only, not a path.")
    if candidate.suffix not in _ALLOWED_STEP_SUFFIXES:
        raise RuntimeError("Config step_filename must end with .step, .STEP, .stp, or .STP")
    return (STEP_FILES_DIR / candidate.name).resolve()


def _missing_step_message(step_path: Path) -> str:
    return (
        f"STEP file not found: {step_path}\n"
        f"Set the filename in: {CONFIG_PATH}\n"
        f"Store STEP files in: {STEP_FILES_DIR}"
    )


def _looks_like_step(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:256].upper()
    except Exception:
        return False
    return "ISO-10303-21" in head or "FILE_SCHEMA" in head or "FILE_DESCRIPTION" in head


def _ensure_waam_on_syspath(app_module) -> None:
    waam_root = Path(app_module.getUserAppDataDir()) / "Mod" / "WAAM"
    if str(waam_root) not in sys.path:
        sys.path.insert(0, str(waam_root))


def _collect_visible_parts(doc) -> list[Any]:
    visible_parts: list[Any] = []
    for obj in getattr(doc, "Objects", []):
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        visible_parts.append(obj)
    return visible_parts


def _render_sampled_layers_image(
    nc_path: Path,
    out_path: Path,
    target_layer_count: int = TARGET_LAYER_COUNT,
) -> list[int]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

    layer_re = re.compile(r"^\(--- LAYER (\d+) Z=([-+0-9.]+) ---\)")
    coord_re = re.compile(r"([XYZ])([-+]?\d*\.?\d+)")

    lines = nc_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    all_layers = []
    for raw in lines:
        match = layer_re.match(raw.strip())
        if match:
            all_layers.append(int(match.group(1)))
    all_layers = sorted(set(all_layers))

    if len(all_layers) <= target_layer_count:
        selected_layers = set(all_layers)
    else:
        first_layer = all_layers[0]
        last_layer = all_layers[-1]
        middle = all_layers[1:-1]
        needed_middle = target_layer_count - 2
        selected_middle = []
        for idx in range(needed_middle):
            middle_idx = round(idx * (len(middle) - 1) / max(1, needed_middle - 1))
            selected_middle.append(middle[middle_idx])
        selected_layers = {first_layer, last_layer, *selected_middle}

    x = y = z = 0.0
    current_layer = None
    torch_on = False
    segments = []

    for raw in lines:
        line = raw.strip()
        match = layer_re.match(line)
        if match:
            current_layer = int(match.group(1))
            continue
        if current_layer is None or current_layer not in selected_layers:
            continue
        if line.startswith("M62 P0"):
            torch_on = True
            continue
        if line.startswith("M63 P0"):
            torch_on = False
            continue
        if not (line.startswith("G0") or line.startswith("G1")):
            continue
        new_x, new_y, new_z = x, y, z
        for axis, value in coord_re.findall(line):
            numeric = float(value)
            if axis == "X":
                new_x = numeric
            elif axis == "Y":
                new_y = numeric
            elif axis == "Z":
                new_z = numeric
        if line.startswith("G1") and torch_on:
            segments.append(((x, y, z), (new_x, new_y, new_z), current_layer))
        x, y, z = new_x, new_y, new_z

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(10, 8), dpi=180)
    ax = fig.add_subplot(111, projection="3d")

    if segments:
        ordered_layers = sorted(selected_layers)
        layer_to_color_idx = {layer: idx for idx, layer in enumerate(ordered_layers)}
        cmap = plt.cm.plasma
        denom = max(1, len(ordered_layers) - 1)
        for start, end, layer_idx in segments:
            color = cmap(layer_to_color_idx[layer_idx] / denom)
            ax.plot(
                [start[0], end[0]],
                [start[1], end[1]],
                [start[2], end[2]],
                color=color,
                linewidth=1.2,
            )

    ax.set_title("WAAM baseline NC, 20 sampled layers\n(first, last, and 18 middle layers)")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    plt.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return sorted(selected_layers)


def _prepare_experimental_export(
    *,
    output_dir: Path,
    nc_out: Path,
    artifacts,
    features_path: Path,
    step_path: Path,
    config_override,
    experimental_planner_name: str | None,
) -> tuple[Path, Path | None, str | None]:
    from Waam_tech.core.paths import get_nc_output_path
    from Waam_tech.experimental.base import ExperimentalPlannerContext
    from Waam_tech.experimental.registry import get_experimental_planner, select_experimental_planner
    from Waam_tech.experimental.runtime import derive_part_profile_from_features_path, new_run_id

    experimental_out = get_nc_output_path(
        filename=EXPERIMENTAL_NC_FILENAME,
        create=True,
        prefer_existing=False,
    )
    if experimental_out.resolve() != nc_out.resolve():
        shutil.copyfile(nc_out, experimental_out)

    planner = get_experimental_planner(experimental_planner_name) if experimental_planner_name else None
    if planner is None:
        planner = select_experimental_planner(
            plan_path=Path(artifacts.plan_path),
            features_path=Path(features_path),
        )

    if planner is None:
        return experimental_out, experimental_out, None

    part_label, part_key, part_family, part_family_signals = derive_part_profile_from_features_path(
        Path(features_path),
        step_path=step_path,
    )
    context = ExperimentalPlannerContext(
        output_dir=output_dir,
        plan_path=Path(artifacts.plan_path),
        features_path=Path(features_path),
        config_override=config_override,
        part_key=part_key,
        part_label=part_label,
        part_family=part_family,
        part_family_signals=part_family_signals,
        step_path=step_path,
        baseline_nc_path=Path(nc_out),
        run_id=new_run_id(),
    )
    exp_nc = Path(planner.export_nc(context))
    if exp_nc.resolve() == nc_out.resolve():
        shutil.copyfile(nc_out, experimental_out)
        exp_nc = experimental_out
    elif exp_nc.resolve() != experimental_out.resolve():
        shutil.copyfile(exp_nc, experimental_out)
        exp_nc = experimental_out
    return experimental_out, exp_nc, planner.label


def run_headless_pipeline(
    *,
    enable_experimental: bool,
    experimental_planner_name: str | None = "",
    config_override=None,
) -> dict[str, Any]:
    import FreeCAD as App
    import Import

    _ensure_waam_on_syspath(App)

    from Waam_tech.core.config_loader import resolve_standard_config_path
    from Waam_tech.core.paths import (
        get_features_path,
        get_nc_output_path,
        get_output_dir,
        get_slice_plan_path,
    )
    from Waam_tech.slicing import write_slice_plan_for_document
    from Waam_tech.slicing.step_to_waam_dsl import default_llm_waam_output_path
    from Waam_tech.waamgen import generate_nc as waamgen_generate

    step_path = _resolve_step_path()
    print(f"[WAAM] Selected shared STEP path: {step_path}")

    if not step_path.is_file():
        raise FileNotFoundError(_missing_step_message(step_path))
    if not _looks_like_step(step_path):
        raise RuntimeError(f"Input does not look like a valid STEP file: {step_path}")

    config_path = resolve_standard_config_path()
    config = _load_json(config_path)
    output_dir = get_output_dir()
    plan_path = get_slice_plan_path(create=True, prefer_existing=False)
    features_path = get_features_path(create=True, prefer_existing=False)
    llm_waam_out = default_llm_waam_output_path(str(step_path))
    nc_out = get_nc_output_path(create=True, prefer_existing=False)
    image_out = IMAGE_LAYERS_DIR / "waam_baseline_20_layers.png"

    doc = App.newDocument("WAAMHeadless")
    Import.insert(str(step_path), doc.Name)
    App.setActiveDocument(doc.Name)

    visible_parts = _collect_visible_parts(doc)
    if not visible_parts:
        raise RuntimeError("No visible solids imported from STEP for headless WAAM pipeline.")

    artifacts = write_slice_plan_for_document(
        doc=doc,
        objects=visible_parts,
        config=config,
        output_path=plan_path,
        config_path=config_path,
        config_override=config_override,
        features_path=features_path,
        llm_waam_output_path=llm_waam_out,
    )

    waamgen_generate(artifacts.plan_path, output_path=nc_out, max_iters=1, geom_tol=0.8, plot=False)
    selected_layers = _render_sampled_layers_image(Path(nc_out), image_out)

    experimental_out = None
    exp_nc = None
    planner_label = None
    if enable_experimental:
        experimental_out, exp_nc, planner_label = _prepare_experimental_export(
            output_dir=output_dir,
            nc_out=Path(nc_out),
            artifacts=artifacts,
            features_path=Path(features_path),
            step_path=step_path,
            config_override=config_override,
            experimental_planner_name=experimental_planner_name,
        )

    result = {
        "step_path": step_path,
        "plan_path": Path(artifacts.plan_path),
        "features_path": Path(features_path),
        "baseline_nc": Path(nc_out),
        "experimental_scaffold": experimental_out,
        "experimental_nc": exp_nc,
        "experimental_planner": planner_label,
        "image_layers": image_out,
        "selected_layers": selected_layers,
    }
    _print_summary(result)
    return result


def _print_summary(result: dict[str, Any]) -> None:
    print("Pipeline finished.")
    print("STEP:", result["step_path"])
    print("Plan:", result["plan_path"])
    print("Features:", result["features_path"])
    print("Baseline NC:", result["baseline_nc"])
    if result["experimental_scaffold"] is not None:
        print("Experimental scaffold:", result["experimental_scaffold"])
    if result["experimental_nc"] is not None:
        print("Experimental NC:", result["experimental_nc"])
    if result["experimental_planner"]:
        print("Experimental planner:", result["experimental_planner"])
    print("Image layers:", result["image_layers"])
    print("Selected layers:", result["selected_layers"])


def main(*, enable_experimental: bool, experimental_planner_name: str | None = "") -> None:
    run_headless_pipeline(
        enable_experimental=enable_experimental,
        experimental_planner_name=experimental_planner_name,
    )
