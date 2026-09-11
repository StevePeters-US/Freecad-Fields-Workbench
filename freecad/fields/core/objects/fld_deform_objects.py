# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.objects.fld_view_provider import FldViewProvider
from freecad.fields.core.objects.fld_modifier_proxy_base import FldModifierProxyBase

_AXES = ["X", "Y", "Z"]


def _same_array(a, b):
    """True when two optional (n,3) arrays hold the same values."""
    import numpy as np

    if a is None or b is None:
        return a is None and b is None
    return np.array_equal(np.asarray(a), np.asarray(b))


def _same_prefix(source, base_field, exts):
    """True when `source` is exactly the stack of `base_field` + `exts`.

    A cached extrusion may only be reused while the material it was swept out
    of is unchanged, so this compares the prefix element by element on object
    identity: editing a lower lobe rebuilds it, which invalidates every lobe
    above. The stack field is rebuilt each pass, so identity of the stack
    itself would never match -- its contents are what matter.
    """
    from freecad.fields.core.sdf.sdf_face_extrude import SdfExtrusionStackField

    if not exts:
        return source is base_field
    return (isinstance(source, SdfExtrusionStackField)
            and source.base is base_field
            and len(source.extrusions) == len(exts)
            and all(a is b for a, b in zip(source.extrusions, exts)))


def _create_modifier(proxy_cls, name: str, source_obj) -> object:
    """Shared body of `create_twist_modifier`/`create_bend_modifier`/
    `create_lattice_modifier`/`create_array_modifier` (CR-029): addObject a
    Part::FeaturePython, wrap it with `proxy_cls`, give it a ViewProvider, link
    `Source`, and touch it. `create_deform_cage_modifier` is NOT one of these --
    its `doc`/optional-`name` signature and pre-checks differ enough that it stays
    its own function.

    The synchronous addObject()/recompute() contract here is intentional (per the
    project's own review guardrails) and unchanged by this dedup -- only the
    repeated body is shared, not the caller-recompute timing.
    """
    doc = FreeCAD.activeDocument()
    obj = doc.addObject("Part::FeaturePython", name)
    proxy_cls(obj)
    FldViewProvider(obj.ViewObject)
    obj.Source = source_obj
    obj.touch()   # recompute belongs to the caller -- see CommandFldModifierBase (IF-016)
    return obj


# ── Twist ─────────────────────────────────────────────────────────────────────

class FldTwistProxy(FldModifierProxyBase):
    """FreeCAD proxy for a parametric Twist deformation modifier."""

    SOURCE_GROUP = "Twist"

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "Axis"):
            obj.addProperty("App::PropertyEnumeration", "Axis", "Twist", "Twist axis")
            obj.Axis = _AXES
            obj.Axis = "Z"
        if not hasattr(obj, "AnglePerUnit"):
            obj.addProperty("App::PropertyFloat", "AnglePerUnit", "Twist",
                            "Twist rate (degrees per mm along axis)").AnglePerUnit = 1.0

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.twist import SdfTwistField
        base_field = self._resolve_source_field(fp)
        if base_field:
            self.SdfField = SdfTwistField(
                source=base_field,
                axis=getattr(fp, "Axis", "Z"),
                angle_per_unit=getattr(fp, "AnglePerUnit", 1.0),
            )

    def onChanged(self, fp, prop):
        if prop in ("Axis", "AnglePerUnit", "Source", "Enabled"):
            self.SdfField = None


def create_twist_modifier(name: str, source_obj) -> object:
    return _create_modifier(FldTwistProxy, name, source_obj)


# ── Bend ──────────────────────────────────────────────────────────────────────

class FldBendProxy(FldModifierProxyBase):
    """FreeCAD proxy for a parametric Bend deformation modifier."""

    SOURCE_GROUP = "Bend"

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "Axis"):
            obj.addProperty("App::PropertyEnumeration", "Axis", "Bend", "Bend axis")
            obj.Axis = _AXES
            obj.Axis = "Z"
        if not hasattr(obj, "BendAngle"):
            obj.addProperty("App::PropertyFloat", "BendAngle", "Bend",
                            "Total bend angle (degrees)").BendAngle = 45.0
        if not hasattr(obj, "BendOrigin"):
            obj.addProperty("App::PropertyFloat", "BendOrigin", "Bend",
                            "Position along axis at the bend centre (mm)").BendOrigin = 0.0

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.bend import SdfBendField
        base_field = self._resolve_source_field(fp)
        if base_field:
            self.SdfField = SdfBendField(
                source=base_field,
                axis=getattr(fp, "Axis", "Z"),
                bend_angle=getattr(fp, "BendAngle", 45.0),
                bend_origin=getattr(fp, "BendOrigin", 0.0),
            )

    def onChanged(self, fp, prop):
        if prop in ("Axis", "BendAngle", "BendOrigin", "Source", "Enabled"):
            self.SdfField = None


def create_bend_modifier(name: str, source_obj) -> object:
    return _create_modifier(FldBendProxy, name, source_obj)


