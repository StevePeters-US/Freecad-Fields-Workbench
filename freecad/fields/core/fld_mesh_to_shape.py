# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Coin3D-format triangle arrays -> Part.Shape, sewn into a solid where possible.

Split out of commands/cmd_sdf_export.py (CR-065) -- pure mesh/geometry conversion
with no UI or command dependency, independently reusable/testable outside the
export command it was defined alongside.
"""
import Part

from freecad.fields.core import fld_logger


def _triangles_to_shape(flat_verts, flat_idx):
    """Convert Coin3D-format triangle arrays to a Part.Shape."""
    import Mesh

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
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.1)
    try:
        solid = Part.makeSolid(shape)
        return solid
    except Exception:
        fld_logger.debug("SDF to Shape: could not make solid, returning shell")
        return shape
