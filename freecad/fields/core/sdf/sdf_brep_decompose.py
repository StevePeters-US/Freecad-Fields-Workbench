# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""B-Rep face decomposition: TopoDS_Face -> Bezier/Coons patch specs.

Split out of sdf_brep_converter.py (CR-060) -- this is the geometry-decomposition
half of the former "B-Rep and CSG to SDF converter": turning a sketch/wire/face into
2D profiles and 3-/4-corner patch specifications, with exact analytic decomposers for
planar, cylindrical and conical faces and a generic triangulated fallback for
everything else. `sdf_brep_primitive_detect.py` (pristine-primitive fast path) and
`sdf_csg_convert.py` (the public entry points, `shape_to_cage_field`/
`convert_csg_to_sdf`) both import from here; this module has no dependency on either.
"""
import math
import numpy as np
import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf2d.polygon import Sdf2dPolygon
from freecad.fields.core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
from freecad.fields.core.sdf.sdf_constants import DEGENERATE_AXIS_EPS
from freecad.fields.core.sdf.sdf_prism import extrude_frame

_DEFAULT_WELD_TOL = 1e-3


#: Segments per full revolution when a circular edge or a cylindrical/conical face is
#: broken into cubic patches. The patch *surface* is near-exact at any count (corners sit on
#: the true circle and the handles are the standard 4/3*tan(dtheta/4) arc approximation), but
#: the flat triangle soup used for the winding-number sign test is the inscribed polygon, so
#: this is what bounds the sign error near a curved wall: at 16 the inscribed/true area gap
#: is 0.08%, versus 3.9% at the 4 this used to be. Circular wire sampling and the
#: cylinder/cone decomposers must stay in lockstep or their shared seam will not weld.
_ARC_SEGMENTS = 16


def _vec_to_np(v):
    """Convert FreeCAD.Vector or iterable to (3,) float64 numpy array."""
    if hasattr(v, "x"):
        return np.array([float(v.x), float(v.y), float(v.z)], dtype=np.float64)
    return np.array([float(v[0]), float(v[1]), float(v[2])], dtype=np.float64)


def _straight_handles(p0, p1):
    """Compute standard straight-edge cubic Bezier handles at 1/3 and 2/3 chord."""
    h0 = p0 + (p1 - p0) / 3.0
    h1 = p0 + 2.0 * (p1 - p0) / 3.0
    return h0, h1


def _arc_to_bezier_segments_2d(center, radius, start_angle, end_angle):
    """Split a 2D circular arc into <= 90 deg sub-arcs and convert to cubic Bezier segments."""
    span = end_angle - start_angle
    n_splits = max(1, int(math.ceil(abs(span) / (math.pi * 0.5))))
    d_theta = span / n_splits
    segs = []
    for i in range(n_splits):
        t0 = start_angle + i * d_theta
        t1 = start_angle + (i + 1) * d_theta
        dt = t1 - t0
        k = (4.0 / 3.0) * math.tan(dt / 4.0)
        
        p0 = (center[0] + radius * math.cos(t0), center[1] + radius * math.sin(t0))
        p3 = (center[0] + radius * math.cos(t1), center[1] + radius * math.sin(t1))
        
        v0 = (-math.sin(t0), math.cos(t0))
        v1 = (-math.sin(t1), math.cos(t1))
        
        p1 = (p0[0] + k * radius * v0[0], p0[1] + k * radius * v0[1])
        p2 = (p3[0] - k * radius * v1[0], p3[1] - k * radius * v1[1])
        segs.append((p0, p1, p2, p3))
    return segs


def _convert_sketch_or_wire_to_2d_profile(obj_or_wire):
    """Extract a high-fidelity 2D profile (with curved arcs, circles, splines) from a Sketch or Wire."""
    if obj_or_wire is None:
        return None

    geo_list = getattr(obj_or_wire, "Geometry", None)
    if geo_list is None and hasattr(obj_or_wire, "Edges"):
        geo_list = obj_or_wire.Edges

    if not geo_list:
        return None

    segs = []
    has_curves = False

    for g in geo_list:
        tname = type(g).__name__
        has_sp_ep = (hasattr(g, "StartPoint") and hasattr(g, "EndPoint")) or (hasattr(g, "Vertexes") and len(g.Vertexes) >= 2)
        has_cr = hasattr(g, "Center") or hasattr(g, "Radius") or (hasattr(g, "Curve") and hasattr(g.Curve, "Radius"))

        if has_cr:
            has_curves = True
            c = getattr(g, "Center", None) or (g.Curve.Center if hasattr(g, "Curve") else FreeCAD.Vector(0,0,0))
            r = getattr(g, "Radius", None) or (g.Curve.Radius if hasattr(g, "Curve") else 1.0)
            center = (float(c.x), float(c.y))
            radius = float(r)

            if tname == "Circle" or getattr(g, "isClosed", lambda: False)():
                arc_segs = _arc_to_bezier_segments_2d(center, radius, 0.0, 2.0 * math.pi)
                segs.extend(arc_segs)
            elif has_sp_ep:
                sp = getattr(g, "StartPoint", None) or g.Vertexes[0].Point
                ep = getattr(g, "EndPoint", None) or g.Vertexes[1].Point
                t0 = math.atan2(sp.y - center[1], sp.x - center[0])
                t1 = math.atan2(ep.y - center[1], ep.x - center[0])
                
                fp = getattr(g, "FirstParameter", 0.0)
                lp = getattr(g, "LastParameter", 1.0)
                val_fn = getattr(g, "value", None) or getattr(g, "valueAt", None)
                if val_fn:
                    try:
                        mid_val = val_fn((fp + lp) * 0.5)
                        t_mid = math.atan2(mid_val.y - center[1], mid_val.x - center[0])
                        while t1 < t0:
                            t1 += 2.0 * math.pi
                        mid_angle = t_mid
                        while mid_angle < t0:
                            mid_angle += 2.0 * math.pi
                        if mid_angle > t1:
                            t0, t1 = t1, t0
                    except Exception:
                        pass

                arc_segs = _arc_to_bezier_segments_2d(center, radius, t0, t1)
                segs.extend(arc_segs)
            else:
                arc_segs = _arc_to_bezier_segments_2d(center, radius, 0.0, 2.0 * math.pi)
                segs.extend(arc_segs)

        elif has_sp_ep:
            sp = getattr(g, "StartPoint", None) or (g.Vertexes[0].Point if hasattr(g, "Vertexes") and g.Vertexes else None)
            ep = getattr(g, "EndPoint", None) or (g.Vertexes[1].Point if hasattr(g, "Vertexes") and len(g.Vertexes) > 1 else None)
            if sp is not None and ep is not None:
                p0 = (float(sp.x), float(sp.y))
                p3 = (float(ep.x), float(ep.y))
                p1 = (p0[0] + (p3[0] - p0[0]) / 3.0, p0[1] + (p3[1] - p0[1]) / 3.0)
                p2 = (p0[0] + 2.0 * (p3[0] - p0[0]) / 3.0, p0[1] + 2.0 * (p3[1] - p0[1]) / 3.0)
                segs.append((p0, p1, p2, p3))

        elif hasattr(g, "value") or hasattr(g, "valueAt"):
            has_curves = True
            fp = getattr(g, "FirstParameter", 0.0)
            lp = getattr(g, "LastParameter", 1.0)
            val_fn = getattr(g, "value", None) or getattr(g, "valueAt", None)
            n_samples = 4
            for s_idx in range(n_samples):
                ta = fp + (lp - fp) * (s_idx / n_samples)
                tb = fp + (lp - fp) * ((s_idx + 1) / n_samples)
                va = val_fn(ta)
                vb = val_fn(tb)
                v_h1 = val_fn(ta + (tb - ta) / 3.0)
                v_h2 = val_fn(ta + 2.0 * (tb - ta) / 3.0)
                p0 = (float(va.x), float(va.y))
                p3 = (float(vb.x), float(vb.y))
                p1 = (float(v_h1.x), float(v_h1.y))
                p2 = (float(v_h2.x), float(v_h2.y))
                segs.append((p0, p1, p2, p3))

    if not segs:
        return None

    if has_curves:
        return Sdf2dBezierCurve(segs)

    # Pure straight polygon
    poly_pts = [seg[0] for seg in segs]
    if len(poly_pts) >= 3:
        return Sdf2dPolygon(poly_pts)
    return Sdf2dBezierCurve(segs)


def _extract_wire_polygon(wire, tol=1e-3):
    """Extract ordered 3D vertices and optional edge handles from a TopoDS_Wire or edge list.

    Each edge is walked in the direction the *wire* traverses it: an edge flagged
    ``Reversed`` runs from ``LastParameter`` back to ``FirstParameter``. Taking
    ``Vertexes[0]`` as the start instead is wrong -- OCC orders an edge's vertices by the
    underlying curve's parameterisation, which is independent of that flag -- and on a
    plain box face that silently transposes two corners and drops a third, leaving the
    assembled shell open and its winding number meaningless.
    """
    edges = getattr(wire, "OrderedEdges", None) or getattr(wire, "Edges", None) or []
    if not edges:
        return [], []

    corners = []
    edge_handles = []

    for edge in edges:
        curve = getattr(edge, "Curve", None)
        c_type = type(curve).__name__ if curve else ""
        is_curved = (
            "Circle" in c_type or "BSpline" in c_type or "Bezier" in c_type or "Ellipse" in c_type
            or getattr(edge, "isClosed", lambda: False)()
        )
        fp = float(getattr(edge, "FirstParameter", 0.0))
        lp = float(getattr(edge, "LastParameter", 1.0))
        if getattr(edge, "Orientation", "Forward") == "Reversed":
            fp, lp = lp, fp
        param_span = abs(lp - fp)

        # For curved edges or circles, sample multiple segments (span <= 90 deg)
        if is_curved and param_span > 1e-4 and hasattr(edge, "valueAt"):
            if "Circle" in c_type or "Ellipse" in c_type:
                # Angular parameterisation, so segment count follows the swept angle and
                # matches what the cylinder/cone decomposers put on the shared seam.
                n_sub = max(2, int(math.ceil(param_span / (2.0 * math.pi) * _ARC_SEGMENTS)))
            else:
                n_sub = max(4, int(math.ceil(param_span / (math.pi * 0.5))))
            for s in range(n_sub):
                t0 = fp + (lp - fp) * (s / n_sub)
                t1 = fp + (lp - fp) * ((s + 1) / n_sub)
                v0 = _vec_to_np(edge.valueAt(t0))
                v1 = _vec_to_np(edge.valueAt(t1))
                h0 = _vec_to_np(edge.valueAt(t0 + (t1 - t0) / 3.0))
                h1 = _vec_to_np(edge.valueAt(t0 + 2.0 * (t1 - t0) / 3.0))
                corners.append(v0)
                edge_handles.append((h0, h1))
            continue

        # Standard straight edge, walked start -> end in wire order
        v_start = None
        v_end = None
        if hasattr(edge, "valueAt"):
            v_start = _vec_to_np(edge.valueAt(fp))
            v_end = _vec_to_np(edge.valueAt(lp))
        elif getattr(edge, "Vertexes", None):
            vtx = edge.Vertexes
            v_start = _vec_to_np(vtx[0].Point)
            if len(vtx) > 1:
                v_end = _vec_to_np(vtx[-1].Point)

        if v_start is None:
            continue

        corners.append(v_start)

        h0, h1 = None, None
        if hasattr(edge, "valueAt") and param_span > 0.0:
            t1 = fp + (lp - fp) / 3.0
            t2 = fp + 2.0 * (lp - fp) / 3.0
            h0 = _vec_to_np(edge.valueAt(t1))
            h1 = _vec_to_np(edge.valueAt(t2))

        if v_end is None and len(corners) > 1:
            v_end = corners[-1]
        if v_end is not None and (h0 is None or h1 is None):
            h0, h1 = _straight_handles(v_start, v_end)

        if h0 is not None and h1 is not None:
            edge_handles.append((h0, h1))

    return corners, edge_handles


def _triangulate_planar_polygon_with_holes(outer_corners, holes_corners, normal=None, weld_tol=_DEFAULT_WELD_TOL):
    """Triangulate a planar polygon with 1 or more interior hole loops via ear clipping.

    Outer boundary and hole wires are projected onto their common 2D plane,
    stitched into a single simple polygon via bridge seam edges, and triangulated
    into 3-corner Bézier patch specifications.
    """
    if not outer_corners or len(outer_corners) < 3:
        return []

    pts_outer = [_vec_to_np(p) for p in outer_corners]
    
    # Filter out consecutive duplicate points in outer boundary
    clean_outer = []
    for p in pts_outer:
        if not clean_outer or not np.allclose(p, clean_outer[-1], atol=weld_tol):
            clean_outer.append(p)
    if len(clean_outer) > 1 and np.allclose(clean_outer[0], clean_outer[-1], atol=weld_tol):
        clean_outer.pop()
    if len(clean_outer) < 3:
        return []

    # Clean hole boundaries
    clean_holes = []
    for hole in (holes_corners or []):
        pts_h = [_vec_to_np(p) for p in hole]
        ch = []
        for p in pts_h:
            if not ch or not np.allclose(p, ch[-1], atol=weld_tol):
                ch.append(p)
        if len(ch) > 1 and np.allclose(ch[0], ch[-1], atol=weld_tol):
            ch.pop()
        if len(ch) >= 3:
            clean_holes.append(ch)

    if not clean_holes:
        return _triangulate_polygon(clean_outer)

    # Compute planar orthonormal basis (u_axis, v_axis) and origin
    p0 = clean_outer[0]
    if normal is None:
        n_sum = np.array([0.0, 0.0, 0.0], dtype=np.float64)
        for i in range(len(clean_outer)):
            va = clean_outer[i] - p0
            vb = clean_outer[(i + 1) % len(clean_outer)] - p0
            n_sum += np.cross(va, vb)
        norm_len = np.linalg.norm(n_sum)
        if norm_len > DEGENERATE_AXIS_EPS:
            n_vec = n_sum / norm_len
        else:
            n_vec = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        n_vec = np.asarray(normal, dtype=np.float64)
        n_len = np.linalg.norm(n_vec)
        n_vec = n_vec / n_len if n_len > DEGENERATE_AXIS_EPS else np.array([0.0, 0.0, 1.0])

    # Find u_axis/v_axis perpendicular to n_vec, via the same right-handed
    # (u x v = n_vec) frame `sdf_prism.extrude_frame()` builds for extrusion walls.
    # Safe to reuse here: n_vec's direction already carries this polygon's own
    # winding (from the Newell-sum above), and to_2d()/signed_area_2d() below
    # re-derive CCW/CW purely from that -- there is no dependence on which
    # specific in-plane rotation of (u_axis, v_axis) extrude_frame happens to pick.
    _, u_axis, v_axis = extrude_frame(n_vec, p0)

    def to_2d(p3d):
        dp = p3d - p0
        return np.array([float(np.dot(dp, u_axis)), float(np.dot(dp, v_axis))], dtype=np.float64)

    def signed_area_2d(pts_2d):
        a = 0.0
        n = len(pts_2d)
        for i in range(n):
            j = (i + 1) % n
            a += pts_2d[i][0] * pts_2d[j][1] - pts_2d[j][0] * pts_2d[i][1]
        return 0.5 * a

    outer_2d = [to_2d(p) for p in clean_outer]
    if signed_area_2d(outer_2d) < 0.0:
        clean_outer.reverse()
        outer_2d.reverse()

    # Holes must be clockwise (negative signed area)
    holes_data = []
    for ch in clean_holes:
        h2d = [to_2d(p) for p in ch]
        if signed_area_2d(h2d) > 0.0:
            ch.reverse()
            h2d.reverse()
        holes_data.append((ch, h2d))

    # Sort holes by max X (U) coordinate descending
    holes_data.sort(key=lambda item: max(p[0] for p in item[1]), reverse=True)

    curr_3d = list(clean_outer)
    curr_2d = list(outer_2d)

    # Bridge each hole into the outer polygon
    for h_3d, h_2d in holes_data:
        # Find hole vertex with maximum X (U)
        h_max_idx = int(np.argmax([p[0] for p in h_2d]))
        h_max_pt = h_2d[h_max_idx]

        # Find visible outer vertex to connect to
        best_outer_idx = 0
        min_dist_sq = float("inf")
        for o_idx, o_pt in enumerate(curr_2d):
            if o_pt[0] >= h_max_pt[0] - 1e-6:
                d_sq = float((o_pt[0] - h_max_pt[0]) ** 2 + (o_pt[1] - h_max_pt[1]) ** 2)
                if d_sq < min_dist_sq:
                    min_dist_sq = d_sq
                    best_outer_idx = o_idx

        # If no outer vertex to the right, pick closest overall
        if math.isinf(min_dist_sq):
            for o_idx, o_pt in enumerate(curr_2d):
                d_sq = float((o_pt[0] - h_max_pt[0]) ** 2 + (o_pt[1] - h_max_pt[1]) ** 2)
                if d_sq < min_dist_sq:
                    min_dist_sq = d_sq
                    best_outer_idx = o_idx

        # Splice hole into current polygon
        hole_rotated_3d = h_3d[h_max_idx:] + h_3d[:h_max_idx + 1]
        hole_rotated_2d = h_2d[h_max_idx:] + h_2d[:h_max_idx + 1]

        curr_3d = curr_3d[:best_outer_idx + 1] + hole_rotated_3d + [curr_3d[best_outer_idx]] + curr_3d[best_outer_idx + 1:]
        curr_2d = curr_2d[:best_outer_idx + 1] + hole_rotated_2d + [curr_2d[best_outer_idx]] + curr_2d[best_outer_idx + 1:]

    # Ear-clipping triangulation on curr_2d / curr_3d
    triangles_3d = []
    indices = list(range(len(curr_2d)))

    def is_point_in_tri_2d(p, a, b, c):
        v0 = c - a
        v1 = b - a
        v2 = p - a
        dot00 = float(np.dot(v0, v0))
        dot01 = float(np.dot(v0, v1))
        dot02 = float(np.dot(v0, v2))
        dot11 = float(np.dot(v1, v1))
        dot12 = float(np.dot(v1, v2))
        inv_denom = 1.0 / (dot00 * dot11 - dot01 * dot01 + 1e-12)
        u = (dot11 * dot02 - dot01 * dot12) * inv_denom
        v = (dot00 * dot12 - dot01 * dot02) * inv_denom
        return (u >= -1e-6) and (v >= -1e-6) and (u + v <= 1.0 + 1e-6)

    max_iter = len(indices) * 4
    iter_count = 0
    while len(indices) > 3 and iter_count < max_iter:
        iter_count += 1
        ear_found = False
        n_idx = len(indices)
        for i in range(n_idx):
            prev_idx = indices[(i - 1) % n_idx]
            curr_idx = indices[i]
            next_idx = indices[(i + 1) % n_idx]

            pa = curr_2d[prev_idx]
            pb = curr_2d[curr_idx]
            pc = curr_2d[next_idx]

            # Convexity test (cross product > 0 for CCW)
            cross_z = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
            if cross_z <= 1e-9:
                continue

            # Check if any other point lies strictly inside triangle (pa, pb, pc)
            is_ear = True
            for k in range(n_idx):
                if k in ((i - 1) % n_idx, i, (i + 1) % n_idx):
                    continue
                pk = curr_2d[indices[k]]
                if is_point_in_tri_2d(pk, pa, pb, pc):
                    is_ear = False
                    break

            if is_ear:
                triangles_3d.append((curr_3d[prev_idx], curr_3d[curr_idx], curr_3d[next_idx]))
                indices.pop(i)
                ear_found = True
                break

        if not ear_found and len(indices) > 3:
            triangles_3d.append((curr_3d[indices[0]], curr_3d[indices[1]], curr_3d[indices[2]]))
            indices.pop(1)

    if len(indices) == 3:
        triangles_3d.append((curr_3d[indices[0]], curr_3d[indices[1]], curr_3d[indices[2]]))

    # Convert 3D triangles to patch specs
    specs = []
    for tri in triangles_3d:
        c0, c1, c2 = tri[0], tri[1], tri[2]
        h0_0, h0_1 = _straight_handles(c0, c1)
        h1_0, h1_1 = _straight_handles(c1, c2)
        h2_0, h2_1 = _straight_handles(c2, c0)
        specs.append({
            "corners": np.array([c0, c1, c2], dtype=np.float64),
            "handles": np.array([h0_0, h0_1, h1_0, h1_1, h2_0, h2_1], dtype=np.float64)
        })

    return specs


def _triangulate_polygon(corners, edge_handles=None):
    """Triangulate a polygon with N >= 3 vertices into tri/quad patch specs."""
    N = len(corners)
    if N == 3:
        handles = []
        for i in range(3):
            if edge_handles and i < len(edge_handles):
                h0, h1 = edge_handles[i]
            else:
                h0, h1 = _straight_handles(corners[i], corners[(i + 1) % 3])
            handles.append(h0)
            handles.append(h1)
        return [{
            "corners": np.array(corners, dtype=np.float64),
            "handles": np.array(handles, dtype=np.float64)
        }]

    if N == 4:
        handles = []
        for i in range(4):
            if edge_handles and i < len(edge_handles):
                h0, h1 = edge_handles[i]
            else:
                h0, h1 = _straight_handles(corners[i], corners[(i + 1) % 4])
            handles.append(h0)
            handles.append(h1)
        return [{
            "corners": np.array(corners, dtype=np.float64),
            "handles": np.array(handles, dtype=np.float64)
        }]

    specs = []
    c0 = corners[0]
    for i in range(1, N - 1):
        c1 = corners[i]
        c2 = corners[i + 1]
        tri_corners = [c0, c1, c2]
        
        h0_0, h0_1 = _straight_handles(c0, c1)
        if edge_handles and i < len(edge_handles):
            h1_0, h1_1 = edge_handles[i]
        else:
            h1_0, h1_1 = _straight_handles(c1, c2)
        if i + 1 == N - 1 and edge_handles and (N - 1) < len(edge_handles):
            h2_0, h2_1 = edge_handles[N - 1]
        else:
            h2_0, h2_1 = _straight_handles(c2, c0)

        tri_handles = [h0_0, h0_1, h1_0, h1_1, h2_0, h2_1]
        specs.append({
            "corners": np.array(tri_corners, dtype=np.float64),
            "handles": np.array(tri_handles, dtype=np.float64)
        })

    return specs


def _cylinder_face_to_patch_specs(face, weld_tol=_DEFAULT_WELD_TOL):
    """Decompose a cylindrical surface face into curved Coons patches with exact arc handles."""
    surf = getattr(face, "Surface", None)
    if surf is None:
        return []
    st = type(surf).__name__
    if not ("Cylinder" in st or "Cylindrical" in st or "Geom_CylindricalSurface" in st):
        return []

    center = _vec_to_np(surf.Center)
    axis = _vec_to_np(surf.Axis)
    axis_len = np.linalg.norm(axis)
    if axis_len > DEGENERATE_AXIS_EPS:
        axis = axis / axis_len
    else:
        axis = np.array([0.0, 0.0, 1.0])
    radius = float(surf.Radius)

    # NOT reusing sdf_prism.extrude_frame() here (CR-027): the `is_reversed` probe
    # below takes the dot of a real B-Rep-measured normal against `ex` specifically
    # -- swapping to extrude_frame()'s different seed-vector convention would rotate
    # `ex` to point a different way for the same axis, changing that dot product's
    # sign for some real cylinders and silently flipping face winding on them. The
    # polygon-triangulation basis a few hundred lines up has no such coupling (its
    # n_vec already carries the polygon's own winding, self-corrected below by
    # signed_area_2d) and was switched; this one and _cone_face_to_patch_specs's
    # identical pattern were left as-is rather than risk that.
    ref = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ex = np.cross(axis, ref)
    ex /= np.linalg.norm(ex)
    ey = np.cross(axis, ex)
    ey /= np.linalg.norm(ey)

    v_vals = []
    vertexes = getattr(face, "Vertexes", None) or []
    for v in vertexes:
        pt = _vec_to_np(v.Point)
        v_vals.append(float(np.dot(pt - center, axis)))

    if not v_vals:
        if hasattr(face, "ParameterRange"):
            pr = face.ParameterRange
            v_vals = [float(pr[2]), float(pr[3])]
        elif hasattr(face, "BoundBox"):
            bb = face.BoundBox
            v_vals = [float(np.dot(_vec_to_np((bb.XMin, bb.YMin, bb.ZMin)) - center, axis)),
                      float(np.dot(_vec_to_np((bb.XMax, bb.YMax, bb.ZMax)) - center, axis))]
        else:
            v_vals = [0.0, 10.0]

    vmin = min(v_vals)
    vmax = max(v_vals)
    if abs(vmax - vmin) < 1e-6:
        vmax = vmin + 1.0

    specs = []
    n_quads = _ARC_SEGMENTS
    d_theta = 2.0 * math.pi / n_quads
    k_arc = (4.0 / 3.0) * math.tan(d_theta / 4.0) * radius

    is_reversed = getattr(face, "Orientation", "") == "Reversed"
    if hasattr(face, "normalAt"):
        try:
            n_probe = face.normalAt(0.0, (vmin + vmax) * 0.5)
            if np.dot(_vec_to_np(n_probe), ex) < -0.1:
                is_reversed = True
        except Exception:
            pass

    for q in range(n_quads):
        t0 = q * d_theta
        t1 = (q + 1) * d_theta

        p0 = center + vmin * axis + radius * (math.cos(t0) * ex + math.sin(t0) * ey)
        p1 = center + vmin * axis + radius * (math.cos(t1) * ex + math.sin(t1) * ey)
        p2 = center + vmax * axis + radius * (math.cos(t1) * ex + math.sin(t1) * ey)
        p3 = center + vmax * axis + radius * (math.cos(t0) * ex + math.sin(t0) * ey)

        tan0 = -math.sin(t0) * ex + math.cos(t0) * ey
        tan1 = -math.sin(t1) * ex + math.cos(t1) * ey

        h01_0 = p0 + k_arc * tan0
        h01_1 = p1 - k_arc * tan1
        h12_0, h12_1 = _straight_handles(p1, p2)
        h23_0 = p2 - k_arc * tan1
        h23_1 = p3 + k_arc * tan0
        h30_0, h30_1 = _straight_handles(p3, p0)

        if is_reversed:
            corners = np.array([p3, p2, p1, p0], dtype=np.float64)
            h_rev = np.array([h23_1, h23_0, h12_1, h12_0, h01_1, h01_0, h30_1, h30_0], dtype=np.float64)
            specs.append({"corners": corners, "handles": h_rev})
        else:
            corners = np.array([p0, p1, p2, p3], dtype=np.float64)
            handles = np.array([h01_0, h01_1, h12_0, h12_1, h23_0, h23_1, h30_0, h30_1], dtype=np.float64)
            specs.append({"corners": corners, "handles": handles})

    return specs


def _cone_face_to_patch_specs(face, weld_tol=_DEFAULT_WELD_TOL):
    """Decompose a conical surface face into curved Coons patches with exact arc handles."""
    surf = getattr(face, "Surface", None)
    if surf is None:
        return []
    st = type(surf).__name__
    if not ("Cone" in st or "Conical" in st or "Geom_ConicalSurface" in st):
        return []

    center = _vec_to_np(surf.Center)
    axis = _vec_to_np(surf.Axis)
    axis_len = np.linalg.norm(axis)
    if axis_len > DEGENERATE_AXIS_EPS:
        axis = axis / axis_len
    else:
        axis = np.array([0.0, 0.0, 1.0])

    r_base = float(getattr(surf, "Radius", getattr(surf, "Radius1", 0.0)))
    semi_angle = float(getattr(surf, "SemiAngle", 0.0))

    ref = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    ex = np.cross(axis, ref)
    ex /= np.linalg.norm(ex)
    ey = np.cross(axis, ex)
    ey /= np.linalg.norm(ey)

    v_vals = []
    vertexes = getattr(face, "Vertexes", None) or []
    for v in vertexes:
        pt = _vec_to_np(v.Point)
        v_vals.append(float(np.dot(pt - center, axis)))

    if not v_vals:
        if hasattr(face, "ParameterRange"):
            pr = face.ParameterRange
            v_vals = [float(pr[2]), float(pr[3])]
        elif hasattr(face, "BoundBox"):
            bb = face.BoundBox
            v_vals = [float(np.dot(_vec_to_np((bb.XMin, bb.YMin, bb.ZMin)) - center, axis)),
                      float(np.dot(_vec_to_np((bb.XMax, bb.YMax, bb.ZMax)) - center, axis))]
        else:
            v_vals = [0.0, 10.0]

    vmin = min(v_vals)
    vmax = max(v_vals)
    if abs(vmax - vmin) < 1e-6:
        vmax = vmin + 1.0

    r_min = max(0.0, r_base + vmin * math.tan(semi_angle))
    r_max = max(0.0, r_base + vmax * math.tan(semi_angle))

    specs = []
    n_quads = _ARC_SEGMENTS
    d_theta = 2.0 * math.pi / n_quads

    for q in range(n_quads):
        t0 = q * d_theta
        t1 = (q + 1) * d_theta

        tan0 = -math.sin(t0) * ex + math.cos(t0) * ey
        tan1 = -math.sin(t1) * ex + math.cos(t1) * ey

        p0 = center + vmin * axis + r_min * (math.cos(t0) * ex + math.sin(t0) * ey)
        p1 = center + vmin * axis + r_min * (math.cos(t1) * ex + math.sin(t1) * ey)
        p2 = center + vmax * axis + r_max * (math.cos(t1) * ex + math.sin(t1) * ey)
        p3 = center + vmax * axis + r_max * (math.cos(t0) * ex + math.sin(t0) * ey)

        if r_max < 1e-6:
            apex = center + vmax * axis
            k_bottom = (4.0 / 3.0) * math.tan(d_theta / 4.0) * r_min
            h01_0 = p0 + k_bottom * tan0
            h01_1 = p1 - k_bottom * tan1
            h1a_0, h1a_1 = _straight_handles(p1, apex)
            ha0_0, ha0_1 = _straight_handles(apex, p0)
            specs.append({
                "corners": np.array([p0, p1, apex], dtype=np.float64),
                "handles": np.array([h01_0, h01_1, h1a_0, h1a_1, ha0_0, ha0_1], dtype=np.float64)
            })
        elif r_min < 1e-6:
            apex = center + vmin * axis
            k_top = (4.0 / 3.0) * math.tan(d_theta / 4.0) * r_max
            ha2_0, ha2_1 = _straight_handles(apex, p2)
            h23_0 = p2 - k_top * tan1
            h23_1 = p3 + k_top * tan0
            h3a_0, h3a_1 = _straight_handles(p3, apex)
            specs.append({
                "corners": np.array([apex, p2, p3], dtype=np.float64),
                "handles": np.array([ha2_0, ha2_1, h23_0, h23_1, h3a_0, h3a_1], dtype=np.float64)
            })
        else:
            k_bottom = (4.0 / 3.0) * math.tan(d_theta / 4.0) * r_min
            k_top = (4.0 / 3.0) * math.tan(d_theta / 4.0) * r_max

            h01_0 = p0 + k_bottom * tan0
            h01_1 = p1 - k_bottom * tan1
            h12_0, h12_1 = _straight_handles(p1, p2)
            h23_0 = p2 - k_top * tan1
            h23_1 = p3 + k_top * tan0
            h30_0, h30_1 = _straight_handles(p3, p0)

            specs.append({
                "corners": np.array([p0, p1, p2, p3], dtype=np.float64),
                "handles": np.array([h01_0, h01_1, h12_0, h12_1, h23_0, h23_1, h30_0, h30_1], dtype=np.float64)
            })

    return specs


def face_to_patch_specs(face, weld_tol=_DEFAULT_WELD_TOL):
    """Decompose a single TopoDS_Face or surface into a list of patch specs with exact curvature."""
    surf = getattr(face, "Surface", None)
    st = type(surf).__name__ if surf else ""

    if "Cylinder" in st or "Cylindrical" in st or "Geom_CylindricalSurface" in st:
        cyl_specs = _cylinder_face_to_patch_specs(face, weld_tol=weld_tol)
        if cyl_specs:
            return cyl_specs

    if "Cone" in st or "Conical" in st or "Geom_ConicalSurface" in st:
        cone_specs = _cone_face_to_patch_specs(face, weld_tol=weld_tol)
        if cone_specs:
            return cone_specs

    # Everything below approximates the face by its boundary wire, which is only faithful
    # for a planar face. A curved surface with no decomposer of its own (sphere, general
    # B-spline, surface of revolution) therefore comes out as a flat lid over its own
    # outline: the field it yields is wrong in the interior, not merely coarse. Say so --
    # a silently wrong solid is far worse than a visible gap.
    if st and not ("Plane" in st or "Geom_Plane" in st):
        fld_logger.warn(
            "face_to_patch_specs: no exact decomposition for surface type %r; approximating "
            "it by its boundary wire. The resulting solid will be wrong wherever that face "
            "bulges away from its own outline." % st)

    # Check for multiple wire loops (outer boundary + interior holes)
    wires = getattr(face, "Wires", None) or []
    if len(wires) > 1:
        outer_w = getattr(face, "OuterWire", None) or wires[0]
        outer_corners, _ = _extract_wire_polygon(outer_w, tol=weld_tol)
        hole_corners_list = []
        for w in wires:
            if hasattr(w, "isSame") and w.isSame(outer_w):
                continue
            if w == outer_w:
                continue
            hc, _ = _extract_wire_polygon(w, tol=weld_tol)
            if len(hc) >= 3:
                hole_corners_list.append(hc)

        if outer_corners and hole_corners_list:
            norm = None
            if hasattr(face, "normalAt"):
                try:
                    nv = face.normalAt(0.0, 0.0)
                    norm = np.array([float(nv.x), float(nv.y), float(nv.z)], dtype=np.float64)
                except Exception:
                    pass
            hole_specs = _triangulate_planar_polygon_with_holes(outer_corners, hole_corners_list, normal=norm, weld_tol=weld_tol)
            if hole_specs:
                return hole_specs

    outer_wire = getattr(face, "OuterWire", None) or getattr(face, "Wire", None) or face
    corners, edge_handles = _extract_wire_polygon(outer_wire, tol=weld_tol)

    if len(corners) >= 3:
        valid_corners = []
        valid_handles = []
        for idx, c in enumerate(corners):
            if not any(np.allclose(c, vc, atol=weld_tol) for vc in valid_corners):
                valid_corners.append(c)
                if edge_handles and idx < len(edge_handles):
                    valid_handles.append(edge_handles[idx])
            elif len(valid_corners) > 0 and not np.allclose(c, valid_corners[-1], atol=weld_tol):
                valid_corners.append(c)
                if edge_handles and idx < len(edge_handles):
                    valid_handles.append(edge_handles[idx])

        if len(valid_corners) >= 3:
            return _triangulate_polygon(valid_corners, valid_handles)

    vertexes = getattr(face, "Vertexes", None) or []
    if len(vertexes) >= 3:
        pts = [_vec_to_np(v.Point) for v in vertexes]
        unique_pts = []
        for p in pts:
            if not any(np.allclose(p, up, atol=weld_tol) for up in unique_pts):
                unique_pts.append(p)
        if len(unique_pts) >= 3:
            return _triangulate_polygon(unique_pts)

    triangles = getattr(face, "Triangles", None) or []
    if triangles:
        specs = []
        for tri in triangles:
            if len(tri) == 3:
                tri_pts = [_vec_to_np(p) for p in tri]
                specs.extend(_triangulate_polygon(tri_pts))
        if specs:
            return specs

    return []