# ── Lattice ───────────────────────────────────────────────────────────────────

class FldLatticeProxy(FldModifierProxyBase):
    """FreeCAD proxy for a free-form lattice deformation modifier."""

    SOURCE_GROUP = "Lattice"

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "Resolution"):
            obj.addProperty("App::PropertyInteger", "Resolution", "Lattice",
                            "Control-point count per axis (>=2)").Resolution = 2
        if not hasattr(obj, "LatticeOrigin"):
            obj.addProperty("App::PropertyVector", "LatticeOrigin", "Lattice",
                            "Origin of the lattice").LatticeOrigin = FreeCAD.Vector(0.0, 0.0, 0.0)
        if not hasattr(obj, "LatticeExtent"):
            obj.addProperty("App::PropertyVector", "LatticeExtent", "Lattice",
                            "Extent (dimensions) of the lattice").LatticeExtent = FreeCAD.Vector(1.0, 1.0, 1.0)
        if not hasattr(obj, "LatticePlacement"):
            obj.addProperty("App::PropertyPlacement", "LatticePlacement", "Lattice",
                            "Placement of the lattice").LatticePlacement = FreeCAD.Placement()
        if not hasattr(obj, "HasLatticeBounds"):
            obj.addProperty("App::PropertyBool", "HasLatticeBounds", "Lattice",
                            "True if bounds are initialized").HasLatticeBounds = False
        if not hasattr(obj, "Displacements"):
            obj.addProperty("App::PropertyVectorList", "Displacements", "Lattice",
                            "Displacement vectors of the control points").Displacements = []

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.lattice import SdfLatticeField
        base_field = self._resolve_source_field(fp)
        if not base_field:
            self.SdfField = None
            return

        res = max(2, getattr(fp, "Resolution", 2))
        
        # If not initialized, fit bounds to the source field's bounding box
        if not getattr(fp, "HasLatticeBounds", False):
            try:
                bbox = base_field.bounding_box()
                min_c, max_c = bbox[0], bbox[1]
                fp.LatticeOrigin = min_c
                fp.LatticeExtent = max_c - min_c
                fp.LatticePlacement = FreeCAD.Placement()
                fp.HasLatticeBounds = True
            except Exception:
                fp.LatticeOrigin = FreeCAD.Vector(-50.0, -50.0, -50.0)
                fp.LatticeExtent = FreeCAD.Vector(100.0, 100.0, 100.0)
                fp.LatticePlacement = FreeCAD.Placement()
                fp.HasLatticeBounds = True
                
        # Initialize Displacements if empty or size mismatch
        num_pts = res ** 3
        disps = getattr(fp, "Displacements", [])
        if len(disps) != num_pts:
            fp.Displacements = [FreeCAD.Vector(0.0, 0.0, 0.0)] * num_pts
            disps = fp.Displacements
            
        import numpy as np
        disps_np = np.array([[d.x, d.y, d.z] for d in disps], dtype=np.float32)
        
        desc = {
            'origin': fp.LatticeOrigin,
            'extent': fp.LatticeExtent,
            'placement': fp.LatticePlacement
        }
        
        self.SdfField = SdfLatticeField(
            source=base_field,
            resolution=res,
            displacements=disps_np,
            descriptor=desc
        )

    def onChanged(self, fp, prop):
        if prop == "Resolution":
            res = max(2, getattr(fp, "Resolution", 2))
            fp.Displacements = [FreeCAD.Vector(0.0, 0.0, 0.0)] * (res ** 3)
            fp.HasLatticeBounds = False
            self.SdfField = None
        elif prop in ("Source", "Displacements", "LatticeOrigin", "LatticeExtent", "LatticePlacement", "Enabled"):
            self.SdfField = None


def create_lattice_modifier(name: str, source_obj) -> object:
    return _create_modifier(FldLatticeProxy, name, source_obj)


# ── Array ─────────────────────────────────────────────────────────────────────

