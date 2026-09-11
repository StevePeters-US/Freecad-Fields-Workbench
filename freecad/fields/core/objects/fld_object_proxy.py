# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/fld_object_proxy.py

FldObjectProxy — the App-level Part::FeaturePython document object proxy for
Fields objects (NURBS curves/surfaces, points, SDF primitives).
Owns parametric properties, build_shape()/execute() recompute logic, and
SdfField caching. See core/objects/fld_view_provider.py for the paired
Gui-level ViewProvider.

CR-061 split the curve-fill/patch geometry math out to
`fld_curve_fill_geometry.py` and the surface-id allocator/stamp/lookup out to
`fld_surface_id.py` -- both re-imported below since this class still calls them.
"""

import FreeCAD
import FreeCADGui
import Part
import numpy as np

from freecad.fields.core import fld_logger
from freecad.fields.core.objects.fld_object import get_show_wireframe
from freecad.fields.core.sdf.sdf_constants import SURFACE_ID_UNSET
from freecad.fields.core.objects.fld_curve_fill_geometry import (
    _triangles_to_preview_shape, _curve_plane_and_local_points,
    _disk_mesh_over_height_field, _patch_mesh_resolution,
    _fit_bspline_surface_to_height_field, _to_local_frame,
    _surface_to_patch_spec, _find_curve_loops, _planar_face_from_wire,
    fill_is_planar,
)
from freecad.fields.core.objects.fld_surface_id import allocate_surface_ids, stamp_surface_id


def _placement_at(placement, local_pt):
    """Return `placement` shifted so its origin sits on `local_pt`.

    The workbench convention is that a primitive's first stored point is its
    origin. Fields built around their local origin (SdfRevolutionField spins
    about local Z at (0,0); SdfExtrusionField spans +/-h/2 about local z=0)
    have no centre parameter, so the frame carries the offset instead. Rotation
    is untouched.
    """
    if placement is None:
        return FreeCAD.Placement(FreeCAD.Vector(local_pt), FreeCAD.Rotation())
    return FreeCAD.Placement(placement.multVec(FreeCAD.Vector(local_pt)),
                             placement.Rotation)


class FldObjectProxy:
    def __init__(self, obj, shape_type, params=None, placement=None):
        obj.Proxy = self
        
        if not hasattr(obj, "ShapeType"):
            obj.addProperty("App::PropertyString", "ShapeType", "Fields", "Type of primitive")
        obj.ShapeType = shape_type
        
        if not hasattr(obj, "SurfaceIdBase"):
            obj.addProperty("App::PropertyInteger", "SurfaceIdBase", "Fields", "Monotonic surface ID base")
            doc = getattr(obj, "Document", None)
            if doc is not None:
                obj.SurfaceIdBase = allocate_surface_ids(doc, 1)
            else:
                obj.SurfaceIdBase = SURFACE_ID_UNSET

        from freecad.fields.core import fld_logger
        # fld_logger.debug(f"FldObjectProxy.__init__: type={shape_type}, has_placement={placement is not None}")
        
        if placement:
            obj.Placement = placement



        # Add typed properties for parametric editing
        params = params or {}
        if shape_type == "curve":
            if not hasattr(obj, "ControlNodes"):
                obj.addProperty("App::PropertyLinkList", "ControlNodes", "Curve", "Shared point nodes")
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "Curve", "Spline fit points")
            if not hasattr(obj, "HandleIn"):
                obj.addProperty("App::PropertyVectorList", "HandleIn", "Curve", "Inbound tangent handles")
            if not hasattr(obj, "HandleOut"):
                obj.addProperty("App::PropertyVectorList", "HandleOut", "Curve", "Outbound tangent handles")
            if not hasattr(obj, "Closed"):
                obj.addProperty("App::PropertyBool", "Closed", "Curve", "Whether the curve is periodic")
            if not hasattr(obj, "PointTypes"):
                obj.addProperty("App::PropertyIntegerList", "PointTypes", "Curve", "Control point types (0=Tangent, 1=Split, 2=Custom)")
            if not hasattr(obj, "HandleTypes"):
                obj.addProperty("App::PropertyIntegerList", "HandleTypes", "Curve",
                    "Per-handle type: 0=Auto (Catmull-Rom), 1=Vector (toward neighbor), "
                    "2=Aligned (collinear, user-editable lengths), 3=Free (independent). "
                    "Length = 2*Points (In0, Out0, In1, Out1, ...)")
            if not hasattr(obj, "EditMode"):
                obj.addProperty("App::PropertyBool", "EditMode", "Curve", "Whether the object is in interactive edit mode. Controls control cage visibility.")
                obj.EditMode = False
            obj.Points = params.get("Points", [])
            
            # Ensure handles are lists of Vectors, never None
            pts = params.get("Points", [])
            h_in = params.get("HandleIn", [])
            h_out = params.get("HandleOut", [])
            p_types = params.get("PointTypes", [])
            h_types = params.get("HandleTypes", [])
            
            # Fill missing handles with the point itself (zero-length handle)
            in_vals = []
            out_vals = []
            pt_vals = []
            ht_vals = []
            for i, p in enumerate(pts):
                in_vals.append(h_in[i] if (i < len(h_in) and h_in[i] is not None) else p)
                out_vals.append(h_out[i] if (i < len(h_out) and h_out[i] is not None) else p)
                pt_vals.append(p_types[i] if (i < len(p_types) and p_types[i] is not None) else 0)
                
                # Each point i has two handles: In at 2*i, Out at 2*i+1
                ht_vals.append(h_types[2*i] if (2*i < len(h_types) and h_types[2*i] is not None) else 0)
                ht_vals.append(h_types[2*i+1] if (2*i+1 < len(h_types) and h_types[2*i+1] is not None) else 0)
            
            obj.HandleIn = in_vals
            obj.HandleOut = out_vals
            obj.PointTypes = pt_vals
            obj.HandleTypes = ht_vals
            # "Closed" is the property name, and what every producer of these params
            # writes. This read used to be params["is_closed"], so a curve created
            # closed came out open -- the slice tool had worked around it by passing
            # both spellings.
            obj.Closed = bool(params.get("Closed", False))

        if shape_type == "point":
            if not hasattr(obj, "Position"):
                obj.addProperty("App::PropertyVector", "Position", "Point", "Position")
            obj.Position = params.get("Position", FreeCAD.Vector(0,0,0))
            if not hasattr(obj, "Coordinates"):
                obj.addProperty("App::PropertyVector", "Coordinates", "Base", "3D coordinates of node")
            obj.Coordinates = params.get("Coordinates", params.get("Position", FreeCAD.Vector(0, 0, 0)))
            if not hasattr(obj, "EditMode"):
                obj.addProperty("App::PropertyBool", "EditMode", "Point", "Whether the point is in interactive edit mode")
                obj.EditMode = False
        elif shape_type == "surface":
            if "Thickness" not in obj.PropertiesList:
                obj.addProperty("App::PropertyFloat", "Thickness", "SDF Face", "Thickness of the thin face SDF (mm)")
                obj.Thickness = params.get("Thickness", 0.1)
            if "BoundaryCurves" not in obj.PropertiesList:
                obj.addProperty("App::PropertyLinkList", "BoundaryCurves", "SDF Face", "Boundary connection curves")
            if "ControlGrid" not in obj.PropertiesList:
                obj.addProperty("App::PropertyVectorList", "ControlGrid", "SDF Face", "Control point grid")
                obj.addProperty("App::PropertyInteger", "UCount", "SDF Face", "Width of grid")
                obj.addProperty("App::PropertyInteger", "VCount", "SDF Face", "Height of grid")
                obj.addProperty("App::PropertyLink", "SourceCurve", "SDF Face", "The curve this surface depends on")
            if "Iterations" not in obj.PropertiesList:
                obj.addProperty("App::PropertyInteger", "Iterations", "SDF Face", "Number of Laplacian iterations")
                obj.Iterations = params.get("Iterations", 200)
            if "CenterOffset" not in obj.PropertiesList:
                obj.addProperty("App::PropertyVector", "CenterOffset", "SDF Face", "Offset for the center point/centroid")
                obj.CenterOffset = params.get("CenterOffset", FreeCAD.Vector(0.0, 0.0, 0.0))
            if "Tension" not in obj.PropertiesList:
                obj.addProperty("App::PropertyFloat", "Tension", "SDF Face", "Tension of the surface (0.0 to 1.0)")
                obj.Tension = params.get("Tension", 1.0)
            if "ShowExtension" not in obj.PropertiesList:
                obj.addProperty("App::PropertyBool", "ShowExtension", "SDF Face", "Whether to show the infinite extension of the SDF face")
                obj.ShowExtension = params.get("ShowExtension", False)
            
            if "SourceCurve" in params:
                obj.SourceCurve = params["SourceCurve"]
            
            grid = params.get("ControlGrid", [[]])
            if grid and grid[0]:
                obj.UCount = len(grid[0])
                obj.VCount = len(grid)
                flat_list = [p for row in grid for p in row]
                obj.ControlGrid = flat_list

        if shape_type == "sdf":
            if not hasattr(obj, "ShowWireframe"):
                obj.addProperty("App::PropertyBool", "ShowWireframe", "Sdf", "Show triangle wireframe")
                obj.ShowWireframe = get_show_wireframe()
            # Migrate legacy IsSubtractive bool to Group enum
            if hasattr(obj, "IsSubtractive"):
                was_sub = bool(obj.IsSubtractive)
                obj.removeProperty("IsSubtractive")
            else:
                was_sub = False
            if not hasattr(obj, "Group"):
                obj.addProperty("App::PropertyEnumeration", "Group", "Sdf",
                                "Rendering group: Additive (orange) or Subtractive (blue)")
                obj.Group = ["Additive", "Subtractive"]
                obj.Group = "Subtractive" if was_sub else "Additive"

            # Pre-add Points so it is always present and serialized; value is set at commit time.
            if not hasattr(obj, "Points"):
                obj.addProperty("App::PropertyVectorList", "Points", "Sdf", "Control Points")

    def get_or_solve_height_field(self, fp, resolution=256):
        """
        Solves or retrieves the cached boundary height field for a FldSurface object.
        Returns (grid_array, bounds, is_planar).
        """
        source_curve = getattr(fp, "SourceCurve", None)
        if source_curve is None:
            source_curve = fp

        if source_curve is not None:
            try:
                from freecad.fields.core.sdf.curve_sampler import sample_curve_world_pts, compute_best_fit_placement
                world_pts = sample_curve_world_pts(source_curve, n_samples=64)
            except Exception:
                world_pts = [
                    source_curve.Placement.multVec(p)
                    for p in list(getattr(source_curve, "Points", []))
                ]
            best_fit = compute_best_fit_placement(world_pts, fallback_placement=getattr(source_curve, "Placement", FreeCAD.Placement()))
            curve_plane = best_fit if best_fit is not None else getattr(source_curve, "Placement", FreeCAD.Placement())
        else:
            world_pts = []
            curve_plane = FreeCAD.Placement()

        m_inv = curve_plane.inverse()
        lp_pts = [m_inv.multVec(pt) for pt in world_pts] if world_pts else []

        is_planar = fill_is_planar(lp_pts)

        if is_planar:
            fld_logger.info("SDF face: planar fast path")
            return (None, None, True)

        control_grid = getattr(fp, "ControlGrid", None)
        # Tension picks the operator the fill is solved with, so it belongs in the key --
        # left out, the first solve would be handed back for every later value and the
        # slider would look dead, which is exactly how the removed controls behaved.
        tension = float(getattr(fp, "Tension", 1.0))
        b_key = tuple((round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in world_pts)
        c_key = tuple((round(p.x, 6), round(p.y, 6), round(p.z, 6)) for p in (control_grid or []))
        # UX-008 put the projection axis inside curve_plane's ROTATION, and two
        # projections of the same curve share a Base -- so the rotation has to be in
        # the key or the second one is served the first one's grid.
        _axes = [curve_plane.Rotation.multVec(FreeCAD.Vector(*a))
                 for a in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]
        axes_key = tuple(round(c, 6) for w in _axes for c in (w.x, w.y, w.z))
        key = (b_key, c_key, int(resolution), round(tension, 6),
               (round(curve_plane.Base.x, 6), round(curve_plane.Base.y, 6), round(curve_plane.Base.z, 6)),
               axes_key)

        if getattr(self, "_hmap_cache_key", None) == key and getattr(self, "_hmap_cache_data", None) is not None:
            return self._hmap_cache_data

        from freecad.fields.core.sdf.sdf_curve_fill_extrusion import solve_boundary_height_field
        try:
            grid, bounds = solve_boundary_height_field(source_curve, curve_plane, control_grid=control_grid,
                                                       resolution=resolution, tension=tension)
            fld_logger.info(f"SDF face: solved height field grid {grid.shape}")
            res = (grid, bounds, False)
        except ValueError as ve:
            # UX-008 step 3. `_curve_plane_and_local_points` already tried all three
            # PCA axes, so reaching here means the curve is knotted: warn once, name
            # the object, and take the planar fill. Cached like a success so a
            # knotted curve does not re-solve on every rebuild.
            fld_logger.warn(f"SDF face {getattr(fp, 'Name', '?')}: {ve}")
            res = (None, None, True)

        self._hmap_cache_key = key
        self._hmap_cache_data = res
        return res

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""

        
        st = fp.ShapeType
        if st == "curve":
            from .fld_curve import FldCurve
            from .fld_point import FldPoint
            
            if hasattr(fp, "ControlNodes") and fp.ControlNodes:
                inv_pl = fp.Placement.inverse()
                pts = [inv_pl.multVec(node.Coordinates) for node in fp.ControlNodes if node is not None]
            else:
                pts = fp.Points
            h_in = fp.HandleIn if hasattr(fp, "HandleIn") else []
            h_out = fp.HandleOut if hasattr(fp, "HandleOut") else []
            
            fld_points = []
            for i, p in enumerate(pts):
                hi = h_in[i] if i < len(h_in) else None
                ho = h_out[i] if i < len(h_out) else None
                fld_points.append(FldPoint(p, handle_in=hi, handle_out=ho))
                
            is_closed = fp.Closed if hasattr(fp, "Closed") else False
            curve = FldCurve(fld_points, is_closed=is_closed)
            return curve.to_shape()
        elif st == "point":
            coords = fp.Coordinates if hasattr(fp, "Coordinates") else fp.Position
            return Part.makeSphere(0.5, coords)
        elif st == "surface":
            grid, bounds, is_planar = self.get_or_solve_height_field(fp)

            source_curve = getattr(fp, "SourceCurve", None)
            if is_planar:
                # The document object carries its geometry on `Shape`. It has no
                # `to_shape()` -- that is `FldCurve`, the geometry class this proxy
                # builds curves WITH, not the object it hangs them on. Guarding on
                # `to_shape` here was a guard nothing could satisfy, so every planar
                # fill returned the null shape below: no face to see, none to pick,
                # and no `untrimmed_shape`, which silently made ShowExtension a no-op.
                if source_curve is not None and getattr(source_curve, "Shape", None) is not None:
                    wire_shape = source_curve.Shape
                    if not wire_shape.isNull() and wire_shape.Edges:
                        try:
                            # `source_curve.Shape` is world-space; whatever is returned
                            # here is read back in fp.Placement's frame, so the curve's
                            # placement would otherwise be applied twice.
                            wire_shape = wire_shape.copy()
                            wire_shape.Placement = fp.Placement.inverse().multiply(
                                wire_shape.Placement)
                            wire = Part.Wire(wire_shape.Edges)
                            # A true Part.Plane where OCC will accept one; see
                            # `_planar_face_from_wire` for why a filled plate is
                            # neither flat enough nor cheap enough to be the default.
                            face = _planar_face_from_wire(wire)
                            if face is not None and not face.isNull():
                                self.untrimmed_shape = self._planar_extension_shape(fp, source_curve)
                                return face
                        except Exception as e:
                            fld_logger.debug(f"Planar picking face creation failed: {e}")
                return Part.Shape()

            if grid is not None and bounds is not None:
                try:
                    curve_plane, lp_pts = _curve_plane_and_local_points(source_curve)
                    if len(lp_pts) < 3:
                        return Part.Shape()

                    min_x, max_x, min_y, max_y = bounds
                    res_y, res_x = grid.shape
                    from scipy.interpolate import RegularGridInterpolator
                    interp = RegularGridInterpolator(
                        (np.linspace(min_y, max_y, res_y), np.linspace(min_x, max_x, res_x)),
                        grid, bounds_error=False, fill_value=0.0)

                    def height_of(x, y):
                        return interp(np.column_stack([np.ravel(y), np.ravel(x)]))

                    # U is angular and V radial, matching the disk parameterisation the
                    # tool's resolution sliders were written against.
                    u_res, v_res = _patch_mesh_resolution(fp)
                    verts, tris, u_res = _disk_mesh_over_height_field(
                        lp_pts, height_of, u_res, v_res)

                    # The mesh is solved in the best-fit plane's frame; fp.Placement is
                    # the curve's, and the two differ whenever the curve is off-plane.
                    local = _to_local_frame(verts, curve_plane, fp.Placement)

                    # UX-007: Smooth B-spline surface over rectangular height field domain,
                    # trimmed with the 2D boundary wire extruded along local Z.
                    self.untrimmed_shape = self._extended_patch_shape(local, u_res, v_res)
                    bspline_face = _fit_bspline_surface_to_height_field(
                        grid, bounds, lp_pts, curve_plane, fp.Placement
                    )
                    if bspline_face is not None and not bspline_face.isNull():
                        return bspline_face

                    fld_logger.warn(
                        f"B-spline surface fit failed for {getattr(fp, 'Label', 'surface')}; "
                        "falling back to faceted preview mesh."
                    )
                    return _triangles_to_preview_shape(local, tris)
                except Exception as e:
                    fld_logger.debug(f"Non-planar picking mesh creation failed: {e}")
            return Part.Shape()

    def _planar_extension_shape(self, fp, source_curve):
        """ShowExtension overlay for a flat patch: its own plane, carried outwards."""
        curve_plane, lp_pts = _curve_plane_and_local_points(source_curve)
        if len(lp_pts) < 3:
            return Part.Shape()
        u_res, v_res = _patch_mesh_resolution(fp)
        verts, _, u_res = _disk_mesh_over_height_field(
            lp_pts, lambda x, y: np.zeros_like(np.ravel(x)), u_res, v_res)
        local = _to_local_frame(verts, curve_plane, fp.Placement)
        return self._extended_patch_shape(local, u_res, v_res)

    @staticmethod
    def _extended_patch_shape(local_verts, u_res, v_res):
        """Shape for the ShowExtension overlay, or a null shape if it cannot be built.

        The overlay reads `proxy.untrimmed_shape`, so leaving it unset silently turns
        the checkbox into a no-op. Built from the outer rings of the disk mesh, which
        are a ring grid in exactly the layout `to_extended_shape` expects.
        """
        try:
            from .fld_surface import FldSurface
            rings = []
            for j in range(max(0, v_res - 2), v_res):
                row = [FreeCAD.Vector(*local_verts[1 + j * u_res + i]) for i in range(u_res)]
                rings.append(row + [row[0]])
            if len(rings) < 2:
                return Part.Shape()
            return FldSurface(rings).to_extended_shape(
                extra_v=max(2, v_res // 4), is_u_periodic=True)
        except Exception as e:
            fld_logger.debug(f"Patch extension shape failed: {e}")
            return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from freecad.fields.core import fld_logger

            st = fp.ShapeType if hasattr(fp, "ShapeType") else "nurbs"

            if st == "sdf":
                # Parametric boolean recompute
                if hasattr(fp, "BooleanInputs") and fp.BooleanInputs:
                    try:
                        from freecad.fields.core.sdf.sdf_boolean_compose import _recompose_boolean
                        new_field = _recompose_boolean(fp)
                        if new_field is not None:
                            self.SdfField = new_field
                            # Push updated field to renderer
                            from freecad.fields.core.render.fld_renderer import SdfRendererStrategy
                            vp = getattr(fp, "ViewObject", None)
                            vp_proxy = getattr(vp, "Proxy", None) if vp else None
                            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
                            if strategy and hasattr(strategy, "label") and strategy.label:
                                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                                FldSceneVoxelRenderer.get_instance().update_field(
                                    strategy.label, new_field
                                )
                    except Exception as e:
                        from freecad.fields.core import fld_logger
                        fld_logger.error(f"Boolean recompute failed for {fp.Label}: {e}")

                elif getattr(fp, "SdfType", None) == "cage" and hasattr(fp, "Members") and fp.Members:
                    try:
                        patch_specs = []
                        surfaces = [m for m in fp.Members if m and getattr(m, "ShapeType", None) == "surface"]
                        curves = [m for m in fp.Members if m and getattr(m, "ShapeType", None) == "curve"]

                        for surf in surfaces:
                            spec = _surface_to_patch_spec(surf)
                            if spec:
                                patch_specs.append(spec)

                        loops = _find_curve_loops(curves)
                        for chained in loops:
                            corners = []
                            patch_handles = []
                            for curve, is_reversed in chained:
                                c_start = curve.value(1.0) if is_reversed else curve.value(0.0)
                                h0 = curve.value(2.0/3.0) if is_reversed else curve.value(1.0/3.0)
                                h1 = curve.value(1.0/3.0) if is_reversed else curve.value(2.0/3.0)
                                pl = curve.Placement
                                if pl:
                                    c_start = pl.multVec(c_start)
                                    h0 = pl.multVec(h0)
                                    h1 = pl.multVec(h1)
                                corners.append([c_start.x, c_start.y, c_start.z])
                                patch_handles.append([h0.x, h0.y, h0.z])
                                patch_handles.append([h1.x, h1.y, h1.z])
                            spec = {
                                "corners": np.array(corners, dtype=np.float64),
                                "handles": np.array(patch_handles, dtype=np.float64)
                            }
                            patch_specs.append(spec)

                        if patch_specs:
                            from freecad.fields.core.sdf.sdf.cage import SdfCageField
                            sign_mode = getattr(fp, "SignMode", "winding")
                            cage = SdfCageField.from_patches(patch_specs, sign_mode=sign_mode)

                            all_pts = list(cage.vertices) + list(cage.handles)
                            new_pts = [FreeCAD.Vector(float(p[0]), float(p[1]), float(p[2])) for p in all_pts]
                            new_fv = [v for face in cage._face_verts for v in face]
                            new_fs = [len(face) for face in cage._face_verts]
                            new_ev = [v for edge in cage._edges for v in edge]
                            new_ht = list(cage._handle_types)
                            new_es = [1 if es else 0 for es in getattr(cage, "_edge_straight", [])]

                            # Assign properties only if they changed to prevent recompute loops
                            if getattr(fp, "Points", None) != new_pts:
                                fp.Points = new_pts
                            if getattr(fp, "FaceVertices", None) != new_fv:
                                fp.FaceVertices = new_fv
                            if getattr(fp, "FaceSizes", None) != new_fs:
                                fp.FaceSizes = new_fs
                            if getattr(fp, "EdgeVertices", None) != new_ev:
                                fp.EdgeVertices = new_ev
                            if getattr(fp, "HandleTypes", None) != new_ht:
                                fp.HandleTypes = new_ht
                            if getattr(fp, "EdgeStraight", None) != new_es:
                                fp.EdgeStraight = new_es

                            self.SdfField = cage

                            # Push updated field to renderer
                            vp = getattr(fp, "ViewObject", None)
                            vp_proxy = getattr(vp, "Proxy", None) if vp else None
                            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
                            if strategy and hasattr(strategy, "label") and strategy.label:
                                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                                FldSceneVoxelRenderer.get_instance().update_field(strategy.label, cage)
                    except Exception as e:
                        from freecad.fields.core import fld_logger
                        fld_logger.error(f"Cage assembly recompute failed for {fp.Label}: {e}")

                elif not getattr(self, "SdfField", None):
                    # Reconstruct primitive field after document load
                    field = self._reconstruct_field(fp)
                    if field is not None:
                        self.SdfField = field
                        vp = getattr(fp, "ViewObject", None)
                        vp_proxy = getattr(vp, "Proxy", None) if vp else None
                        strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
                        if strategy and hasattr(strategy, "label") and strategy.label:
                            try:
                                from freecad.fields.core.render.fld_scene_voxel_renderer import FldSceneVoxelRenderer
                                FldSceneVoxelRenderer.get_instance().update_field(strategy.label, field)
                            except Exception as e:
                                from freecad.fields.core import fld_logger
                                fld_logger.debug(f"update_field on renderer failed: {e}")

                # SDF objects are drawn by FldSceneVoxelRenderer;
                # they never produce native B-Rep / mesh geometry for the preview.
                fp.Shape = Part.Shape()
                return

            new_shape = self.build_shape(fp)
            fp.Shape = new_shape

        except Exception:
            from freecad.fields.core import fld_logger
            fld_logger.exception(f"FldObject.execute error for {fp.Label}")



    def get_sdf_field(self, fp):
        """Returns an SdfField representation of this object."""
        st = getattr(fp, "ShapeType", None)
        res = None
        if st == "sdf":
            field = getattr(self, "SdfField", None)
            if field is None:
                # Memoise the *inputs* that failed, not a bare "it failed" flag.
                # Reconstruction depends only on SdfType and the point count, so
                # an unchanged signature can never produce a different answer --
                # retrying it burns time and floods the log. A half-built object
                # whose points arrive later has a new signature and is retried.
                sig = (getattr(fp, "SdfType", None), len(getattr(fp, "Points", []) or []))
                if getattr(self, "_reconstruct_failed_sig", None) != sig:
                    fld_logger.debug(f"get_sdf_field: '{getattr(fp, 'Label', '?')}' SdfField is None, attempting reconstruction")
                    field = self._reconstruct_field(fp)
                    if field is not None:
                        self.SdfField = field
                        self._reconstruct_failed_sig = None
                        fld_logger.debug(f"get_sdf_field: '{getattr(fp, 'Label', '?')}' reconstructed as {type(field).__name__}")
                    else:
                        self._reconstruct_failed_sig = sig
                        fld_logger.debug(f"get_sdf_field: '{getattr(fp, 'Label', '?')}' reconstruction returned None")
            res = field
            
        elif st == "curve":
            from freecad.fields.core.sdf.sdf.nurbs_curve import SdfNurbsCurveField
            try:
                # During interactive dragging, Shape may be stale (recompute deferred).
                # Build curve directly from Points/Handles if possible.
                if hasattr(fp, "ControlNodes") and fp.ControlNodes:
                    inv_pl = fp.Placement.inverse()
                    pts = [inv_pl.multVec(node.Coordinates) for node in fp.ControlNodes if node is not None]
                else:
                    pts = getattr(fp, "Points", [])
                if pts:
                    from freecad.fields.core.objects.fld_curve import FldCurve
                    from freecad.fields.core.objects.fld_point import FldPoint
                    h_in = getattr(fp, "HandleIn", [])
                    h_out = getattr(fp, "HandleOut", [])
                    fld_pts = []
                    for i, p in enumerate(pts):
                        hi = h_in[i] if i < len(h_in) else None
                        ho = h_out[i] if i < len(h_out) else None
                        fld_pts.append(FldPoint(p, handle_in=hi, handle_out=ho))
                    
                    is_closed = getattr(fp, "Closed", False)
                    fld_curve = FldCurve(fld_pts, is_closed=is_closed)
                    if fld_curve.bspline:
                        rad = getattr(fp, "Radius", 1.0)
                        res = SdfNurbsCurveField(fld_curve, tube_radius=rad, placement=fp.Placement)

                elif fp.Shape and fp.Shape.Edges:
                    from freecad.fields.core.objects.fld_curve import FldCurve
                    fld_curve = FldCurve(bspline=fp.Shape.Edges[0].Curve)
                    rad = getattr(fp, "Radius", 1.0)
                    res = SdfNurbsCurveField(fld_curve, tube_radius=rad, placement=fp.Placement)
            except Exception as e:
                fld_logger.debug(f"get_sdf_field(curve) failed: {e}")

        elif st == "surface":
            # RA-012 resolved 2026-09-01 as "retire": `SdfNurbsSurfaceField` was
            # wired in here, reverted 2026-08-01, and has now been deleted. It was a
            # signed distance to a two-sided INFINITE surface, so `fp.Thickness` stopped
            # meaning anything and the field was negative across one whole side; and its
            # `evaluate_grid` was a per-point Python loop over `projectPoint` that every
            # CPU consumer (octree, mesher, baker) would have paid.
            # `SdfCurveFillExtrusionField` is exact, thick and vectorised. Do not
            # reintroduce a bare surface field here without a thickness term.
            from freecad.fields.core.sdf.sdf_curve_fill_extrusion import SdfCurveFillExtrusionField
            try:
                thickness = float(getattr(fp, "Thickness", 0.1))
                res = SdfCurveFillExtrusionField(fp, height=thickness, placement=fp.Placement)
            except Exception as e:
                fld_logger.debug(f"get_sdf_field(surface) failed: {e}")

        return stamp_surface_id(fp, res)

    def _reconstruct_field(self, fp):
        """Reconstruct an SdfField from stored FreeCAD properties after document load."""
        import math
        pts = list(getattr(fp, "Points", []))
        placement = getattr(fp, "Placement", None)
        sdf_type = getattr(fp, "SdfType", None)

        if not sdf_type:
            fld_logger.debug(f"_reconstruct_field: '{getattr(fp, 'Label', '?')}' sdf_type not set")
            return None

        fld_logger.debug(f"_reconstruct_field: '{getattr(fp, 'Label', '?')}' sdf_type={sdf_type!r} pts={len(pts)}")

        try:
            if sdf_type == "box" and pts:
                from freecad.fields.core.sdf.sdf.box import SdfBoxField
                min_v = FreeCAD.Vector(min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))
                max_v = FreeCAD.Vector(max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))
                center = (min_v + max_v) / 2.0
                size = max_v - min_v
                return SdfBoxField(center, size, placement=placement)

            elif sdf_type == "sphere" and len(pts) >= 2:
                from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
                radius = (pts[1] - pts[0]).Length
                return SdfSphereField(pts[0], radius, placement=placement)

            elif sdf_type == "cylinder" and len(pts) >= 3:
                from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
                base, r_pt, h_pt = pts[0], pts[1], pts[2]
                radius = math.sqrt((r_pt.x - base.x)**2 + (r_pt.y - base.y)**2)
                height = (h_pt - base).z
                return SdfCylinderField(base, FreeCAD.Vector(0, 0, 1), radius, height, placement=placement)

            elif sdf_type == "torus" and len(pts) >= 3:
                from freecad.fields.core.sdf.sdf.torus import SdfTorusField
                loc_c, loc_r, loc_t = pts[0], pts[1], pts[2]
                major_r = math.sqrt((loc_r.x - loc_c.x)**2 + (loc_r.y - loc_c.y)**2)
                dist_t = math.sqrt((loc_t.x - loc_c.x)**2 + (loc_t.y - loc_c.y)**2)
                tube_r = max(abs(dist_t - major_r), 0.5)
                return SdfTorusField(loc_c, major_r, tube_r, placement=placement)

            elif sdf_type == "prism" and len(pts) >= 4:
                from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
                from freecad.fields.core.sdf.sdf2d.polygon import Sdf2dPolygon
                loc_center, loc_rad, loc_h = pts[0], pts[1], pts[2]
                n = max(3, int(round(pts[3].x)))
                radius = math.sqrt((loc_rad.x - loc_center.x)**2 + (loc_rad.y - loc_center.y)**2)
                half_h = abs(loc_h.z - loc_center.z)
                if radius < 0.1:
                    return None
                # Mirrors PrismCreator._prism_field: profile about (0, 0), frame
                # moved to pts[0] so the prism reloads where its origin says.
                vertices = [
                    (radius * math.cos(2.0 * math.pi * i / n),
                     radius * math.sin(2.0 * math.pi * i / n))
                    for i in range(n)
                ]
                return SdfExtrusionField(Sdf2dPolygon(vertices), height=2.0 * half_h,
                                         placement=_placement_at(placement, loc_center))

            elif sdf_type == "revolve" and len(pts) >= 2:
                from freecad.fields.core.sdf.sdf2d.circle import Sdf2dCircle
                from freecad.fields.core.sdf.sdf_revolution import SdfRevolutionField
                loc_c, loc_r = pts[0], pts[1]
                ring_d = math.sqrt((loc_r.x - loc_c.x)**2 + (loc_r.y - loc_c.y)**2)
                if len(pts) >= 3:
                    loc_t = pts[2]
                    dt = math.sqrt((loc_t.x - loc_c.x)**2 + (loc_t.y - loc_c.y)**2)
                    tube_r = max(abs(dt - ring_d), 0.5)
                else:
                    tube_r = max(ring_d * 0.15, 1.0)
                # Mirrors RevolveCreator._revolve_field: the revolve spins about
                # local Z at the local origin, so pts[0] has to move the frame.
                return SdfRevolutionField(Sdf2dCircle(tube_r), offset=ring_d,
                                          placement=_placement_at(placement, loc_c))

            elif sdf_type in ("curve_extrude", "curve_3d_extrude") and pts:
                source = getattr(fp, "SourceCurveLink", None)
                if source:
                    height = pts[0].z
                    from freecad.fields.core.sdf.curve_sampler import (
                        sample_curve_world_pts, compute_best_fit_placement,
                        extract_bezier_segments_in_placement,
                    )
                    from freecad.fields.core.sdf.sdf2d.bezier_curve import Sdf2dBezierCurve
                    from freecad.fields.core.sdf.sdf_extrusion import SdfExtrusionField
                    world_pts = sample_curve_world_pts(source, n_samples=64)
                    best_fit = compute_best_fit_placement(world_pts, fallback_placement=source.Placement)
                    if best_fit is None:
                        best_fit = source.Placement
                    segs = extract_bezier_segments_in_placement(source, best_fit)
                    if segs:
                        norm = best_fit.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                        center_pl = FreeCAD.Placement(best_fit.Base + norm * (height * 0.5), best_fit.Rotation)
                        return SdfExtrusionField(Sdf2dBezierCurve(segs), height=height, placement=center_pl)

            elif sdf_type == "curve_pipe" and pts:
                source = getattr(fp, "SourceCurveLink", None)
                if source:
                    radius = pts[0].x
                    from freecad.fields.core.sdf.curve_sampler import extract_bezier_segments_3d
                    from freecad.fields.core.sdf.sdf_pipe import SdfPipeField
                    segs = extract_bezier_segments_3d(source)
                    if segs:
                        return SdfPipeField(segs, radius)

            elif sdf_type == "surface_extrude" and pts:
                source = getattr(fp, "SourceSurfaceLink", None)
                if source:
                    height = pts[0].z
                    from freecad.fields.core.sdf.sdf_curve_fill_extrusion import SdfCurveFillExtrusionField
                    norm = placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
                    center_pl = FreeCAD.Placement(placement.Base + norm * (height * 0.5), placement.Rotation)
                    return SdfCurveFillExtrusionField(source, height=height, placement=center_pl)

            elif sdf_type == "cage":
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                import numpy as np
                fv = list(getattr(fp, "FaceVertices", []))
                fs = list(getattr(fp, "FaceSizes",    []))
                if not fs or not fv:  # fallback: standard box topology
                    from freecad.fields.core.sdf.sdf.cage import _BOX_FACES
                    fv = [v for face in _BOX_FACES for v in face]
                    fs = [len(f) for f in _BOX_FACES]
                
                n_verts = max(fv) + 1 if fv else 8
                if len(pts) < n_verts:
                    return None
                
                verts = np.array([[p.x,p.y,p.z] for p in pts[:n_verts]], dtype=np.float64)
                handles = np.array([[p.x,p.y,p.z] for p in pts[n_verts:]], dtype=np.float64)
                
                # Check EdgeVertices property first
                ev = list(getattr(fp, "EdgeVertices", []))
                if ev:
                    edges = [(ev[2*i], ev[2*i+1]) for i in range(len(ev)//2)]
                else:
                    # Dynamic edges list determination fallback
                    if len(fv) == 24 and all(sz == 3 for sz in fs): # octahedron / sphere topology
                        from freecad.fields.core.sdf.sdf.cage import _OCT_EDGES
                        edges = _OCT_EDGES
                    else:
                        from freecad.fields.core.sdf.sdf.cage import _BOX_EDGES
                        edges = _BOX_EDGES
                    
                handle_types = list(getattr(fp, "HandleTypes", []))
                edge_straight = list(getattr(fp, "EdgeStraight", [])) if hasattr(fp, "EdgeStraight") else None
                field = SdfCageField(verts, handles, fv, fs, edges=edges, placement=None, handle_types=handle_types, edge_straight=edge_straight)
                field.sign_mode = getattr(fp, "SignMode", "closest")
                return field

            elif sdf_type == "patch_assembly":
                # CPA-007: Compatibility shim for legacy patch_assembly -> SdfCageField
                from freecad.fields.core.sdf.sdf.cage import SdfCageField
                patches = list(getattr(fp, "Patches", []))
                patch_specs = []
                for patch in patches:
                    spec = _surface_to_patch_spec(patch)
                    if spec:
                        patch_specs.append(spec)
                if patch_specs:
                    return SdfCageField.from_patches(patch_specs, sign_mode="winding", placement=placement)
                return None

            elif sdf_type == "boolean":
                from freecad.fields.core.sdf.sdf_boolean_compose import _recompose_boolean
                return _recompose_boolean(fp)

        except Exception as e:
            fld_logger.debug(f"_reconstruct_field exception for '{getattr(fp, 'Label', '?')}' (type={sdf_type}): {e}")

        fld_logger.debug(f"_reconstruct_field: no match for '{getattr(fp, 'Label', '?')}' (type={sdf_type!r})")
        return None

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def onDocumentRestored(self, obj):
        # Ensure new properties exist on restoration of older files
        try:
            if not hasattr(obj, "SurfaceIdBase"):
                obj.addProperty("App::PropertyInteger", "SurfaceIdBase", "Fields", "Monotonic surface ID base")
                doc = getattr(obj, "Document", None)
                obj.SurfaceIdBase = allocate_surface_ids(doc, 1) if doc else SURFACE_ID_UNSET

            st = getattr(obj, "ShapeType", None)
            if st == "surface":
                if "BoundaryCurves" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyLinkList", "BoundaryCurves", "SDF Face", "Boundary connection curves")
                if "ControlGrid" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyVectorList", "ControlGrid", "SDF Face", "Control point grid")
                    obj.addProperty("App::PropertyInteger", "UCount", "SDF Face", "Width of grid")
                    obj.addProperty("App::PropertyInteger", "VCount", "SDF Face", "Height of grid")
                    obj.addProperty("App::PropertyLink", "SourceCurve", "SDF Face", "The curve this surface depends on")
                # FillType ("Open Cascade" / "Disk Laplacian" / "Coons Patch") named the
                # three fills the boundary height-field solve replaced. Nothing has read
                # it since; a saved file still carries it, so drop it here or the dropdown
                # keeps offering a choice that changes nothing.
                if "FillType" in obj.PropertiesList:
                    obj.removeProperty("FillType")
                if "Iterations" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyInteger", "Iterations", "SDF Face", "Number of Laplacian iterations")
                    obj.Iterations = 200
                if "CenterOffset" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyVector", "CenterOffset", "SDF Face", "Offset for the center point/centroid")
                    obj.CenterOffset = FreeCAD.Vector(0.0, 0.0, 0.0)
                if "Tension" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyFloat", "Tension", "SDF Face", "Tension of the surface (0.0 to 1.0)")
                    obj.Tension = 1.0
                if "ShowExtension" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyBool", "ShowExtension", "SDF Face", "Whether to show the infinite extension of the SDF face")
                    obj.ShowExtension = False
            elif st == "point":
                if "Coordinates" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyVector", "Coordinates", "Base", "3D coordinates of node")
                    obj.Coordinates = getattr(obj, "Position", FreeCAD.Vector(0, 0, 0))
            elif st == "curve":
                if "ControlNodes" not in obj.PropertiesList:
                    obj.addProperty("App::PropertyLinkList", "ControlNodes", "Curve", "Shared point nodes")
            elif st == "sdf":
                if getattr(obj, "Group", None) in ("Group 1", "Group 2"):
                    old_val = obj.Group
                    obj.Group = ["Additive", "Subtractive"]
                    obj.Group = "Subtractive" if old_val == "Group 2" else "Additive"
                if getattr(obj, "SdfType", None) == "cage":
                    if "EdgeVertices" not in obj.PropertiesList:
                        obj.addProperty("App::PropertyIntegerList", "EdgeVertices", "Sdf", "Edge endpoint indices")
                    if "EdgeStraight" not in obj.PropertiesList:
                        obj.addProperty("App::PropertyIntegerList", "EdgeStraight", "Sdf", "Straight edge flags")
                    if "SignMode" not in obj.PropertiesList:
                        obj.addProperty("App::PropertyEnumeration", "SignMode", "Sdf", "Inside/Outside sign mode")
                        obj.SignMode = ["closest", "winding"]
                        obj.SignMode = "closest"
                    if "Members" not in obj.PropertiesList:
                        obj.addProperty("App::PropertyLinkList", "Members", "Sdf", "Assembly member patches/curves")
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.error(f"FldObjectProxy.onDocumentRestored failed: {e}")

    def onChanged(self, fp, prop):
        if prop == "Coordinates" and hasattr(fp, "Position"):
            if fp.Position != fp.Coordinates:
                fp.Position = fp.Coordinates
        elif prop == "Position" and hasattr(fp, "Coordinates"):
            if fp.Coordinates != fp.Position:
                fp.Coordinates = fp.Position
        if prop in ("Group",):
            try:
                import FreeCADGui
                view = FreeCADGui.activeView()
                if view:
                    view.redraw()
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"onChanged Group redraw failed: {e}")

