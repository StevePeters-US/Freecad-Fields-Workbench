# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_base.py

`FldMesher`, the abstract base every Fields SDF mesher extends. Implements the
shared octree-build / cache / timing / post-processing pipeline (Template
Method pattern); subclasses provide the isosurface extraction logic via
`_extract_mesh_geometry`.
"""
import numpy as np

from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.mesh.mesh_timer import mesh_timer
from freecad.fields.core.mesh.mesh_postprocess import decimate_flat_tris, deduplicate_verts


class FldMesher:
    """Abstract base class for all Fields SDF meshers.

    Implements the shared octree-build / timing / post-processing pipeline
    (Template Method pattern); subclasses provide the isosurface extraction
    logic via `_extract_mesh_geometry`.
    """
    def mesh(self, field: SdfField, cell_size: float, decimate=False, deduplicate=True, bounds=None, **kwargs) -> tuple:
        from freecad.fields.core.sdf.sdf_octree import SdfOctreeCache
        mesh_timer.tick()

        proxy = kwargs.get("proxy", None)
        octree = None

        if proxy is not None:
            cached_octree = getattr(proxy, "_octree_cache", None)
            if cached_octree is not None and abs(cached_octree.leaf_size - cell_size) < 1e-6:
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                if isinstance(field, SdfCageField):
                    current_sig = (len(field.vertices), tuple(tuple(f) for f in field._face_verts))
                else:
                    current_sig = None

                cached_sig = getattr(proxy, "_octree_topo_sig", None)
                if cached_sig == current_sig:
                    octree = cached_octree
                    octree.field = field
                    if bounds is not None:
                        mesh_timer.start("field_eval")
                        octree.update_region(bounds)
                        mesh_timer.stop("field_eval")
                    else:
                        mesh_timer.start("field_eval")
                        octree.build()
                        mesh_timer.stop("field_eval")

        if octree is None:
            octree = SdfOctreeCache(field, leaf_size=cell_size)
            mesh_timer.start("field_eval")
            octree.build(bounds=bounds)
            mesh_timer.stop("field_eval")
            if proxy is not None:
                proxy._octree_cache = octree
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                if isinstance(field, SdfCageField):
                    proxy._octree_topo_sig = (len(field.vertices), tuple(tuple(f) for f in field._face_verts))
                else:
                    proxy._octree_topo_sig = None

        if not octree._leaves:
            return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

        flat_verts, flat_idx = self._extract_mesh_geometry(octree, field, cell_size, **kwargs)

        if decimate:
            mesh_timer.start("decimate")
            flat_verts, flat_idx = decimate_flat_tris(flat_verts, flat_idx)
            mesh_timer.stop("decimate")

        if deduplicate:
            mesh_timer.start("deduplicate")
            flat_verts, flat_idx = deduplicate_verts(flat_verts, flat_idx)
            mesh_timer.stop("deduplicate")

        return flat_verts, flat_idx

    def _extract_mesh_geometry(self, octree, field: SdfField, cell_size: float, **kwargs) -> tuple:
        raise NotImplementedError("mesher must implement _extract_mesh_geometry()")