class FldArrayProxy(FldModifierProxyBase):
    """FreeCAD proxy for a parametric Array (Grid / Step) repetition modifier."""

    SOURCE_GROUP = "Array"

    def _ensure_array_properties(self, obj):
        old_mode = None
        if hasattr(obj, "Mode"):
            try:
                curr_val = str(obj.Mode)
            except Exception:
                curr_val = ""
            if curr_val in ("Linear", "Radial"):
                old_mode = curr_val
                # Only touch the enumeration list when a legacy value is present.
                obj.Mode = ["Grid", "Step"]
                obj.Mode = "Grid" if old_mode == "Linear" else "Step"
        else:
            obj.addProperty("App::PropertyEnumeration", "Mode", "Array", "Array mode (Grid or Step)")
            obj.Mode = ["Grid", "Step"]
            obj.Mode = "Grid"

        # Grid properties
        if not hasattr(obj, "CountX"):
            obj.addProperty("App::PropertyInteger", "CountX", "Array", "Copies along X axis")
            obj.CountX = 2
        if not hasattr(obj, "CountY"):
            obj.addProperty("App::PropertyInteger", "CountY", "Array", "Copies along Y axis")
            obj.CountY = 1
        if not hasattr(obj, "CountZ"):
            obj.addProperty("App::PropertyInteger", "CountZ", "Array", "Copies along Z axis")
            obj.CountZ = 1
        if not hasattr(obj, "SpacingX"):
            obj.addProperty("App::PropertyFloat", "SpacingX", "Array", "Spacing along X axis (mm)")
            obj.SpacingX = 20.0
        if not hasattr(obj, "SpacingY"):
            obj.addProperty("App::PropertyFloat", "SpacingY", "Array", "Spacing along Y axis (mm)")
            obj.SpacingY = 20.0
        if not hasattr(obj, "SpacingZ"):
            obj.addProperty("App::PropertyFloat", "SpacingZ", "Array", "Spacing along Z axis (mm)")
            obj.SpacingZ = 20.0

        # Step properties
        if not hasattr(obj, "StepCount"):
            obj.addProperty("App::PropertyInteger", "StepCount", "Array", "Number of step copies")
            obj.StepCount = 6
        if not hasattr(obj, "StepX"):
            obj.addProperty("App::PropertyFloat", "StepX", "Array", "Step offset X (mm)")
            obj.StepX = 0.0
        if not hasattr(obj, "StepY"):
            obj.addProperty("App::PropertyFloat", "StepY", "Array", "Step offset Y (mm)")
            obj.StepY = 0.0
        if not hasattr(obj, "StepZ"):
            obj.addProperty("App::PropertyFloat", "StepZ", "Array", "Step offset Z (mm)")
            obj.StepZ = 0.0
        if not hasattr(obj, "StepAxis"):
            obj.addProperty("App::PropertyEnumeration", "StepAxis", "Array", "Rotation axis for step transform")
            obj.StepAxis = _AXES
            obj.StepAxis = "Z"
        if not hasattr(obj, "StepAngle"):
            obj.addProperty("App::PropertyFloat", "StepAngle", "Array", "Step rotation angle (degrees)")
            obj.StepAngle = 60.0
        if not hasattr(obj, "StepCenterX"):
            obj.addProperty("App::PropertyFloat", "StepCenterX", "Array", "Step center X (mm)")
            obj.StepCenterX = 0.0
        if not hasattr(obj, "StepCenterY"):
            obj.addProperty("App::PropertyFloat", "StepCenterY", "Array", "Step center Y (mm)")
            obj.StepCenterY = 0.0
        if not hasattr(obj, "StepCenterZ"):
            obj.addProperty("App::PropertyFloat", "StepCenterZ", "Array", "Step center Z (mm)")
            obj.StepCenterZ = 0.0
        if not hasattr(obj, "StepScale"):
            obj.addProperty("App::PropertyFloat", "StepScale", "Array", "Step uniform scale factor")
            obj.StepScale = 1.0
        if not hasattr(obj, "AngleFormula"):
            obj.addProperty("App::PropertyString", "AngleFormula", "Array", "Formula override for angle")
            obj.AngleFormula = ""
        if not hasattr(obj, "RadiusFormula"):
            obj.addProperty("App::PropertyString", "RadiusFormula", "Array", "Formula override for radius")
            obj.RadiusFormula = ""
        if not hasattr(obj, "RiseFormula"):
            obj.addProperty("App::PropertyString", "RiseFormula", "Array", "Formula override for rise")
            obj.RiseFormula = ""
        if not hasattr(obj, "ScaleFormula"):
            obj.addProperty("App::PropertyString", "ScaleFormula", "Array", "Formula override for scale")
            obj.ScaleFormula = ""
        if not hasattr(obj, "SkipIndices"):
            obj.addProperty("App::PropertyIntegerList", "SkipIndices", "Array", "List of copy indices to skip")
            obj.SkipIndices = []
        if not hasattr(obj, "OverlapMode"):
            obj.addProperty("App::PropertyEnumeration", "OverlapMode", "Array", "Overlap evaluation mode")
            obj.OverlapMode = ["Auto", "Always", "Never"]
            obj.OverlapMode = "Auto"

        # Apply migration if old_mode was present
        if old_mode == "Radial":
            if hasattr(obj, "RadialAxis"):
                obj.StepAxis = getattr(obj, "RadialAxis", "Z")
            rad_cnt = getattr(obj, "RadialCount", 6)
            rad_span = getattr(obj, "RadialSpan", 360.0)
            obj.StepCount = rad_cnt
            obj.StepAngle = rad_span / max(rad_cnt, 1)

    def __init__(self, obj):
        super().__init__(obj)
        self._ensure_array_properties(obj)

    def onDocumentRestored(self, obj):
        super().onDocumentRestored(obj)
        self._ensure_array_properties(obj)

    def _build_field(self, fp):
        from freecad.fields.core.sdf.sdf.array import SdfArrayField
        base_field = self._resolve_source_field(fp)
        if base_field:
            self.SdfField = SdfArrayField(
                source=base_field,
                mode=getattr(fp, "Mode", "Grid"),
                counts=(
                    getattr(fp, "CountX", 2),
                    getattr(fp, "CountY", 1),
                    getattr(fp, "CountZ", 1),
                ),
                spacing=(
                    getattr(fp, "SpacingX", 20.0),
                    getattr(fp, "SpacingY", 20.0),
                    getattr(fp, "SpacingZ", 20.0),
                ),
                step_count=getattr(fp, "StepCount", 6),
                step_offset=(
                    getattr(fp, "StepX", 0.0),
                    getattr(fp, "StepY", 0.0),
                    getattr(fp, "StepZ", 0.0),
                ),
                step_axis=getattr(fp, "StepAxis", "Z"),
                step_angle=getattr(fp, "StepAngle", 60.0),
                step_center=(
                    getattr(fp, "StepCenterX", 0.0),
                    getattr(fp, "StepCenterY", 0.0),
                    getattr(fp, "StepCenterZ", 0.0),
                ),
                step_scale=getattr(fp, "StepScale", 1.0),
                angle_formula=getattr(fp, "AngleFormula", ""),
                radius_formula=getattr(fp, "RadiusFormula", ""),
                rise_formula=getattr(fp, "RiseFormula", ""),
                scale_formula=getattr(fp, "ScaleFormula", ""),
                skipped=getattr(fp, "SkipIndices", ()),
                overlap_mode=getattr(fp, "OverlapMode", "Auto"),
            )

    def onChanged(self, fp, prop):
        if prop in (
            "Mode",
            "CountX",
            "CountY",
            "CountZ",
            "SpacingX",
            "SpacingY",
            "SpacingZ",
            "StepCount",
            "StepX",
            "StepY",
            "StepZ",
            "StepAxis",
            "StepAngle",
            "StepCenterX",
            "StepCenterY",
            "StepCenterZ",
            "StepScale",
            "AngleFormula",
            "RadiusFormula",
            "RiseFormula",
            "ScaleFormula",
            "SkipIndices",
            "OverlapMode",
            "OverlapSafe",
            "RadialAxis",
            "RadialCount",
            "RadialSpan",
            "Source",
            "Enabled",
        ):
            self.SdfField = None


