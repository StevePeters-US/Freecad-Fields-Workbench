# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
tools/creators/extrusion_creator.py

Interactive SDF curve-extrusion primitive creator tools: linear extrusion
of a sketched 2D profile (CurveExtrudeCreator), extrusion along a 3D
direction vector (CurveExtrude3DCreator / CurvePipe), and
extrusion of an existing 3D curve's surface (SdfCurveFillExtrudeCreator).
"""
import FreeCAD
import FreeCADGui
from PySide import QtCore
from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_point import FldPoint
from freecad.fields.core.objects.fld_line import FldLineSet
from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
from freecad.fields.core.sdf.sdf_field import placement_matrix
from freecad.fields.tools.fld_base import ToolState
from freecad.fields.tools.primitive_creator_base import (
    PrimitiveCreatorBase, MIN_PRIMITIVE_DIM_TYPED_MM
)
from freecad.fields.tools.extrude_gizmo import ExtrudeGizmo
import math


def extrude_rings_from_segments_3d(segs_3d, offset):
    """Base and top gizmo rings for a 3D curve swept by `offset`.

    Returns (base_ring, base_handles, top_ring, top_handles) as FreeCAD.Vectors, with
    the handles in the [near i, near i+1] per-edge order `bezier_ring_samples` expects.
    Everything stays in world space and keeps its z, which is the whole point: the
    projected 2D segments the prism walls are built from have had their z dropped, so
    drawing those flattens a 3D curve onto its own best-fit plane.
    """
    base_ring = [FreeCAD.Vector(*seg[0]) for seg in segs_3d]
    base_handles = [FreeCAD.Vector(*p) for seg in segs_3d for p in (seg[1], seg[2])]
    return (base_ring, base_handles,
            [p + offset for p in base_ring], [p + offset for p in base_handles])


class CurveExtrudeCreator(PrimitiveCreatorBase):
    """
    Extrudes a selected closed curve into an SDF solid using exact cubic Bezier distance.
    Uses obj.Placement directly as the extrusion direction (2D curves only).
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    # Geometry is anchored to the linked source object — the panel must not move it.
    SUPPORTS_ORIGIN_EDIT = False

    def get_command_id(self):
        return "Fields_ExtrudeCurve"

    def get_sdf_type(self):
        return "curve_extrude"

    def _get_source_curve_link(self):
        return self._curve_obj

    def __init__(self):
        super().__init__()
        self._curve_obj      = None
        self._height         = 5.0   # full extrusion height in mm
        self._bezier_segs    = None
        # GLSL-stable field cache: id() must stay constant to avoid shader recompiles
        self._cached_profile = None   # Sdf2dNurbsCurve
        self._cached_extrude = None   # SdfExtrusionField

        # Visual handle gizmo
        self.gizmo = ExtrudeGizmo(self.points_root)

        import FreeCADGui
        for obj in FreeCADGui.Selection.getSelection():
            if getattr(obj, "ShapeType", None) == "curve" and getattr(obj, "Closed", False):
                self._curve_obj = obj
                break

        if self._curve_obj is not None:
            from freecad.fields.core.sdf.curve_sampler import (
                sample_curve_world_pts, compute_best_fit_placement,
                extract_bezier_segments_in_placement,
            )
            world_pts = sample_curve_world_pts(self._curve_obj, n_samples=64)
            best_fit  = compute_best_fit_placement(
                world_pts, fallback_placement=self._curve_obj.Placement
            )
            if best_fit is None:
                best_fit = self._curve_obj.Placement
            self.working_plane              = best_fit
            self._working_plane_is_fallback = False
            self._anchor_pt                 = best_fit.Base
            self._bezier_segs               = extract_bezier_segments_in_placement(
                self._curve_obj, best_fit
            )
            self._update_extrude_handles()

    # Prevent _detect_selected_workplane from overriding the curve's placement
    def _detect_selected_workplane(self):
        pass

    def _clamp_height(self, h):
        return max(0.1, h)

    def _on_stage_accept(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            wp   = self.working_plane
            norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            self._height = self._clamp_height((pos - self._anchor_pt).dot(norm))
            self._update_extrude_handles()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            wp   = self.working_plane
            norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1)) if wp else FreeCAD.Vector(0, 0, 1)
            self._height = self._clamp_height((pos - self._anchor_pt).dot(norm))
            self._update_extrude_handles()

    def _update_extrude_handles(self):
        """Draw/update base + top curve rings and vertical connector lines via ExtrudeGizmo."""
        if not self._bezier_segs or not self.working_plane:
            self.gizmo.clear()
            return
        wp   = self.working_plane
        norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        offset = norm * self._height
        r = self._compute_handle_radius()

        base_ctrl = [wp.multVec(FreeCAD.Vector(p0[0], p0[1], 0))
                     for p0, p1, p2, p3 in self._bezier_segs]
        top_ctrl  = [p + offset for p in base_ctrl]

        self.gizmo.update(base_ctrl, top_ctrl, handle_radius=r)

    def _offset_placement(self):
        """Working plane shifted height/2 along its normal - centers the ±height/2 extrusion."""
        wp   = self.working_plane
        norm = wp.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        return FreeCAD.Placement(wp.Base + norm * (self._height * 0.5), wp.Rotation)

    def _update_field_inplace(self, field):
        """Sync height and placement on an existing SdfExtrusionField without changing id()."""
        import numpy as np
        field.height = self._height
        op = self._offset_placement()
        field.placement = op
        field.inv_matrix = placement_matrix(op, inverse=True, dtype=np.float32)

    def _extrude_field(self):
        if self._height < MIN_PRIMITIVE_DIM_TYPED_MM or not self._curve_obj:
            return None
        from freecad.fields.core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
        from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField

        if self._cached_profile is None:
            if self._bezier_segs:
                self._cached_profile = Sdf2dBezierCurve(self._bezier_segs)
            else:
                return None

        if self._cached_extrude is None:
            self._cached_extrude = SdfExtrusionField(
                self._cached_profile,
                height=self._height,
                placement=self._offset_placement(),
            )
        else:
            self._update_field_inplace(self._cached_extrude)

        return self._cached_extrude

    def _get_preview_field(self):       return self._extrude_field()
    def _get_edit_preview_field(self):  return self._extrude_field()
    def _get_final_field(self):         return self._extrude_field()

    def _get_final_points(self):
        return [FreeCAD.Vector(0.0, 0.0, self._height)] if self._curve_obj else None

    def get_parameters(self):
        return {"Height": self._height}

    def _apply_parameters(self, params):
        self._height = self._clamp_height(float(params.get("Height", 10.0)))
        if self._cached_extrude is not None:
            self._update_field_inplace(self._cached_extrude)
        self._update_extrude_handles()
        return True

    def _primitive_name(self):
        return "CurveExtrude"

    def finish(self):
        """One-shot tool: commit then terminate (prevents a second object being created)."""
        if getattr(self, "_is_editing", False):
            super().finish()   # commits edits, resets to DRAG_Z
            self._finished = True
            self.terminate()
        elif self.is_in_progress():
            self._finalize_object(self._primitive_name(), terminate=True)
        else:
            self.terminate()

    def _do_terminate(self):
        self.gizmo.clear()
        super()._do_terminate()


