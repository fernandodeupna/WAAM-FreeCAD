"""Mesh export utilities used by WAAM mesh preparation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import FreeCAD as App  # type: ignore

from ..core.paths import get_output_dir

try:  # pragma: no cover - FreeCAD injects MeshPart at runtime
    import MeshPart  # type: ignore
except ImportError:  # pragma: no cover
    MeshPart = None  # type: ignore


def _slugify(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    safe = safe.strip("_").lower()
    return safe or "part"


def _mesh_output_dir(base: Optional[Path] = None) -> Path:
    out_root = Path(base) if base else get_output_dir()
    target = out_root / "mesh_exports"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _mesh_from_object(
    obj: App.DocumentObject,
    mesh_linear_deflection: float = 0.25,
    mesh_angular_deflection: float = 0.34906585,
):
    if MeshPart is None:
        raise RuntimeError("FreeCAD MeshPart module is not available in this environment.")

    shape = getattr(obj, "Shape", None)
    if shape is None:
        raise ValueError("Selected object does not expose a Shape for meshing.")
    if shape.Volume == 0 and not shape.BoundBox.isValid():
        raise ValueError("Selected object has an invalid or empty shape.")

    mesh = MeshPart.meshFromShape(
        Shape=shape,
        LinearDeflection=mesh_linear_deflection,
        AngularDeflection=mesh_angular_deflection,
        Relative=False,
    )
    if mesh is None:
        raise RuntimeError("MeshPart failed to generate a triangulation for the supplied shape.")
    try:
        facet_count = getattr(mesh, "CountFacets", None)
        if facet_count is None and hasattr(mesh, "countFacets"):
            facet_count = mesh.countFacets()
        if facet_count is not None and int(facet_count) == 0:
            raise RuntimeError("Meshing produced zero facets; check the selected shape or deflection settings.")
    except Exception:
        # If introspection fails, continue and rely on the file check below.
        pass
    return mesh


def _write_mesh(mesh, *, filename: str, out_dir: Optional[Path] = None) -> Path:
    filename = filename or "part_object.obj"
    target_dir = _mesh_output_dir(out_dir)
    target_path = target_dir / filename

    mesh.write(str(target_path))
    try:
        with target_path.open("r", encoding="utf-8", errors="ignore") as handle:
            has_vertex = any(line.startswith("v ") for line in handle)
        if not has_vertex:
            raise RuntimeError("Exported OBJ is empty (no vertices written).")
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    return target_path


def export_object_to_obj(
    obj: App.DocumentObject,
    *,
    out_dir: Optional[Path] = None,
    mesh_linear_deflection: float = 0.25,
    mesh_angular_deflection: float = 0.34906585,
) -> Path:
    """
    Triangulate the supplied object and persist it as an OBJ mesh snapshot.

    Returns the file path of the exported OBJ.
    """
    mesh = _mesh_from_object(
        obj,
        mesh_linear_deflection=mesh_linear_deflection,
        mesh_angular_deflection=mesh_angular_deflection,
    )
    target_path = _write_mesh(mesh, filename="part_object.obj", out_dir=out_dir)
    return target_path


def export_objects_to_obj(
    objects: Iterable[App.DocumentObject],
    *,
    out_dir: Optional[Path] = None,
    mesh_linear_deflection: float = 0.25,
    mesh_angular_deflection: float = 0.34906585,
    filename: str = "part_object.obj",
) -> Path:
    """
    Triangulate all supplied objects and persist them as a single OBJ mesh snapshot.

    Returns the file path of the exported OBJ.
    """
    candidates = list(objects)
    if not candidates:
        raise ValueError("No objects provided for OBJ export.")
    if MeshPart is None:
        raise RuntimeError("FreeCAD MeshPart module is not available in this environment.")

    meshes = []
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            mesh = _mesh_from_object(
                candidate,
                mesh_linear_deflection=mesh_linear_deflection,
                mesh_angular_deflection=mesh_angular_deflection,
            )
        except Exception as exc:
            last_error = exc
            continue
        meshes.append(mesh)

    if not meshes:
        if last_error:
            raise last_error
        raise ValueError("None of the supplied objects have a valid shape for meshing.")

    combined = meshes[0]
    for mesh in meshes[1:]:
        combined.addMesh(mesh)

    target_path = _write_mesh(combined, filename=filename, out_dir=out_dir)
    return target_path


__all__ = [
    "export_object_to_obj",
    "export_objects_to_obj",
]
