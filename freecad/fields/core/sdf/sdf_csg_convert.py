# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""B-Rep and CSG to SDF converter, public entry points.

Split out of sdf_brep_converter.py (CR-060) -- this is the orchestration half of the
former "B-Rep and CSG to SDF converter": `shape_to_cage_field` (any TopoDS_Shape/solid
-> SdfField, trying the exact-primitive fast path before falling back to full patch
decomposition) and `convert_csg_to_sdf` (recursive FreeCAD CSG-tree/PartDesign-feature
walk, calling `shape_to_cage_field` at its own B-Rep fallback leaf). These are what
external callers actually want -- `cmd_sdf_import.py` and the test suite both import
from this module by name.

Converts arbitrary OpenCASCADE CAD solids (primitives, sketch extrusions with curved
arcs and splines, revolutions, edge fillets and roundovers, quadrics, patterns,
boolean trees, and STEP imports) into Fields SdfField trees.
"""
import math
import FreeCAD

from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf.box import SdfBoxField
from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
from freecad.fields.core.sdf.sdf.torus import SdfTorusField
from freecad.fields.core.sdf.sdf.plane import SdfPlaneField
from freecad.fields.core.sdf.sdf.cage import SdfCageField
from freecad.fields.core.sdf.sdf.array import SdfArrayField
from freecad.fields.core.sdf.sdf.offset import SdfOffsetField
from freecad.fields.core.sdf.sdf.edge_fillet import SdfEdgeFilletField
from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
from freecad.fields.core.sdf.sdf_revolution import SdfRevolutionField
from freecad.fields.core.sdf.sdf_composer import (
    UnionField, SubtractionField, IntersectionField,
)
from freecad.fields.core.sdf.sdf_brep_decompose import (
    _DEFAULT_WELD_TOL, face_to_patch_specs, _convert_sketch_or_wire_to_2d_profile,
)
from freecad.fields.core.sdf.sdf_brep_primitive_detect import detect_exact_primitive_shape


def shape_to_cage_field(shape, weld_tol=_DEFAULT_WELD_TOL, sign_mode="winding", placement=None):
    """Convert an arbitrary TopoDS_Shape / solid into an SdfField."""
    # Fast path: detect exact pristine primitive solids
    prim = detect_exact_primitive_shape(shape, placement=placement)
    if prim is not None:
        return prim

    faces = getattr(shape, "Faces", None) or []
    if not faces:
        if hasattr(shape, "OuterWire") or hasattr(shape, "Vertexes"):
            faces = [shape]

    all_specs = []
    for face in faces:
        face_specs = face_to_patch_specs(face, weld_tol=weld_tol)
        all_specs.extend(face_specs)

    if not all_specs:
        fld_logger.warn("shape_to_cage_field: No patch specifications could be extracted from shape.")
        return None

    return SdfCageField.from_patches(
        all_specs,
        weld_tol=weld_tol,
        sign_mode=sign_mode,
        placement=placement
    )


def _new_planar_faces(shape, base_shape):
    """Planar faces of `shape` that do not lie in the plane of any planar face of
    `base_shape`. Those are the faces the blend feature actually added -- a
    topological test, not a guess from normal directions."""
    def planes_of(sh):
        out = []
        for f in getattr(sh, "Faces", []) or []:
            s = getattr(f, "Surface", None)
            if s is None or "Plane" not in type(s).__name__:
                continue
            try:
                n = f.normalAt(0.0, 0.0)
                q = f.CenterOfMass
            except Exception:
                continue
            ln = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
            if ln < 1e-9:
                continue
            n = (n.x / ln, n.y / ln, n.z / ln)
            out.append((f, n, n[0] * q.x + n[1] * q.y + n[2] * q.z))
        return out

    base_planes = planes_of(base_shape) if base_shape is not None else []
    new_faces = []
    for f, n, off in planes_of(shape):
        same = False
        for _bf, bn, boff in base_planes:
            if abs(abs(n[0] * bn[0] + n[1] * bn[1] + n[2] * bn[2]) - 1.0) < 1e-6 \
               and abs(abs(off) - abs(boff)) < 1e-6:
                same = True
                break
        if not same:
            new_faces.append(f)
    return new_faces


def convert_csg_to_sdf(obj, max_depth=50, weld_tol=_DEFAULT_WELD_TOL, sign_mode="winding"):
    """Recursively convert any FreeCAD CSG tree, PartDesign feature, or B-Rep object into an SdfField."""
    if obj is None or max_depth <= 0:
        return None

    # Check if already a Fields object with an SdfField
    proxy = getattr(obj, "Proxy", None)
    if proxy is not None:
        if hasattr(proxy, "get_sdf_field"):
            existing_field = proxy.get_sdf_field(obj)
            if existing_field is not None:
                return existing_field
        elif hasattr(proxy, "SdfField") and proxy.SdfField is not None:
            return proxy.SdfField

    type_id = getattr(obj, "TypeId", "")
    placement = getattr(obj, "Placement", None) or FreeCAD.Placement()

    # PartDesign::Body -> follow Tip or active features
    if type_id == "PartDesign::Body" or hasattr(obj, "Tip"):
        tip = getattr(obj, "Tip", None)
        if tip is not None and tip != obj:
            return convert_csg_to_sdf(tip, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        group = getattr(obj, "Group", []) or []
        if group:
            return convert_csg_to_sdf(group[-1], max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)

    # PartDesign::Pad / Part::Extrusion
    if type_id in ("PartDesign::Pad", "Part::Extrusion"):
        length_val = getattr(obj, "Length", None)
        length = float(length_val.Value if hasattr(length_val, "Value") else length_val) if length_val is not None else 10.0
        profile_ref = getattr(obj, "Profile", None)
        if isinstance(profile_ref, tuple) and profile_ref:
            sketch = profile_ref[0]
        else:
            sketch = getattr(obj, "Base", profile_ref)

        profile_2d = _convert_sketch_or_wire_to_2d_profile(sketch)
        if profile_2d is not None:
            sk_pl = getattr(sketch, "Placement", placement)
            norm = sk_pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            center_pl = FreeCAD.Placement(sk_pl.Base + norm * (length * 0.5), sk_pl.Rotation)
            return SdfExtrusionField(profile_2d, height=length, placement=center_pl)

    # PartDesign::Pocket / PartDesign::Hole / PartDesign::Groove
    if type_id in ("PartDesign::Pocket", "PartDesign::Hole", "PartDesign::Groove"):
        base_feature = getattr(obj, "BaseFeature", None) or getattr(obj, "Base", None)
        length_val = getattr(obj, "Length", None) or getattr(obj, "Depth", None)
        length = float(length_val.Value if hasattr(length_val, "Value") else length_val) if length_val is not None else 10.0
        profile_ref = getattr(obj, "Profile", None)
        sketch = profile_ref[0] if isinstance(profile_ref, tuple) and profile_ref else getattr(obj, "Base", profile_ref)
        profile_2d = _convert_sketch_or_wire_to_2d_profile(sketch)
        if profile_2d is not None and base_feature is not None:
            sk_pl = getattr(sketch, "Placement", placement)
            norm = sk_pl.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            center_pl = FreeCAD.Placement(sk_pl.Base + norm * (length * 0.5), sk_pl.Rotation)
            tool_field = SdfExtrusionField(profile_2d, height=length, placement=center_pl)
            base_field = convert_csg_to_sdf(base_feature, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
            if base_field is not None:
                return SubtractionField(base_field, tool_field)

    # PartDesign::Revolution / Part::Revolution
    if type_id in ("PartDesign::Revolution", "Part::Revolution"):
        profile_ref = getattr(obj, "Profile", None)
        sketch = profile_ref[0] if isinstance(profile_ref, tuple) and profile_ref else getattr(obj, "Base", profile_ref)
        profile_2d = _convert_sketch_or_wire_to_2d_profile(sketch)
        if profile_2d is not None:
            return SdfRevolutionField(profile_2d, offset=10.0, placement=getattr(sketch, "Placement", placement))

    # PartDesign::Fillet / Part::Fillet
    if type_id in ("PartDesign::Fillet", "Part::Fillet"):
        base_ref = getattr(obj, "Base", None) or getattr(obj, "BaseFeature", None)
        if isinstance(base_ref, tuple) and base_ref:
            base_obj = base_ref[0]
        else:
            base_obj = base_ref
        radius_val = getattr(obj, "Radius", None)
        radius = float(radius_val.Value if hasattr(radius_val, "Value") else radius_val) if radius_val is not None else 1.0
        base_field = convert_csg_to_sdf(base_obj, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)

        # The fillet faces are the cylinders this feature added: match on the
        # feature's own radius rather than a magic size cap, so a large cylindrical
        # wall in the base solid is never mistaken for a blend.
        shape = getattr(obj, "Shape", None)
        if shape is not None and hasattr(shape, "Faces") and base_field is not None:
            cyl_faces = [
                f for f in shape.Faces
                if "Cylinder" in type(getattr(f, "Surface", None)).__name__
                and abs(float(f.Surface.Radius) - radius) < 1e-6
            ]
            if cyl_faces:
                return SdfEdgeFilletField(base_field, shape=shape, fillet_faces=cyl_faces)
            fld_logger.warn(
                "convert_csg_to_sdf: '%s' has no cylindrical face at r=%.4g; edge left sharp."
                % (getattr(obj, "Label", "Fillet"), radius))
        if base_field is not None:
            return base_field

    # PartDesign::Chamfer / Part::Chamfer
    if type_id in ("PartDesign::Chamfer", "Part::Chamfer"):
        base_ref = getattr(obj, "Base", None) or getattr(obj, "BaseFeature", None)
        if isinstance(base_ref, tuple) and base_ref:
            base_obj = base_ref[0]
        else:
            base_obj = base_ref
        size_val = getattr(obj, "Size", None) or getattr(obj, "Size1", None)
        size = float(size_val.Value if hasattr(size_val, "Value") else size_val) if size_val is not None else 1.0
        base_field = convert_csg_to_sdf(base_obj, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)

        shape = getattr(obj, "Shape", None)
        if shape is not None and hasattr(shape, "Faces") and base_field is not None:
            chamfer_faces = _new_planar_faces(shape, getattr(base_obj, "Shape", None))
            if chamfer_faces:
                return SdfEdgeFilletField(base_field, shape=shape, chamfer_faces=chamfer_faces)
            fld_logger.warn(
                "convert_csg_to_sdf: '%s' contributed no new planar face; edge left sharp."
                % getattr(obj, "Label", "Chamfer"))
        if base_field is not None:
            return base_field

    # PartDesign::Draft / PartDesign::Thickness
    if type_id in ("PartDesign::Draft", "PartDesign::Thickness"):
        base_ref = getattr(obj, "Base", None) or getattr(obj, "BaseFeature", None)
        if isinstance(base_ref, tuple) and base_ref:
            base_obj = base_ref[0]
        else:
            base_obj = base_ref
        val = getattr(obj, "Value", None) or getattr(obj, "Thickness", None)
        offset = float(val.Value if hasattr(val, "Value") else val) if val is not None else 1.0
        base_field = convert_csg_to_sdf(base_obj, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        if base_field is not None:
            return SdfOffsetField(base_field, offset)

    # PartDesign::LinearPattern / PartDesign::PolarPattern
    if type_id in ("PartDesign::LinearPattern", "PartDesign::PolarPattern"):
        base_feature = getattr(obj, "Originals", [None])[0] if hasattr(obj, "Originals") and obj.Originals else getattr(obj, "Base", None)
        base_field = convert_csg_to_sdf(base_feature, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        occ_val = getattr(obj, "Occurrences", 2)
        count = int(occ_val.Value if hasattr(occ_val, "Value") else occ_val)
        length_val = getattr(obj, "Length", 50.0)
        span = float(length_val.Value if hasattr(length_val, "Value") else length_val)
        spacing = span / max(1, count - 1)
        if base_field is not None:
            return SdfArrayField(base_field, mode="linear", counts=(count, 1, 1), spacing=(spacing, 20.0, 20.0))

    # PartDesign::Mirrored / Part::Mirroring
    if type_id in ("PartDesign::Mirrored", "Part::Mirroring"):
        base_feature = getattr(obj, "Originals", [None])[0] if hasattr(obj, "Originals") and obj.Originals else getattr(obj, "Source", None)
        return convert_csg_to_sdf(base_feature, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)

    # Part::Box
    if type_id == "Part::Box" or (hasattr(obj, "Length") and hasattr(obj, "Width") and hasattr(obj, "Height")):
        L = float(getattr(obj, "Length"))
        W = float(getattr(obj, "Width"))
        H = float(getattr(obj, "Height"))
        center = FreeCAD.Vector(L * 0.5, W * 0.5, H * 0.5)
        size = FreeCAD.Vector(L, W, H)
        return SdfBoxField(center=center, size=size, placement=placement)

    # Part::Sphere
    if type_id == "Part::Sphere" or (hasattr(obj, "Radius") and hasattr(obj, "Angle1") and hasattr(obj, "Angle2")):
        R = float(getattr(obj, "Radius"))
        return SdfSphereField(center=FreeCAD.Vector(0, 0, 0), radius=R, placement=placement)

    # Part::Cylinder
    if type_id == "Part::Cylinder" or (hasattr(obj, "Radius") and hasattr(obj, "Height") and not hasattr(obj, "Length")):
        R = float(getattr(obj, "Radius"))
        H = float(getattr(obj, "Height"))
        return SdfCylinderField(base_center=FreeCAD.Vector(0,0,0), axis=FreeCAD.Vector(0,0,1), radius=R, height=H, placement=placement)

    # Part::Torus
    if type_id == "Part::Torus" or (hasattr(obj, "Radius1") and hasattr(obj, "Radius2")):
        R1 = float(getattr(obj, "Radius1"))
        R2 = float(getattr(obj, "Radius2"))
        return SdfTorusField(center=FreeCAD.Vector(0,0,0), major_radius=R1, tube_radius=R2, placement=placement)

    # Part::Plane
    if type_id == "Part::Plane":
        norm = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        return SdfPlaneField(normal=norm, origin=placement.Base)

    # Part::Cut
    if type_id == "Part::Cut" or (hasattr(obj, "Base") and hasattr(obj, "Tool") and "Cut" in getattr(obj, "Name", "")):
        f_base = convert_csg_to_sdf(getattr(obj, "Base", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        f_tool = convert_csg_to_sdf(getattr(obj, "Tool", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        if f_base is not None and f_tool is not None:
            return SubtractionField(f_base, f_tool)

    # Part::Fuse
    if type_id == "Part::Fuse":
        f_base = convert_csg_to_sdf(getattr(obj, "Base", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        f_tool = convert_csg_to_sdf(getattr(obj, "Tool", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        if f_base is not None and f_tool is not None:
            return UnionField(f_base, f_tool)

    # Part::Common
    if type_id == "Part::Common":
        f_base = convert_csg_to_sdf(getattr(obj, "Base", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        f_tool = convert_csg_to_sdf(getattr(obj, "Tool", None), max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
        if f_base is not None and f_tool is not None:
            return IntersectionField(f_base, f_tool)

    # Part::MultiFuse / MultiCommon / Part::Compound
    if type_id in ("Part::MultiFuse", "Part::MultiCommon", "Part::Compound") or hasattr(obj, "Shapes"):
        shapes = getattr(obj, "Shapes", []) or getattr(obj, "Links", []) or []
        child_fields = [
            convert_csg_to_sdf(s, max_depth=max_depth - 1, weld_tol=weld_tol, sign_mode=sign_mode)
            for s in shapes
        ]
        child_fields = [f for f in child_fields if f is not None]
        if child_fields:
            if type_id == "Part::MultiCommon":
                res = child_fields[0]
                for cf in child_fields[1:]:
                    res = IntersectionField(res, cf)
                return res
            else:
                from freecad.fields.core.sdf.sdf_boolean_compose import _fold_union
                return _fold_union(child_fields)

    # General B-Rep fallback
    shape = getattr(obj, "Shape", obj)
    return shape_to_cage_field(shape, weld_tol=weld_tol, sign_mode=sign_mode, placement=placement)