def create_array_modifier(name: str, source_obj) -> object:
    return _create_modifier(FldArrayProxy, name, source_obj)


# ── Deform Cage ───────────────────────────────────────────────────────────────

class FldDeformCageProxy(FldModifierProxyBase):
    """FreeCAD proxy for a deform cage modifier wrapped around a source SDF."""

    SOURCE_GROUP = "DeformCage"

    def __init__(self, obj):
        super().__init__(obj)
        if not hasattr(obj, "Vertices"):
            obj.addProperty("App::PropertyVectorList", "Vertices", "DeformCage", "Rest cage vertices")
        if not hasattr(obj, "Handles"):
            obj.addProperty("App::PropertyVectorList", "Handles", "DeformCage", "Rest cage handles")
        if not hasattr(obj, "FaceVertices"):
            obj.addProperty("App::PropertyIntegerList", "FaceVertices", "DeformCage", "Face vertex indices")
        if not hasattr(obj, "FaceSizes"):
            obj.addProperty("App::PropertyIntegerList", "FaceSizes", "DeformCage", "Face sizes")
        if not hasattr(obj, "EdgeVertices"):
            obj.addProperty("App::PropertyIntegerList", "EdgeVertices", "DeformCage", "Edge vertex pairs")
        if not hasattr(obj, "HandleTypes"):
            obj.addProperty("App::PropertyIntegerList", "HandleTypes", "DeformCage", "Handle types")
        if not hasattr(obj, "EdgeStraight"):
            obj.addProperty("App::PropertyIntegerList", "EdgeStraight", "DeformCage", "Straight edge flags")
        if not hasattr(obj, "Displacements"):
            obj.addProperty("App::PropertyVectorList", "Displacements", "DeformCage", "Control point displacements")
        if not hasattr(obj, "Points"):
            obj.addProperty("App::PropertyVectorList", "Points", "DeformCage", "Active control points (vertices + handles)")
        if not hasattr(obj, "ExtrudeRingSizes"):
            obj.addProperty("App::PropertyIntegerList", "ExtrudeRingSizes", "DeformCage",
                            "Vertex count of each extrusion ring")
        if not hasattr(obj, "ExtrudeBaseRings"):
            obj.addProperty("App::PropertyVectorList", "ExtrudeBaseRings", "DeformCage",
                            "Concatenated rest-frame base rings, one segment per extrusion")
        if not hasattr(obj, "ExtrudeTopRings"):
            obj.addProperty("App::PropertyVectorList", "ExtrudeTopRings", "DeformCage",
                            "Concatenated rest-frame top rings, one segment per extrusion")
        if not hasattr(obj, "ExtrudeBaseHandles"):
            obj.addProperty("App::PropertyVectorList", "ExtrudeBaseHandles", "DeformCage",
                            "Concatenated rest-frame base-ring handles, 2 per ring edge")
        if not hasattr(obj, "SdfType"):
            obj.addProperty("App::PropertyString", "SdfType", "DeformCage", "SDF shape type")
        obj.SdfType = "cage"

    @staticmethod
    def _extrusion_records(fp):
        """(base_ring, top_ring, base_handles) per stored extrusion, as (k,3) arrays.

        Malformed or half-written segments are skipped, not raised on: FreeCAD fires
        onChanged once per property assignment, so a batch writer transiently exposes
        a ring list that does not yet match ExtrudeRingSizes.
        """
        import numpy as np

        sizes = list(getattr(fp, "ExtrudeRingSizes", None) or [])
        if not sizes:
            return []

        from freecad.fields.core.objects.fld_face_extrude_objects import to_xyz_list

        def to_np(vecs):
            if len(vecs) == 0:
                return np.empty((0, 3), dtype=np.float64)
            return np.array([to_xyz_list(v) for v in vecs], dtype=np.float64).reshape((-1, 3))

        bases = list(getattr(fp, "ExtrudeBaseRings", None) or [])
        tops = list(getattr(fp, "ExtrudeTopRings", None) or [])
        hands = list(getattr(fp, "ExtrudeBaseHandles", None) or [])

        out, at_ring, at_handle = [], 0, 0
        for k in sizes:
            k = int(k)
            if len(bases) < at_ring + k or len(tops) < at_ring + k:
                break
            handle_seg = (to_np(hands[at_handle:at_handle + 2 * k])
                          if len(hands) >= at_handle + 2 * k else None)
            out.append((to_np(bases[at_ring:at_ring + k]),
                        to_np(tops[at_ring:at_ring + k]),
                        handle_seg))
            at_ring += k
            at_handle += 2 * k
        return out

    def _extruded_source(self, fp, base_field):
        """base_field plus one analytic extrusion per stored record, as a flat stack.

        Each extrusion is swept out of everything before it, not out of
        base_field. An analytic face extrude is "a slice of the source, swept"
        (see design_analytic_face_extrude): both the height datum and the cap are
        measured on its source, so an extrusion whose base ring sits on an earlier
        lobe has to see that lobe or it measures empty space and caps with a copy
        of the bare primitive.

        "Everything before it" is an `SdfExtrusionStackField`, not a nested
        `UnionField` spine. The spine cost 3^n -- every level evaluated its source
        once for the base-side term, once inside the cap and once as the union's
        left operand -- which froze the third extrude and produced a 20k-character
        shader at four lobes. The stack shares the running prefix instead. Note
        this makes construction cheap too, since the height-datum scan runs
        against the flat prefix.

        Extrusion fields are cached per record. A modal extrude drag re-enters
        here every tick but moves only the last record's top ring, so building
        all of them from scratch made each extrusion already on the cage add its
        own height-datum scan to every subsequent tick -- and that scan is not a
        fixed cost: it evaluates the stack beneath its lobe, so it measured 0.64
        / 21.2 / 34.5 ms at 1 / 2 / 3 lobes (CX-006, 2026-08-18). A record is reused
        only while every extrusion below it is the identical object, so an edit
        invalidates everything stacked on top of it.
        """
        from freecad.fields.core.sdf.sdf_face_extrude import (
            SdfExtrusionStackField, SdfFaceExtrusionField)

        cached = getattr(self, "_extrude_cache", None)
        built = cached[1] if cached is not None and cached[0] is base_field else []

        exts = []
        for base_ring, top_ring, base_handles in self._extrusion_records(fp):
            if len(base_ring) != len(top_ring) or len(base_ring) < 3:
                continue
            prev = built[len(exts)] if len(exts) < len(built) else None
            if prev is not None and _same_prefix(prev.source, base_field, exts) \
                    and _same_array(prev.base_ring, base_ring) \
                    and _same_array(prev.base_handles, base_handles):
                ext = (prev if _same_array(prev.top_ring, top_ring)
                       else prev.with_top_ring(top_ring))
            else:
                ext = SdfFaceExtrusionField(
                    source=(SdfExtrusionStackField(base_field, exts) if exts
                            else base_field),
                    base_ring=base_ring,
                    top_ring=top_ring,
                    mode="control",
                    base_handles=base_handles,
                )
            exts.append(ext)

        self._extrude_cache = (base_field, exts)
        if not exts:
            return base_field

        # Reuse the stack WRAPPER, not just the lobes inside it. The lobe cache
        # above already returns the identical `ext` objects when nothing moved,
        # but wrapping them in a fresh SdfExtrusionStackField each time still
        # threw away that object's `_octree_cache` -- and the 2.0 mm picking
        # octree hangs off exactly this object, because
        # SdfCageDeformField.octree_cache delegates to `self.source` while the
        # cage is identity (cage_deform.py:729), and a face extrude leaves the
        # cage identity. So every rebuild silently re-armed a 15-48 ms lazy
        # rebuild on the next ray_march, twice per commit, growing with lobe
        # count because evaluating the stack gets dearer with each lobe.
        #
        # Identity comparison is the right test and not a shortcut: the lobes
        # are immutable once built (an edit produces a new object via
        # `with_top_ring`), so same base + same lobe objects is the same field,
        # and the octree it holds is still valid. A drag tick moves the top ring,
        # produces a new lobe, misses here, and correctly rebuilds.
        prev = getattr(self, "_extrude_stack_cache", None)
        if (prev is not None
                and prev.base is base_field
                and len(prev.extrusions) == len(exts)
                and all(a is b for a, b in zip(prev.extrusions, exts))):
            return prev

        stack = SdfExtrusionStackField(base_field, exts)
        self._extrude_stack_cache = stack
        return stack

    def _build_field(self, fp):
        # A single property write rebuilds the whole field, and a commit writes
        # eight of them in a row. min_ms keeps the cheap rebuilds silent, so the
        # log shows only the ones worth explaining -- and how many of them there
        # were, which is the other half of the question.
        from freecad.fields.core import fld_perf

        # `_suspend_rebuild` guarded onChanged only, and execute()/get_sdf_field()
        # do not check it -- so anything that read the field mid-batch built one
        # from a half-written record set. `_extrusion_records` tolerates that
        # state by design (it `break`s at the first size with no ring behind it,
        # see :376), so the build did not raise: it silently produced a field
        # with the WRONG lobe count, which is exactly the alternating 2/3 record
        # signature in the burst. The batch writer assigns the finished field
        # itself, so leaving the current one in place here is correct as well as
        # cheaper -- every batch writer either unsuspends before rebuilding
        # (push/set_last/pop_extrusion) or assigns SdfField directly
        # (_rebuild_cage_from_topology).
        if getattr(self, "_suspend_rebuild", False):
            return

        n = len(getattr(fp, "ExtrudeRingSizes", []) or [])
        self._build_field_calls = getattr(self, "_build_field_calls", 0) + 1

        # The five child phases go to a sink, not straight to the log: they run on
        # every rebuild and are usually 0.0ms, and a rebuild that does not clear
        # min_ms has nothing worth breaking down. Flushing only when the parent
        # fires keeps each child line under a parent that explains it.
        self._phase_sink = []
        with fld_perf.phase(
            f"deform cage _build_field #{self._build_field_calls} ({n} extrusion record(s))",
            category="cage",
            min_ms=20.0,
        ) as parent:
            self._build_field_inner(fp)
        if parent["seconds"] * 1000.0 >= 20.0:
            fld_perf.flush(self._phase_sink, category="cage")
        del self._phase_sink[:]

    def _build_field_inner(self, fp):
        import numpy as np
        from freecad.fields.core import fld_perf
        from freecad.fields.core.sdf.sdf.cage_net import CageNet

        # None if something other than _build_field called us: then each child
        # logs itself, which is the right behaviour for a standalone call.
        sink = getattr(self, "_phase_sink", None)
        with fld_perf.phase("  resolve source", category="cage", sink=sink):
            base_field = self._resolve_source_field(fp)
        if not base_field:
            self.SdfField = None
            return

        with fld_perf.phase("  CageNet.from_properties", category="cage", sink=sink):
            net = CageNet.from_properties(fp)

        # Validate consistency; see onChanged comment.
        topo_sig = (
            tuple(getattr(fp, "FaceVertices", []) or []),
            tuple(getattr(fp, "FaceSizes", []) or []),
            tuple(getattr(fp, "EdgeVertices", []) or []),
        )
        with fld_perf.phase("  topology validation", category="cage", sink=sink):
            if topo_sig != getattr(self, "_validated_topo_sig", None):
                n_verts = len(net.vertices)
                edge_set = {frozenset(e) for e in net.edges}
                for face in net.face_verts:
                    n = len(face)
                    if n < 2:
                        continue
                    if any(vi < 0 or vi >= n_verts for vi in face):
                        return
                    for i in range(n):
                        vi, vj = face[i], face[(i + 1) % n]
                        if frozenset((vi, vj)) not in edge_set:
                            return
                self._validated_topo_sig = topo_sig

        with fld_perf.phase("  _extruded_source", category="cage", sink=sink):
            source = self._extruded_source(fp, base_field)

        mvc_rv, mvc_tr = None, None
        cached_mvc = getattr(self, "_mvc_cache", None)
        if cached_mvc is not None:
            c_rv, c_rh, c_fv, c_res_v, c_tris = cached_mvc
            if (c_fv == net.face_verts
                    and c_rv.shape == net.rest_vertices.shape
                    and c_rh.shape == net.rest_handles.shape
                    and np.array_equal(c_rv, net.rest_vertices)
                    and np.array_equal(c_rh, net.rest_handles)):
                mvc_rv, mvc_tr = c_res_v, c_tris

        with fld_perf.phase("  net.to_field", category="cage", sink=sink):
            field = net.to_field(
                source=source,
                placement=fp.Placement if hasattr(fp, "Placement") else None,
                mvc_rest_verts=mvc_rv,
                mvc_tris=mvc_tr,
            )
            self.SdfField = field
            self._mvc_cache = (
                net.rest_vertices.copy(),
                net.rest_handles.copy(),
                [list(f) for f in net.face_verts],
                field._mvc_rest_verts,
                field._mvc_tris,
            )

    def onChanged(self, fp, prop):
        # FreeCAD fires onChanged once per property assignment. A caller updating
        # several topology props in a batch (e.g. cage_tool's extrude/rebuild
        # writing FaceVertices, then EdgeVertices, then Vertices) would otherwise
        # rebuild the field on each half-updated prop set — e.g. new FaceVertices
        # against old EdgeVertices, which raises KeyError on a missing edge. Batch
        # callers set _suspend_rebuild and assign SdfField themselves at the end.
        # execute() no longer rebuilds unconditionally: it skips while SdfField is
        # set and the upstream field is the same object, so a batch-assigned field
        # now survives recomputes instead of being regenerated from the properties
        # on the next one. Persist everything you write anyway — the first execute()
        # after the batch still rebuilds, and a reload has only the properties.
        if getattr(self, "_suspend_rebuild", False):
            return
        if prop in ("Source", "Vertices", "Handles", "FaceVertices", "FaceSizes",
                    "EdgeVertices", "HandleTypes", "EdgeStraight", "Displacements",
                    "Points", "Placement", "ExtrudeRingSizes", "ExtrudeBaseRings",
                    "ExtrudeTopRings", "ExtrudeBaseHandles", "Enabled"):
            self.SdfField = None


