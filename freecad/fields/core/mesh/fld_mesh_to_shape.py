# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Coin3D-format triangle arrays -> Part.Shape, sewn into a solid where possible.

Split out of commands/cmd_sdf_export.py (CR-065) -- pure mesh/geometry conversion
with no UI or command dependency, independently reusable/testable outside the
export command it was defined alongside.
"""
import numpy as np
import Part

from freecad.fields.core import fld_logger

# Sewing tolerance as a fraction of the mesh's bounding-box diagonal, so it
# scales with the mesh instead of merging/failing-to-merge vertices at a
# fixed 0.1 model-unit distance regardless of part size.
_SEW_TOLERANCE_FRACTION = 1e-4
_SEW_TOLERANCE_MIN = 1e-6


def _triangles_to_shape(flat_verts, flat_idx):
    """Convert Coin3D-format triangle arrays to a Part.Shape."""
    import Mesh

    if len(flat_verts) == 0:
        return None

    # Extract triangle vertex indices (skip -1 sentinels)
    idx = flat_idx.reshape(-1, 4)[:, :3]  # (N_tris, 3)

    # Build Mesh.Mesh from facets
    facets = []
    for tri in idx:
        v0 = flat_verts[tri[0]]
        v1 = flat_verts[tri[1]]
        v2 = flat_verts[tri[2]]
        facets.append([
            (float(v0[0]), float(v0[1]), float(v0[2])),
            (float(v1[0]), float(v1[1]), float(v1[2])),
            (float(v2[0]), float(v2[1]), float(v2[2])),
        ])

    mesh = Mesh.Mesh(facets)

    # Convert to Part.Shape via sewing
    diag = float(np.linalg.norm(flat_verts.max(axis=0) - flat_verts.min(axis=0)))
    tolerance = max(diag * _SEW_TOLERANCE_FRACTION, _SEW_TOLERANCE_MIN)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, tolerance)
    try:
        solid = Part.makeSolid(shape)
        return solid
    except Exception:
        fld_logger.debug("SDF to Shape: could not make solid, returning shell")
        return shape
