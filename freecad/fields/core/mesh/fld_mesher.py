# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/fld_mesher.py

Public entry point for the meshing pipeline: mesher selection (`get_active_mesher`)
plus re-exports of the pieces the rest of the codebase imports by name
(`FldMesher`, the three concrete meshers, `MeshTimer`/`mesh_timer`, and the
post-processing functions). The implementations themselves live split across
sibling modules — see fld_mesher_architecture skill for the map:

  mesh_base.py             - FldMesher abstract base (octree build/cache pipeline)
  mesh_shared.py           - vectorized helpers shared by >1 mesher
  mesh_timer.py            - MeshTimer + the mesh_timer singleton
  mesh_marching_cubes.py   - MarchingCubesMesher
  mesh_surface_nets.py     - SurfaceNetsMesher
  mesh_dual_contouring.py  - DualContouringMesher
  mesh_postprocess.py      - decimate_flat_tris, deduplicate_verts

Keep importing from `freecad.fields.core.mesh.fld_mesher` — this module re-exports
everything callers currently use; only the file each class lives in changed.
"""
from freecad.fields.core.mesh.mesh_base import FldMesher
from freecad.fields.core.mesh.mesh_timer import MeshTimer, mesh_timer
from freecad.fields.core.mesh.mesh_postprocess import decimate_flat_tris, deduplicate_verts
from freecad.fields.core.mesh.mesh_marching_cubes import MarchingCubesMesher
from freecad.fields.core.mesh.mesh_surface_nets import SurfaceNetsMesher
from freecad.fields.core.mesh.mesh_dual_contouring import DualContouringMesher

__all__ = [
    "FldMesher",
    "MeshTimer", "mesh_timer",
    "MarchingCubesMesher", "SurfaceNetsMesher", "DualContouringMesher",
    "decimate_flat_tris", "deduplicate_verts",
    "get_active_mesher",
]


def get_active_mesher(type_override=None) -> FldMesher:
    """Return the active mesher based on type override, defaulting to Marching Cubes (0).

    Handles both integer indices and string labels from FreeCAD's PropertyEnumeration.
    """
    st = type_override if type_override is not None else 0

    # 0: MarchingCubes, 1: SurfaceNets, 2: DualContouring
    label = str(st)
    if st == 1 or "Surface Nets" in label:
        return SurfaceNetsMesher()
    if st == 2 or "Dual Contouring" in label:
        return DualContouringMesher()
    return MarchingCubesMesher()