def can_build_deform_cage(field) -> bool:
    """True if create_deform_cage_modifier() can build *some* cage for `field`.

    A native cage net (to_deform_cage()) is the ideal case; every other field
    falls back to a bounding-box FFD lattice in create_deform_cage_modifier,
    which only needs bounding_box() to succeed. Mirror that fallback order here
    exactly, or the gate and the factory will disagree about what works.
    """
    if field is None:
        return False
    try:
        if field.to_deform_cage() is not None:
            return True
    except Exception as e:
        from freecad.fields.core import fld_logger
        fld_logger.debug(
            f"can_build_deform_cage: {type(field).__name__}.to_deform_cage() "
            f"raised ({e}); falling through to the bounding-box FFD lattice "
            f"probe, exactly as create_deform_cage_modifier does."
        )
    try:
        field.bounding_box()
        return True
    except Exception:
        return False


def _existing_deform_cage(source_obj):
    """Return the FldDeformCageProxy object already wrapping source_obj, if any."""
    doc = getattr(source_obj, "Document", None)
    if doc is None:
        return None
    doc_objs = getattr(doc, "Objects", None)
    if doc_objs is None and hasattr(doc, "_objs"):
        doc_objs = list(doc._objs.values())
    if not doc_objs:
        return None
    for obj in doc_objs:
        proxy = getattr(obj, "Proxy", None)
        if isinstance(proxy, FldDeformCageProxy) and getattr(obj, "Source", None) is source_obj:
            return obj
    return None


