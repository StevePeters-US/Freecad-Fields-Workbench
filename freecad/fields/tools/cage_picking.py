# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Cage picking: ray hit tests over control points and the closest-face search. Stateless functions."""
import numpy as np

from freecad.fields.core.sdf.sdf.cage import _closest_on_face, _closest_on_tri_face


def nearest_point(tool, ray_p, ray_d, indices, tolerance_of):
    """Index in `indices` of the control point nearest the ray, or None.

    `tolerance_of(i)` is the perpendicular-distance tolerance of point i; the
    tolerance is per call site on purpose (see pick_handle and the hover picks).
    """
    best_idx = None
    best_dist = float('inf')
    for i in indices:
        pt = tool.points[i]
        idx, dist = tool._hit_test_perp(ray_p, ray_d, [pt], tolerance=tolerance_of(i))
        if idx is not None and dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def pick_handle(tool, ray_p, ray_d, *, include_vertices, vertex_tol=1.0, handle_tol=0.6):
    """Click pick: nearest control point within `vertex_tol` / `handle_tol` handle radii.

    LMB passes include_vertices=True with the defaults (vertices r * 1.0, handles r * 0.6);
    RMB passes include_vertices=False and skips vertices, keeping the handle tolerance.
    """
    r = tool._compute_handle_radius()
    n_verts = getattr(tool, '_n_verts', 8)
    indices = range(len(tool.points)) if include_vertices else range(n_verts, len(tool.points))
    return nearest_point(tool, ray_p, ray_d, indices,
                         lambda i: r * (vertex_tol if i < n_verts else handle_tol))


def pick_hover_edge_handle(tool, ray_p, ray_d):
    """Hover pick over handles only, with the fixed 1000 mm tolerance. Returns the handle index."""
    best = nearest_point(tool, ray_p, ray_d, range(tool._n_verts, len(tool.points)),
                         lambda i: 1000.0)
    return None if best is None else best - tool._n_verts


def pick_hover_vertex(tool, ray_p, ray_d):
    """Hover pick over vertices only, with the fixed 1000 mm tolerance."""
    return nearest_point(tool, ray_p, ray_d, range(tool._n_verts), lambda i: 1000.0)


def closest_face(field, q):
    """Index of the face whose patch is closest to world point `q` (numpy array), or None."""
    ctrl_cache = getattr(field, "_face_cached_ctrl_pts", None) or []

    best_fi, best_dist = None, float("inf")
    for fi in range(len(field._face_verts)):
        ctrl = ctrl_cache[fi] if fi < len(ctrl_cache) else None
        if ctrl is None:
            # No cached patch (n-gon): fall back to corner polygon centroid
            dist = float(np.linalg.norm(field.vertices[field._face_verts[fi]].mean(axis=0) - q))
        elif len(field._face_verts[fi]) == 4:
            dist = _closest_on_face(*ctrl, q, simplify=True)[2]
        else:
            dist = _closest_on_tri_face(ctrl, q, simplify=True)[2]
        if dist < best_dist:
            best_dist, best_fi = dist, fi
    return best_fi
