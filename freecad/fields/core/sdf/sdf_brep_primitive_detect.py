# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Pristine-CAD-primitive detection: TopoDS_Shape -> exact analytic SdfField, or None.

Split out of sdf_brep_converter.py (CR-060) -- the fast-path half of the former
"B-Rep and CSG to SDF converter": recognizing a Box/Sphere/Cylinder/Torus by its
face census and volume, and returning the exact analytic field instead of falling
through to the general Bezier/Coons patch decomposition in sdf_brep_decompose.py.
"""
import math
import numpy as np
import FreeCAD

from freecad.fields.core.sdf.sdf.box import SdfBoxField
from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
from freecad.fields.core.sdf.sdf.torus import SdfTorusField
from freecad.fields.core.sdf.sdf_constants import DEGENERATE_AXIS_EPS
from freecad.fields.core.sdf.sdf_brep_decompose import _vec_to_np


def _volume_matches(shape, vol, rel_tol=1e-4):
    """True if `shape` really is the primitive whose analytic volume is `vol`.

    A face-type census cannot tell a pristine primitive from a modified one: drill a hole
    through the side of a cylinder and the result still has exactly two planar faces and,
    counting the bore, two cylindrical ones, so the census matches and the bore is silently
    discarded. Comparing volumes is what actually rejects a trimmed or pierced body.

    Shapes with no computable volume (shells, and the duck-typed faces used in tests) fall
    back to the census alone -- there is nothing to compare against.
    """
    try:
        actual = float(shape.Volume)
    except Exception:
        return True
    if actual <= 0.0:
        return True
    return abs(actual - vol) <= rel_tol * max(actual, vol)


def detect_exact_primitive_shape(shape, placement=None):
    """Detect if a TopoDS_Shape / solid is a pristine untrimmed CAD primitive (Box, Sphere, Cylinder, Torus).

    Returns an exact analytical SdfField (SdfBoxField, SdfSphereField, SdfCylinderField, SdfTorusField)
    with exact dimensions and placement, or None if the shape is general / modified / freeform.
    """
    if shape is None:
        return None

    faces = getattr(shape, "Faces", None) or []
    if not faces:
        return None

    n_faces = len(faces)

    # --- 1. SPHERE ---
    sphere_surfs = [
        getattr(f, "Surface", None) for f in faces
        if getattr(f, "Surface", None) is not None
        and ("Sphere" in type(getattr(f, "Surface", None)).__name__ or "Geom_SphericalSurface" in type(getattr(f, "Surface", None)).__name__)
    ]
    if len(sphere_surfs) in (1, 2) and len(sphere_surfs) == n_faces:
        s0 = sphere_surfs[0]
        r = float(getattr(s0, "Radius", 0.0))
        c = getattr(s0, "Center", FreeCAD.Vector(0, 0, 0))
        if r > 1e-6 and _volume_matches(shape, (4.0 / 3.0) * math.pi * r ** 3):
            return SdfSphereField(center=FreeCAD.Vector(c.x, c.y, c.z), radius=r)

    # --- 2. TOROID / TORUS ---
    torus_surfs = [
        getattr(f, "Surface", None) for f in faces
        if getattr(f, "Surface", None) is not None
        and ("Toroid" in type(getattr(f, "Surface", None)).__name__
             or "Torus" in type(getattr(f, "Surface", None)).__name__
             or "Geom_ToroidalSurface" in type(getattr(f, "Surface", None)).__name__)
    ]
    if len(torus_surfs) in (1, 2) and len(torus_surfs) == n_faces:
        t0 = torus_surfs[0]
        r_maj = float(getattr(t0, "MajorRadius", getattr(t0, "Radius1", 0.0)))
        r_min = float(getattr(t0, "MinorRadius", getattr(t0, "Radius2", 0.0)))
        c = getattr(t0, "Center", FreeCAD.Vector(0, 0, 0))
        ax = getattr(t0, "Axis", FreeCAD.Vector(0, 0, 1))
        if r_maj > 1e-6 and r_min > 1e-6 and \
                _volume_matches(shape, 2.0 * math.pi ** 2 * r_maj * r_min ** 2):
            rot = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), FreeCAD.Vector(ax.x, ax.y, ax.z))
            pl = FreeCAD.Placement(FreeCAD.Vector(c.x, c.y, c.z), rot)
            return SdfTorusField(center=FreeCAD.Vector(0, 0, 0), major_radius=r_maj, tube_radius=r_min, placement=pl)

    # --- 3. CYLINDER ---
    # Strictly check Cylinder surface names so Cones are NOT claimed
    cyl_surfs = [
        getattr(f, "Surface", None) for f in faces
        if getattr(f, "Surface", None) is not None
        and ("Cylinder" in type(getattr(f, "Surface", None)).__name__
             or "Cylindrical" in type(getattr(f, "Surface", None)).__name__
             or "Geom_CylindricalSurface" in type(getattr(f, "Surface", None)).__name__)
    ]
    plane_faces = [
        f for f in faces
        if getattr(f, "Surface", None) is not None
        and ("Plane" in type(getattr(f, "Surface", None)).__name__
             or "Geom_Plane" in type(getattr(f, "Surface", None)).__name__)
    ]
    if len(cyl_surfs) in (1, 2) and len(plane_faces) == 2 and len(cyl_surfs) + len(plane_faces) == n_faces:
        c0 = cyl_surfs[0]
        r = float(getattr(c0, "Radius", 0.0))
        axis_v = getattr(c0, "Axis", FreeCAD.Vector(0, 0, 1))
        axis = _vec_to_np(axis_v)
        la = np.linalg.norm(axis)
        if la > DEGENERATE_AXIS_EPS:
            axis = axis / la
        else:
            axis = np.array([0.0, 0.0, 1.0])

        p1 = _vec_to_np(plane_faces[0].CenterOfMass if hasattr(plane_faces[0], "CenterOfMass") else getattr(plane_faces[0].Surface, "Center", (0, 0, 0)))
        p2 = _vec_to_np(plane_faces[1].CenterOfMass if hasattr(plane_faces[1], "CenterOfMass") else getattr(plane_faces[1].Surface, "Center", (0, 0, 0)))

        h = abs(float(np.dot(p2 - p1, axis)))
        if h > 1e-6 and r > 1e-6 and _volume_matches(shape, math.pi * r ** 2 * h):
            proj1 = float(np.dot(p1, axis))
            proj2 = float(np.dot(p2, axis))
            base_center_np = p1 if proj1 < proj2 else p2
            base_center_v = FreeCAD.Vector(base_center_np[0], base_center_np[1], base_center_np[2])
            axis_vec = FreeCAD.Vector(axis[0], axis[1], axis[2])
            return SdfCylinderField(base_center=base_center_v, axis=axis_vec, radius=r, height=h)

    # --- 4. BOX ---
    # 6 planar faces with 3 pairs of parallel opposing planes
    verts = getattr(shape, "Vertexes", None) or []
    if n_faces == 6 and len(plane_faces) == 6 and (len(verts) == 8 or len(verts) == 0):
        face_normals = []
        face_centers = []
        for f in plane_faces:
            try:
                n = None
                if hasattr(f, "normalAt"):
                    try:
                        n = f.normalAt(0.0, 0.0)
                    except TypeError:
                        n = f.normalAt(f, 0.0, 0.0)
                if n is None and hasattr(f, "Surface") and hasattr(f.Surface, "Axis"):
                    n = f.Surface.Axis
                if n is not None:
                    n_np = _vec_to_np(n)
                    ln = np.linalg.norm(n_np)
                    if ln > 1e-8:
                        n_np /= ln
                    face_normals.append(n_np)
                c = f.CenterOfMass if hasattr(f, "CenterOfMass") else _vec_to_np(getattr(f.Surface, "Center", (0, 0, 0)))
                face_centers.append(_vec_to_np(c))
            except Exception:
                pass

        if len(face_normals) == 6:
            pairs = []
            used = set()
            for i in range(6):
                if i in used:
                    continue
                for j in range(i + 1, 6):
                    if j in used:
                        continue
                    if abs(np.dot(face_normals[i], face_normals[j]) + 1.0) < 1e-2:
                        dist = abs(float(np.dot(face_centers[i] - face_centers[j], face_normals[i])))
                        pairs.append((dist, face_normals[i]))
                        used.add(i)
                        used.add(j)
                        break

            if len(pairs) == 3:
                # 3 orthogonal pairs: axes e_x, e_y, e_z and lengths L, W, H
                ex = pairs[0][1]
                ey = pairs[1][1]
                ez = np.cross(ex, ey)
                ez_len = np.linalg.norm(ez)
                if ez_len > 1e-4:
                    ez /= ez_len
                    ey = np.cross(ez, ex)
                    ey /= np.linalg.norm(ey)

                    L = pairs[0][0]
                    W = pairs[1][0]
                    H = pairs[2][0]

                    if not _volume_matches(shape, L * W * H):
                        return None

                    all_c = np.mean(face_centers, axis=0)
                    center_v = FreeCAD.Vector(all_c[0], all_c[1], all_c[2])
                    size_v = FreeCAD.Vector(L, W, H)

                    rot = FreeCAD.Rotation(
                        FreeCAD.Vector(ex[0], ex[1], ex[2]),
                        FreeCAD.Vector(ey[0], ey[1], ey[2]),
                        FreeCAD.Vector(ez[0], ez[1], ez[2]),
                        'ZXY'
                    )
                    pl = FreeCAD.Placement(center_v, rot)
                    return SdfBoxField(center=FreeCAD.Vector(0, 0, 0), size=size_v, placement=pl)

    return None