def create_deform_cage_modifier(doc, source_obj, name: str = None) -> object:
    """Create a Part::FeaturePython object with FldDeformCageProxy wrapping source_obj."""
    import numpy as np

    existing = _existing_deform_cage(source_obj)
    if existing is not None:
        return existing

    if name is None:
        name = f"{source_obj.Name}_DeformCage"

    proxy = getattr(source_obj, "Proxy", None)
    if not proxy or not hasattr(proxy, "SdfField"):
        from freecad.fields.core import fld_logger
        fld_logger.warn("create_deform_cage_modifier: selected object has no SdfField")
        return None

    field = proxy.SdfField
    desc = field.to_deform_cage() if hasattr(field, "to_deform_cage") else None
    if desc is None:
        try:
            bmin, bmax = field.bounding_box()
            center = (bmin + bmax) * 0.5
            size = bmax - bmin
            from freecad.fields.core.sdf.sdf.box import SdfBoxField
            desc = SdfBoxField(center, size).to_deform_cage()
        except Exception:
            from freecad.fields.core import fld_logger
            fld_logger.warn("create_deform_cage_modifier: selected object has no cage descriptor and bounding box failed")
            return None

    # Hide source object visibility
    if hasattr(source_obj, "ViewObject") and source_obj.ViewObject:
        source_obj.ViewObject.Visibility = False

    mod_obj = doc.addObject("Part::FeaturePython", name)
    cage_proxy = FldDeformCageProxy(mod_obj)
    FldViewProvider(mod_obj.ViewObject)

    pl = desc.get("placement")
    if pl is not None:
        def _to_world(v):
            w = pl.multVec(FreeCAD.Vector(float(v[0]), float(v[1]), float(v[2])))
            return FreeCAD.Vector(w.x, w.y, w.z)
        verts_vec = [_to_world(v) for v in desc["vertices"]]
        handles_vec = [_to_world(h) for h in desc["handles"]]
    else:
        verts_vec = [FreeCAD.Vector(float(v[0]), float(v[1]), float(v[2])) for v in desc["vertices"]]
        handles_vec = [FreeCAD.Vector(float(h[0]), float(h[1]), float(h[2])) for h in desc["handles"]]

    fv_flat = [v for face in desc["face_verts"] for v in face]
    fs = [len(face) for face in desc["face_verts"]]
    ev_flat = [v for edge in desc["edges"] for v in edge]

    cage_proxy._suspend_rebuild = True
    try:
        mod_obj.Source = source_obj
        mod_obj.Vertices = verts_vec
        mod_obj.Handles = handles_vec
        mod_obj.FaceVertices = fv_flat
        mod_obj.FaceSizes = fs
        mod_obj.EdgeVertices = ev_flat
        mod_obj.HandleTypes = list(desc["handle_types"])
        mod_obj.EdgeStraight = [1 if es else 0 for es in (desc["edge_straight"] or [])]
        mod_obj.Displacements = [FreeCAD.Vector(0, 0, 0)] * (len(verts_vec) + len(handles_vec))
        mod_obj.Points = list(verts_vec) + list(handles_vec)
        mod_obj.Placement = FreeCAD.Placement()
    finally:
        cage_proxy._suspend_rebuild = False

    cage_proxy._build_field(mod_obj)
    doc.recompute([mod_obj])
    return mod_obj