class CurveExtrude3DCreator(PrimitiveCreatorBase):
    """
    Creates a round tube/pipe swept along a selected 3D curve path.
    Works with open and closed curves of any 3D shape.
    The user drags to set the tube radius.
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    # Geometry is anchored to the linked source object — the panel must not move it.
    SUPPORTS_ORIGIN_EDIT = False

    def get_command_id(self):
        return "Fields_CurvePipe"

    def get_sdf_type(self):
        return "curve_pipe"

    def _get_source_curve_link(self):
        return self._curve_obj

    def __init__(self):
        super().__init__()
        self._curve_obj   = None
        self._radius      = 3.0
        self._segs_3d     = None   # list of (p0,p1,p2,p3), each pi = (x,y,z) world
        self._cached_pipe = None   # SdfPipeField — kept stable to avoid recompile

        # Visuals
        self._path_wire     = None  # FldLineSet: sampled curve path
        self._ctrl_fld_pts   = []    # FldPoint spheres at control points
        self._radius_circle = None  # FldLineSet: cross-section circle at path start

        import FreeCADGui
        for obj in FreeCADGui.Selection.getSelection():
            if getattr(obj, "ShapeType", None) == "curve":
                self._curve_obj = obj
                break

        if self._curve_obj is not None:
            from freecad.fields.core.sdf.curve_sampler import extract_bezier_segments_3d
            self._segs_3d = extract_bezier_segments_3d(self._curve_obj)

            # Working plane: horizontal at the centroid of all control points
            pts = list(getattr(self._curve_obj, "Points", []))
            pl  = self._curve_obj.Placement
            world_pts = [pl.multVec(p) for p in pts]
            if world_pts:
                cx = sum(p.x for p in world_pts) / len(world_pts)
                cy = sum(p.y for p in world_pts) / len(world_pts)
                cz = sum(p.z for p in world_pts) / len(world_pts)
                centroid = FreeCAD.Vector(cx, cy, cz)
            else:
                centroid = FreeCAD.Vector(0, 0, 0)
            self.working_plane              = FreeCAD.Placement(centroid, FreeCAD.Rotation())
            self._working_plane_is_fallback = False
            self._anchor_pt                 = centroid
            self._update_pipe_handles()

    def _detect_selected_workplane(self):
        pass

    def _clamp_radius(self, r):
        return max(0.1, r)

    def _on_stage_accept(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            self._radius = self._clamp_radius((pos - self._anchor_pt).Length)
            if self._cached_pipe is not None:
                self._cached_pipe.radius = self._radius
            self._update_pipe_handles()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            self._radius = self._clamp_radius((pos - self._anchor_pt).Length)
            if self._cached_pipe is not None:
                self._cached_pipe.radius = self._radius
            self._update_pipe_handles()

    @staticmethod
    def _sample_seg_3d_world(p0, p1, p2, p3, n=12):
        """Sample n evenly-spaced points on a 3D cubic Bezier (excludes t=1)."""
        pts = []
        for i in range(n):
            t = i / n
            s = 1.0 - t
            x = s**3*p0[0] + 3*s**2*t*p1[0] + 3*s*t**2*p2[0] + t**3*p3[0]
            y = s**3*p0[1] + 3*s**2*t*p1[1] + 3*s*t**2*p2[1] + t**3*p3[1]
            z = s**3*p0[2] + 3*s**2*t*p1[2] + 3*s*t**2*p2[2] + t**3*p3[2]
            pts.append(FreeCAD.Vector(x, y, z))
        return pts

    @staticmethod
    def _circle_pts(center, tangent, radius, n=24):
        """Sample a circle of `radius` at `center` in the plane perpendicular to `tangent`."""
        n_vec = FreeCAD.Vector(tangent).normalize() if tangent.Length > 1e-6 else FreeCAD.Vector(0, 0, 1)
        if abs(n_vec.z) < 0.9:
            u = FreeCAD.Vector(0, 0, 1).cross(n_vec)
        else:
            u = FreeCAD.Vector(1, 0, 0).cross(n_vec)
        if u.Length < 1e-6:
            u = FreeCAD.Vector(1, 0, 0)
        u.normalize()
        v = n_vec.cross(u)
        pts = []
        for i in range(n + 1):
            a = 2.0 * math.pi * i / n
            pts.append(center + u * (radius * math.cos(a)) + v * (radius * math.sin(a)))
        return pts

    def _update_pipe_handles(self):
        if not self._segs_3d:
            return
        r = self._compute_handle_radius()

        # Control points (one per segment start)
        ctrl_world = [FreeCAD.Vector(*seg[0]) for seg in self._segs_3d]
        if not getattr(self._curve_obj, "Closed", False):
            ctrl_world.append(FreeCAD.Vector(*self._segs_3d[-1][3]))

        while len(self._ctrl_fld_pts) < len(ctrl_world):
            ctrl_pt = FldPoint(ctrl_world[len(self._ctrl_fld_pts)])
            ctrl_pt.draw_point(self.points_root, r, color=(0.3, 0.8, 1.0))
            self._ctrl_fld_pts.append(ctrl_pt)
        for i, pt in enumerate(ctrl_world):
            self._ctrl_fld_pts[i].position = pt
            self._ctrl_fld_pts[i].update_draw(radius=r)

        # Path wire
        is_closed = getattr(self._curve_obj, "Closed", False)
        wire_pts = []
        for seg in self._segs_3d:
            wire_pts.extend(self._sample_seg_3d_world(*seg))
        if is_closed and wire_pts:
            wire_pts.append(wire_pts[0])

        if self._path_wire is None:
            self._path_wire = FldLineSet(self.points_root, color=(0.3, 0.8, 1.0), width=2.0)
        self._path_wire.update_lines(wire_pts)

        # Radius circle at the start of the first segment, perpendicular to its tangent
        p0 = FreeCAD.Vector(*self._segs_3d[0][0])
        p1 = FreeCAD.Vector(*self._segs_3d[0][1])
        tangent = p1 - p0
        circle = self._circle_pts(p0, tangent, self._radius)
        if self._radius_circle is None:
            self._radius_circle = FldLineSet(self.points_root, color=(0.3, 0.8, 1.0), width=1.5)
        self._radius_circle.update_lines(circle)

    def _pipe_field(self):
        if not self._segs_3d:
            return None
        from freecad.fields.core.sdf.sdf_pipe import SdfPipeField
        if self._cached_pipe is None:
            self._cached_pipe = SdfPipeField(self._segs_3d, self._radius)
        else:
            self._cached_pipe.radius = self._radius
        return self._cached_pipe

    def _get_preview_field(self):      return self._pipe_field()
    def _get_edit_preview_field(self): return self._pipe_field()
    def _get_final_field(self):        return self._pipe_field()

    def _get_final_points(self):
        return [FreeCAD.Vector(self._radius, 0.0, 0.0)] if self._curve_obj else None

    def get_parameters(self):
        return {"Radius": self._radius}

    def _apply_parameters(self, params):
        self._radius = self._clamp_radius(float(params.get("Radius", 3.0)))
        if self._cached_pipe is not None:
            self._cached_pipe.radius = self._radius
        self._update_pipe_handles()
        return True

    def _primitive_name(self):
        return "CurvePipe"

    def finish(self):
        """One-shot tool: commit then terminate."""
        if getattr(self, "_is_editing", False):
            super().finish()
            self._finished = True
            self.terminate()
        elif self.is_in_progress():
            self._finalize_object(self._primitive_name(), terminate=True)
        else:
            self.terminate()


class SdfCurveFillExtrudeCreator(PrimitiveCreatorBase):
    """
    Extrudes a selected SDF face (FldSurface) into a solid SDF using SdfCurveFillExtrusionField.
    Shows two curve rings (base + top) representing the boundary and lets the user drag to set depth.
    """

    CREATION_STEPS = [ToolState.DRAG_Z]

    # Geometry is anchored to the linked source object — the panel must not move it.
    SUPPORTS_ORIGIN_EDIT = False

    def get_command_id(self):
        return "Fields_ExtrudeCurve"

    def get_sdf_type(self):
        return "surface_extrude"

    def _get_source_curve_link(self):
        return None

    def __init__(self):
        super().__init__()
        self._surface_obj    = None
        self._curve_obj      = None
        self._height         = 5.0
        self._bezier_segs    = None
        self._segs_3d        = None
        self._cached_extrude = None

        self.gizmo = ExtrudeGizmo(self.points_root)

        import FreeCADGui
        for obj in FreeCADGui.Selection.getSelection():
            if getattr(obj, "ShapeType", None) == "surface":
                self._surface_obj = obj
                break

        if self._surface_obj is not None:
            self._curve_obj = getattr(self._surface_obj, "SourceCurve", None)
            surf_name = self._surface_obj.Name
            curve_name = self._curve_obj.Name if self._curve_obj else "None"
            fld_logger.info(f"SdfCurveFillExtrudeCreator.__init__: resolved _surface_obj={surf_name}, _curve_obj={curve_name}")
            if self._curve_obj is not None:
                from freecad.fields.core.sdf.curve_sampler import (
                    compute_best_fit_placement,
                    extract_bezier_segments_in_placement,
                    sample_curve_world_pts,
                    extract_bezier_segments_3d,
                )
                try:
                    pts_world = sample_curve_world_pts(self._curve_obj, n_samples=64)
                except Exception:
                    pts_world = [
                        self._curve_obj.Placement.multVec(p)
                        for p in list(getattr(self._curve_obj, "Points", []))
                    ]
                best_fit = compute_best_fit_placement(
                    pts_world, fallback_placement=self._curve_obj.Placement
                ) if len(pts_world) >= 3 else self._curve_obj.Placement

                self.working_plane              = best_fit
                self._working_plane_is_fallback = False
                self._anchor_pt                 = best_fit.Base
                
                self._bezier_segs               = extract_bezier_segments_in_placement(
                    self._curve_obj, best_fit
                )
                self._segs_3d                   = extract_bezier_segments_3d(self._curve_obj)
                self._update_extrude_handles()

    def edit_object(self, obj):
        super().edit_object(obj)
        linked_surf = getattr(obj, "SourceSurfaceLink", None)
        if linked_surf is not None or self._surface_obj is None:
            self._surface_obj = linked_surf

        if self._surface_obj is not None:
            self._curve_obj = getattr(self._surface_obj, "SourceCurve", None)
        else:
            self._curve_obj = None

        surf_name = self._surface_obj.Name if self._surface_obj else "None"
        curve_name = self._curve_obj.Name if self._curve_obj else "None"
        fld_logger.info(f"SdfCurveFillExtrudeCreator.edit_object: resolved _surface_obj={surf_name}, _curve_obj={curve_name}")

        pts = getattr(obj, "Points", [])
        if pts:
            self._height = pts[0].z
        else:
            self._height = 5.0

        if self._curve_obj is not None:
            from freecad.fields.core.sdf.curve_sampler import (
                compute_best_fit_placement,
                extract_bezier_segments_in_placement,
                sample_curve_world_pts,
                extract_bezier_segments_3d,
            )
            try:
                pts_world = sample_curve_world_pts(self._curve_obj, n_samples=64)
            except Exception as e:
                fld_logger.warn(
                    f"sample_curve_world_pts failed on "
                    f"{getattr(self._curve_obj, 'Label', '?')} ({e}); falling back "
                    f"to raw control points, which do not lie on the curve -- the "
                    f"fitted plane will be approximate."
                )
                pts_world = [
                    self._curve_obj.Placement.multVec(p)
                    for p in list(getattr(self._curve_obj, "Points", []))
                ]
            best_fit = compute_best_fit_placement(
                pts_world, fallback_placement=self._curve_obj.Placement
            ) if len(pts_world) >= 3 else self._curve_obj.Placement

            self.working_plane              = best_fit
            self._working_plane_is_fallback = False
            self._anchor_pt                 = best_fit.Base
            
            self._bezier_segs               = extract_bezier_segments_in_placement(
                self._curve_obj, best_fit
            )
            self._segs_3d                   = extract_bezier_segments_3d(self._curve_obj)

        self.state = ToolState.IDLE
        self._update_extrude_handles()
        self.update_preview()
        self.update_ui()

    def _detect_selected_workplane(self):
        pass

    def _clamp_height(self, h):
        return max(0.1, h)

    def _on_stage_accept(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            norm = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            self._height = self._clamp_height(abs((pos - self._anchor_pt).dot(norm)))
            self._update_extrude_handles()

    def _on_stage_preview(self, state, pos):
        if state == ToolState.DRAG_Z and pos and self._anchor_pt:
            norm = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            self._height = self._clamp_height(abs((pos - self._anchor_pt).dot(norm)))
            self._update_extrude_handles()

    def _update_extrude_handles(self):
        """Draw the base ring on the curve itself, and the top ring one height above it.

        The ring comes from the WORLD-space Bezier segments, with their handles, not
        from `_bezier_segs`. Those are the curve projected into the best-fit plane with
        z dropped, which is the right profile for the prism walls but the wrong gizmo:
        drawn, it flattens a 3D curve onto its own best-fit plane and joins the segment
        starts with chords, so the handles sit off the curve and the edges cut every
        corner. The solid is unaffected either way -- the caps carry the z variation.
        """
        if not self._segs_3d or not self.working_plane:
            self.gizmo.clear()
            return
        norm   = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        r      = self._compute_handle_radius()

        base_ctrl, base_handles, top_ctrl, top_handles = extrude_rings_from_segments_3d(
            self._segs_3d, norm * self._height
        )
        self.gizmo.update(base_ctrl, top_ctrl, base_handles, top_handles, handle_radius=r)

    def _offset_placement(self):
        """Working plane shifted height/2 along normal — centers the ±height/2 extrusion."""
        norm = self.working_plane.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
        return FreeCAD.Placement(
            self.working_plane.Base + norm * (self._height * 0.5),
            self.working_plane.Rotation,
        )

    def _update_field_inplace(self, field):
        """Retarget the cached field to the current drag height, keeping id() stable.

        Only the height moves. Unlike SdfExtrusionField, SdfCurveFillExtrusionField is not
        evaluated through `placement`/`inv_matrix` -- it lives in the curve's own best-fit
        frame and spans [0, height] along that frame's axis -- so writing a recentred
        placement here moved nothing and only made it look as though it had.
        """
        field.height = self._height

    def _extrude_field(self):
        if self._height < MIN_PRIMITIVE_DIM_TYPED_MM or not self._surface_obj:
            return None
        from freecad.fields.core.sdf.sdf_curve_fill_extrusion import SdfCurveFillExtrusionField

        if self._cached_extrude is None:
            self._cached_extrude = SdfCurveFillExtrusionField(
                self._surface_obj,
                height=self._height,
                placement=self._offset_placement(),
            )
        else:
            self._update_field_inplace(self._cached_extrude)

        return self._cached_extrude

    def _get_preview_field(self):      return self._extrude_field()
    def _get_edit_preview_field(self): return self._extrude_field()
    def _get_final_field(self):        return self._extrude_field()

    def _get_final_points(self):
        return [FreeCAD.Vector(0.0, 0.0, self._height)] if self._surface_obj else None

    def get_parameters(self):
        return {"Height": self._height}

    def _apply_parameters(self, params):
        self._height = self._clamp_height(float(params.get("Height", 10.0)))
        if self._cached_extrude is not None:
            self._update_field_inplace(self._cached_extrude)
        self._update_extrude_handles()
        return True

    def _primitive_name(self):
        return "SurfaceExtrude"

    def _on_committed(self, obj):
        try:
            super()._on_committed(obj)
        except Exception as e:
            fld_logger.error(f"SdfCurveFillExtrudeCreator._on_committed: super check failed: {e}")

        surf_name = self._surface_obj.Name if self._surface_obj else "None"
        curve_name = self._curve_obj.Name if self._curve_obj else "None"
        fld_logger.debug(f"SdfCurveFillExtrudeCreator._on_committed: finished tool. _surface_obj={surf_name}, _curve_obj={curve_name}")

        if self._surface_obj is not None or self._curve_obj is not None:
            QtCore.QTimer.singleShot(0, self._hide_surface_object_deferred)

    def _hide_surface_object_deferred(self):
        if self._surface_obj is not None:
            try:
                if hasattr(self._surface_obj, "ViewObject") and self._surface_obj.ViewObject:
                    self._surface_obj.ViewObject.Visibility = False
            except Exception as e:
                fld_logger.error(f"SdfCurveFillExtrudeCreator: Failed to hide SDF face '{self._surface_obj.Name}': {e}")

        if self._curve_obj is not None:
            try:
                if hasattr(self._curve_obj, "ViewObject") and self._curve_obj.ViewObject:
                    self._curve_obj.ViewObject.Visibility = False
            except Exception as e:
                fld_logger.error(f"SdfCurveFillExtrudeCreator: Failed to hide source curve '{self._curve_obj.Name}': {e}")

        if self.view:
            try:
                self.view.redraw()
            except Exception as e:
                fld_logger.debug(f"SdfCurveFillExtrudeCreator: redraw failed: {e}")

    def _do_terminate(self):
        self.gizmo.clear()
        super()._do_terminate()
