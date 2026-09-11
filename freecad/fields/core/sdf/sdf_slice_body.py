# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_slice_body.py

A real B-Rep body built directly from the SDF's zero set on a slice plane,
with no curve fitting anywhere in the path.

This exists to answer one diagnostic question that the fitted curves cannot:
when a slice comes out wrong, is the *contour extraction* wrong or is the
*curve fit* wrong? The Fields curve objects `trace_sdf` produces are the end of a
pipeline (find seeds -> walk -> decimate -> fit Beziers -> check deviation),
so a bad curve indicts all five stages at once. The body here is produced by
sampling the field on a grid, running marching squares on the sign array, and
snapping each vertex onto the surface -- a dense polygon, straight into
`Part`. Put it next to the fitted curves and whichever one is wrong is the
one that disagrees with the field.

The contour itself comes from `sdf_contour.slice_polygons`, which is also what
`trace_sdf` now fits its curves to, so this body and those curves are two
renderings of the same extraction rather than two extractions. That is a
narrower check than it was when the two paths were independent -- it no longer
cross-examines the extraction -- but it is the check that matters day to day:
the body is the dense polygon with no fitting applied, so anywhere the curves
part company with it, the fit is what moved.
"""
import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf_contour import (
    slice_frame, slice_polygons)

V = FreeCAD.Vector

def _signed_area(uv):
    """Twice the signed area of a closed (u, v) polygon; CCW is positive."""
    total = 0.0
    n = len(uv)
    for i in range(n):
        u0, v0 = uv[i]
        u1, v1 = uv[(i + 1) % n]
        total += u0 * v1 - u1 * v0
    return total


def _contains(outer, pt):
    """Ray-cast point-in-polygon on (u, v) coordinates."""
    u, v = pt
    inside = False
    n = len(outer)
    for i in range(n):
        u0, v0 = outer[i]
        u1, v1 = outer[(i + 1) % n]
        if (v0 > v) != (v1 > v):
            if v1 != v0 and u < u0 + (v - v0) * (u1 - u0) / (v1 - v0):
                inside = not inside
    return inside


def build_slice_shape(polys, normal, thickness=0.0):
    """A `Part` shape from `slice_polygons` output.

    Closed polygons become faces, nested by containment so holes are cut rather
    than drawn as extra outlines -- an odd containment depth is a hole. With
    `thickness > 0` each face is extruded into a solid spanning the slab it was
    measured on, so the body occupies the same volume the slab field described.

    Open polygons ran off the grid border and cannot bound a face; they are kept
    as loose edges in the compound rather than dropped, because a slice that
    produced them is a slice worth seeing.
    """
    import Part

    normal, u_axis, v_axis = slice_frame(normal)
    closed, open_polys = [], []
    for pts, is_closed in polys:
        (closed if is_closed else open_polys).append(pts)

    uv_of = lambda pts: [(p.dot(u_axis), p.dot(v_axis)) for p in pts]
    uvs = [uv_of(pts) for pts in closed]

    # Containment depth: even = outer boundary, odd = hole in the one directly
    # enclosing it (the smallest enclosing polygon, by area).
    depth, parent = [], []
    for i, uv_i in enumerate(uvs):
        probe = uv_i[0]
        encl = [j for j, uv_j in enumerate(uvs) if j != i and _contains(uv_j, probe)]
        depth.append(len(encl))
        if encl:
            parent.append(min(encl, key=lambda j: abs(_signed_area(uvs[j]))))
        else:
            parent.append(None)

    shapes = []
    for i, pts in enumerate(closed):
        if depth[i] % 2 == 1:
            continue    # a hole; cut by its parent below
        holes = [k for k in range(len(closed))
                 if parent[k] == i and depth[k] % 2 == 1]
        try:
            wires = [Part.makePolygon([FreeCAD.Vector(p) for p in pts]
                                      + [FreeCAD.Vector(pts[0])])]
            for k in holes:
                hp = closed[k]
                wires.append(Part.makePolygon([FreeCAD.Vector(p) for p in hp]
                                              + [FreeCAD.Vector(hp[0])]))
            face = Part.Face(wires) if len(wires) > 1 else Part.Face(wires[0])
            if thickness > 0.0:
                face.translate(normal * (-0.5 * thickness))
                shapes.append(face.extrude(normal * thickness))
            else:
                shapes.append(face)
        except Exception as exc:
            fld_logger.warn(f"SliceBody: polygon {i} ({len(pts)} pts) "
                           f"would not face: {exc}")

    for pts in open_polys:
        try:
            shapes.append(Part.makePolygon([FreeCAD.Vector(p) for p in pts]))
        except Exception as exc:
            fld_logger.warn(f"SliceBody: open polyline would not build: {exc}")

    if not shapes:
        return None
    return Part.Compound(shapes)


def create_slice_body(doc, label, field, origin, normal, tolerance=0.05,
                      cell=None, thickness=0.0, max_samples=1_000_000):
    """Add a `Part::Feature` of the raw SDF slice to `doc`. Returns it, or None."""
    polys = slice_polygons(field, origin, normal, tolerance=tolerance,
                           cell=cell, thickness=thickness,
                           max_samples=max_samples)
    if not polys:
        fld_logger.warn(f"SliceBody: {label}: no contour on this plane")
        return None
    shape = build_slice_shape(polys, normal, thickness=thickness)
    if shape is None:
        fld_logger.warn(f"SliceBody: {label}: no shape built from "
                       f"{len(polys)} polygon(s)")
        return None
    obj = doc.addObject("Part::Feature", label)
    obj.Label = label
    obj.Shape = shape
    return obj