def _rebuild_extrusion_field(obj):
    proxy = getattr(obj, "Proxy", None)
    if not proxy:
        return None
    if hasattr(proxy, "_build_field"):
        proxy._build_field(obj)
    return getattr(proxy, "SdfField", None)


def push_extrusion(obj, base_ring, top_ring, base_handles=None):
    """Append one rest-frame extrusion record to obj and rebuild its field.

    base_ring/top_ring are (k,3) arrays in the cage's REST frame; base_handles is
    (2k,3) ordered 2 per ring edge j as (near ring[j], near ring[j+1]).
    """
    import numpy as np

    def to_vecs(arr):
        return [FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2]))
                for p in np.asarray(arr, dtype=np.float64).reshape((-1, 3))]

    k = len(base_ring)
    proxy = obj.Proxy
    proxy._suspend_rebuild = True
    try:
        obj.ExtrudeRingSizes = list(getattr(obj, "ExtrudeRingSizes", None) or []) + [k]
        obj.ExtrudeBaseRings = list(getattr(obj, "ExtrudeBaseRings", None) or []) + to_vecs(base_ring)
        obj.ExtrudeTopRings = list(getattr(obj, "ExtrudeTopRings", None) or []) + to_vecs(top_ring)
        if base_handles is not None:
            obj.ExtrudeBaseHandles = (list(getattr(obj, "ExtrudeBaseHandles", None) or [])
                                      + to_vecs(base_handles))
    finally:
        proxy._suspend_rebuild = False
    return _rebuild_extrusion_field(obj)


def set_last_extrusion_top(obj, top_ring):
    """Overwrite the last record's top ring and rebuild. One drag tick."""
    import numpy as np

    sizes = list(getattr(obj, "ExtrudeRingSizes", None) or [])
    if not sizes:
        return None
    k = int(sizes[-1])
    tops = list(getattr(obj, "ExtrudeTopRings", None) or [])
    if len(tops) < k:
        return None
    new_seg = [FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2]))
               for p in np.asarray(top_ring, dtype=np.float64).reshape((-1, 3))]
    proxy = obj.Proxy
    proxy._suspend_rebuild = True
    try:
        obj.ExtrudeTopRings = tops[:len(tops) - k] + new_seg
    finally:
        proxy._suspend_rebuild = False
    return _rebuild_extrusion_field(obj)


def pop_extrusion(obj):
    """Remove the last extrusion record and rebuild. Cancels a modal extrude."""
    sizes = list(getattr(obj, "ExtrudeRingSizes", None) or [])
    if not sizes:
        return None
    k = int(sizes[-1])
    proxy = obj.Proxy
    proxy._suspend_rebuild = True
    try:
        obj.ExtrudeRingSizes = sizes[:-1]
        rings = list(getattr(obj, "ExtrudeBaseRings", None) or [])
        obj.ExtrudeBaseRings = rings[:max(0, len(rings) - k)]
        tops = list(getattr(obj, "ExtrudeTopRings", None) or [])
        obj.ExtrudeTopRings = tops[:max(0, len(tops) - k)]
        hands = list(getattr(obj, "ExtrudeBaseHandles", None) or [])
        obj.ExtrudeBaseHandles = hands[:max(0, len(hands) - 2 * k)]
    finally:
        proxy._suspend_rebuild = False
    return _rebuild_extrusion_field(obj)



