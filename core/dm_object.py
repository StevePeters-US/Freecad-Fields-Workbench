"""
core/dm_object.py

Core Direct Modeling document object. Uses Part::FeaturePython for native
NURBS/BRep rendering.

Factory
-------
    obj = create_dm_object("Box", shape_type="box", params={"length": 10, ...})

The Proxy.execute() reads parametric properties and calls build_shape() to 
generate the native BRep geometry.
"""

import FreeCAD
import Part
try:
    from pivy import coin
except ImportError:
    coin = None

# ─────────────────────────────────────────────────────────────────────────────
# DM Settings helpers
# ─────────────────────────────────────────────────────────────────────────────

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/DirectModeling"


def get_show_wireframe():
    """Return whether to show wireframe for NURBS objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ShowWireframe", False)

def set_show_wireframe(show):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ShowWireframe", bool(show))

def get_line_width():
    """Return the line width for DM objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("LineWidth", 3.0)

def set_line_width(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("LineWidth", float(val))

def get_point_size():
    """Return the point size for DM objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("PointSize", 6.0)

def set_point_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("PointSize", float(val))

def get_frep_storage_type():
    """
    Return the F-Rep storage/meshing approach:
    0: Marching Cubes (Standard SDF)
    1: Adaptive Marching Cubes
    2: NURBS based F-Rep approach
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("FrepStorageType", 0)

def set_frep_storage_type(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("FrepStorageType", int(val))

def get_picking_radius():
    """Return the picking radius in mm."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("PickingRadius", 5.0)

def set_picking_radius(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("PickingRadius", float(val))

def get_max_bounds():
    """Return the maximum field bounds in mm."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("MaxBounds", 10000.0)

def set_max_bounds(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("MaxBounds", float(val))

def get_perf_profiler_enabled():
    """Return whether the performance profiler is enabled."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnablePerfProfiler", False)

def set_perf_profiler_enabled(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnablePerfProfiler", bool(val))




# ─────────────────────────────────────────────────────────────────────────────
# DMObjectProxy — used for NURBS/BRep objects
# ─────────────────────────────────────────────────────────────────────────────

class DMObjectProxy:
    def __init__(self, obj, shape_type, params=None, placement=None):
        obj.Proxy = self
        
        if not hasattr(obj, "ShapeType"):
            obj.addProperty("App::PropertyString", "ShapeType", "DM", "Type of primitive")
        obj.ShapeType = shape_type
        
        from . import dm_logger
        # dm_logger.debug(f"DMObjectProxy.__init__: type={shape_type}, has_placement={placement is not None}")
        
        if placement:
            obj.Placement = placement
        
        # Add typed properties for parametric editing
        params = params or {}
        if shape_type == "curve":
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
                obj.addProperty("App::PropertyIntegerList", "HandleTypes", "Curve", "Handle override types (0=Auto, 1=Manual). Length should be 2*Points (In1, Out1, In2, Out2...)")
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
            obj.Closed = params.get("is_closed", False)

        if shape_type == "point":
            if not hasattr(obj, "Position"):
                obj.addProperty("App::PropertyVector", "Position", "Point", "Position")
            obj.Position = params.get("Position", FreeCAD.Vector(0,0,0))
        elif shape_type == "surface":
            if not hasattr(obj, "ControlGrid"):
                obj.addProperty("App::PropertyVectorList", "ControlGrid", "NURBS", "Control point grid")
                obj.addProperty("App::PropertyInteger", "UCount", "NURBS", "Width of grid")
                obj.addProperty("App::PropertyInteger", "VCount", "NURBS", "Height of grid")
                obj.addProperty("App::PropertyLink", "SourceCurve", "NURBS", "The curve this surface depends on")
            
            obj.SourceCurve = params.get("SourceCurve", None)
            
            grid = params.get("ControlGrid", [[]])
            if grid and grid[0]:
                obj.UCount = len(grid[0])
                obj.VCount = len(grid)
                flat_list = [p for row in grid for p in row]
                obj.ControlGrid = flat_list

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""

        
        st = fp.ShapeType
        if st == "curve":
            # Points are stored as VectorList on the object
            from .nurbs_geometry import DMCurve, DMPoint
            
            pts = fp.Points
            h_in = fp.HandleIn if hasattr(fp, "HandleIn") else []
            h_out = fp.HandleOut if hasattr(fp, "HandleOut") else []
            
            dm_points = []
            for i, p in enumerate(pts):
                hi = h_in[i] if i < len(h_in) else None
                ho = h_out[i] if i < len(h_out) else None
                dm_points.append(DMPoint(p, handle_in=hi, handle_out=ho))
                
            is_closed = fp.Closed if hasattr(fp, "Closed") else False
            curve = DMCurve(dm_points, is_closed=is_closed)
            return curve.to_shape()
        elif st == "point":
            return Part.Point(fp.Position).toShape()
        elif st == "surface":
            from .nurbs_geometry import DMSurface, DMCurve, DMPoint
            
            # If we have a source curve, rebuild the grid dynamically
            if hasattr(fp, "SourceCurve") and fp.SourceCurve:
                curve_obj = fp.SourceCurve
                pts = curve_obj.Points
                h_in = getattr(curve_obj, "HandleIn", [])
                h_out = getattr(curve_obj, "HandleOut", [])
                dm_points = []
                for i, p in enumerate(pts):
                    hi = h_in[i] if i < len(h_in) else None
                    ho = h_out[i] if i < len(h_out) else None
                    dm_points.append(DMPoint(p, handle_in=hi, handle_out=ho))
                
                curve = DMCurve(dm_points, is_closed=getattr(curve_obj, "Closed", False))
                
                # Coons Patch segments
                class CurveSegment:
                    def __init__(self, curve, t_start, t_end):
                        self.curve = curve
                        self.t_start = t_start
                        self.t_end = t_end
                    def value(self, t):
                        mapped_t = self.t_start + t * (self.t_end - self.t_start)
                        return self.curve.value(mapped_t % 1.0)

                c1 = CurveSegment(curve, 0.0, 0.25)
                c2 = CurveSegment(curve, 0.75, 0.5)
                d1 = CurveSegment(curve, 1.0, 0.75)
                d2 = CurveSegment(curve, 0.25, 0.5)

                res = fp.UCount if fp.UCount >= 2 else 8
                surf = DMSurface.from_boundaries(c1, c2, d1, d2, res=res)
                return surf.to_shape()

            # Otherwise use the static grid
            grid = []
            u_count = fp.UCount
            v_count = fp.VCount
            flat_list = fp.ControlGrid
            
            if u_count > 0 and v_count > 0 and len(flat_list) == u_count * v_count:
                for v in range(v_count):
                    row = flat_list[v*u_count : (v+1)*u_count]
                    grid.append(row)
            
            surf = DMSurface(grid)
            return surf.to_shape()
        elif st == "frep":
            if hasattr(self, "FRepField") and self.FRepField is not None:
                from core.frep_mesher import get_active_mesher
                mesher = get_active_mesher()
                # Use resolution hint if set (by primitive_tool on finalize), else default
                res = getattr(self, "_final_resolution", 20)
                return mesher.mesh(self.FRepField, resolution=res)
            return Part.Shape()
            
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from . import dm_logger
            st = fp.ShapeType if hasattr(fp, "ShapeType") else "nurbs"

            if st == "frep":
                if hasattr(self, "FRepField") and self.FRepField is not None:
                    from core.frep_mesher import get_active_mesher
                    mesher = get_active_mesher()
                    res = getattr(self, "_final_resolution", 20)
                    result = mesher.mesh(self.FRepField, resolution=res)
                    if result is not None:
                        self._frep_verts, self._frep_idx = result
                    else:
                        self._frep_verts = self._frep_idx = None
                else:
                    dm_logger.debug(f"DMObjectProxy: Built NULL shape for {fp.Label} (expected if object is empty)")
                    self._frep_verts = self._frep_idx = None

                # Setting fp.Shape triggers ViewProvider.updateData(fp, "Shape")
                # which is called on the correct ViewProvider instance.
                fp.Shape = Part.Shape()
                return

            new_shape = self.build_shape(fp)
            if new_shape.isNull():
                dm_logger.debug(f"DMObjectProxy: Built NULL shape for {fp.Label} (expected if object is empty)")
            fp.Shape = new_shape

        except Exception:
            from . import dm_logger
            dm_logger.exception(f"DMObject.execute error for {fp.Label}")



    def __setstate__(self, state):
        from . import dm_logger
        # dm_logger.debug(f"DMObjectProxy.__setstate__: {state}")
        pass

class DMViewProvider:
    """ViewProvider for DM objects. Shows an orange part icon."""
    def __init__(self, vobj):
        # setup_view MUST run before vobj.Proxy = self.
        # In FreeCAD, assigning Proxy triggers attach() synchronously,
        # which sets _frep_coords etc. setup_view() must not overwrite them.
        self.setup_view(vobj)
        vobj.Proxy = self

    def setup_view(self, vobj):
        vobj.PointColor = (1.0, 0.5, 0.0)
        vobj.LineColor = (1.0, 0.5, 0.0)
        vobj.LineWidth = get_line_width()
        vobj.PointSize = get_point_size()
        if hasattr(vobj.Object, "ShapeType") and vobj.Object.ShapeType == "surface":
            vobj.DisplayMode = "Shaded"
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
        elif hasattr(vobj.Object, "ShapeType") and vobj.Object.ShapeType == "frep":
            # Frep objects render via Coin3D; suppress the Part shape renderer
            try:
                # Add our custom display modes directly to the ViewProvider
                # We need to manually handle property changes for these because
                # FreeCAD's built-in "No Drawing" will hide the Coin3D node entirely.
                pass
            except Exception:
                pass
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
        else:
            vobj.DisplayMode = "Flat Lines"

        # Initialize Coin3D overlay fields
        self._ctrl_cage_sep = None
        self._ctrl_coords = None
        self._ctrl_lines = None
        self._ctrl_handle_points = None
        self._style = None

        # Persistent knots
        self._knot_points = None

        self._debug_sep = None
        self._debug_coords = None

        # FRep Coin3D mesh nodes (set in _setup_frep_mesh_nodes)
        self._frep_sep = None
        self._frep_coords = None
        self._frep_faces = None

    def attach(self, vobj):
        from . import dm_logger
        self.Object = vobj.Object
        dm_logger.debug(f"DMViewProvider.attach: obj={self.Object.Label}, coin_avail={coin is not None}")

        if coin:
            # Setup curve overlay if it's a curve
            if hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "curve":
                self._setup_coin_overlay(vobj)
            # Setup direct mesh rendering for F-Rep objects
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                self._setup_frep_mesh_nodes(vobj)

    def _setup_frep_mesh_nodes(self, vobj):
        """Create Coin3D nodes for direct mesh rendering of F-Rep objects."""
        if not coin:
            return
        try:
            sep = coin.SoSeparator()

            # ── Mesh nodes ────────────────────────────────────────────────────
            mesh_sep = coin.SoSeparator()

            mat = coin.SoMaterial()
            mat.diffuseColor.setValue(1.0, 0.5, 0.0)
            mat.specularColor.setValue(0.3, 0.3, 0.3)
            mat.shininess.setValue(0.3)
            mesh_sep.addChild(mat)

            hints = coin.SoShapeHints()
            try:
                hints.vertexOrdering = coin.SoShapeHints.COUNTER_CLOCKWISE
            except AttributeError:
                hints.vertexOrdering = 2
            try:
                hints.shapeType = coin.SoShapeHints.SOLID
            except AttributeError:
                hints.shapeType = 1
            hints.creaseAngle = 0.5
            mesh_sep.addChild(hints)

            self._frep_draw_style = coin.SoDrawStyle()
            # Default to shaded solid triangles
            try:
                self._frep_draw_style.style = coin.SoDrawStyle.FILLED
            except AttributeError:
                self._frep_draw_style.style = 1 # FILLED = 1
            mesh_sep.addChild(self._frep_draw_style)

            self._frep_coords = coin.SoCoordinate3()
            mesh_sep.addChild(self._frep_coords)

            self._frep_faces = coin.SoIndexedFaceSet()
            mesh_sep.addChild(self._frep_faces)
            sep.addChild(mesh_sep)

            # ── Corner point + handle nodes ────────────────────────────────────
            corner_sep = coin.SoSeparator()

            # Corner points (white spheres / points)
            c_mat = coin.SoMaterial()
            c_mat.diffuseColor.setValue(1.0, 1.0, 1.0)
            corner_sep.addChild(c_mat)

            c_style = coin.SoDrawStyle()
            c_style.pointSize.setValue(8)
            corner_sep.addChild(c_style)

            self._frep_corner_coords = coin.SoCoordinate3()
            corner_sep.addChild(self._frep_corner_coords)
            self._frep_corner_pts = coin.SoPointSet()
            corner_sep.addChild(self._frep_corner_pts)

            # Bevel handle lines (orange dashed)
            h_mat = coin.SoMaterial()
            h_mat.diffuseColor.setValue(1.0, 0.6, 0.2)
            corner_sep.addChild(h_mat)

            h_style = coin.SoDrawStyle()
            h_style.lineWidth = 1
            h_style.linePattern = 0x0F0F
            corner_sep.addChild(h_style)

            self._frep_handle_coords = coin.SoCoordinate3()
            corner_sep.addChild(self._frep_handle_coords)
            self._frep_handle_lines = coin.SoLineSet()
            corner_sep.addChild(self._frep_handle_lines)

            sep.addChild(corner_sep)

            self._frep_sep = sep
            vobj.RootNode.addChild(sep)
        except Exception as e:
            from . import dm_logger
            dm_logger.debug(f"DMViewProvider._setup_frep_mesh_nodes failed: {e}")
            self._frep_sep = None
            self._frep_draw_style = None
            self._frep_coords = None
            self._frep_faces = None
            self._frep_corner_coords = None
            self._frep_corner_pts = None
            self._frep_handle_coords = None
            self._frep_handle_lines = None


    def _update_frep_mesh(self, verts, flat_idx):
        """Push numpy mesh arrays directly into Coin3D nodes."""
        from . import dm_logger
        if not coin or not hasattr(self, "_frep_coords") or self._frep_coords is None:
            dm_logger.debug("[FREP] _update_frep_mesh: nodes not ready")
            return
        try:
            if verts is None or flat_idx is None or len(verts) == 0:
                self._frep_coords.point.setNum(0)
                self._frep_faces.coordIndex.setNum(0)
                return
            self._frep_coords.point.setValues(verts)
            self._frep_faces.coordIndex.setValues(flat_idx)
        except Exception as e:
            dm_logger.info(f"[FREP] _update_frep_mesh failed ({type(e).__name__}): {e}")

    def _update_frep_corners(self, field):
        """Show corner points + inward bevel handles for the FRep field."""
        if not coin or not hasattr(self, "_frep_corner_coords") or self._frep_corner_coords is None:
            return
        try:
            import FreeCAD as _FC
            # Use actual local-space corners transformed to world space via the field's placement.
            # For MCBoxField this gives true oriented box corners, not AABB corners.
            placement = getattr(field, "placement", None)
            center = getattr(field, "center", None)
            half_size = getattr(field, "half_size", None)

            if center is not None and half_size is not None:
                # Build 8 local corners
                c = center
                h = half_size
                local_corners = [
                    _FC.Vector(c.x - h.x, c.y - h.y, c.z - h.z),
                    _FC.Vector(c.x + h.x, c.y - h.y, c.z - h.z),
                    _FC.Vector(c.x + h.x, c.y + h.y, c.z - h.z),
                    _FC.Vector(c.x - h.x, c.y + h.y, c.z - h.z),
                    _FC.Vector(c.x - h.x, c.y - h.y, c.z + h.z),
                    _FC.Vector(c.x + h.x, c.y - h.y, c.z + h.z),
                    _FC.Vector(c.x + h.x, c.y + h.y, c.z + h.z),
                    _FC.Vector(c.x - h.x, c.y + h.y, c.z + h.z),
                ]
                if placement is not None:
                    world_corners = [placement.multVec(lc) for lc in local_corners]
                else:
                    world_corners = local_corners
                corners = [(v.x, v.y, v.z) for v in world_corners]
                wc = sum((v.x for v in world_corners), 0.0) / 8
                hc = sum((v.y for v in world_corners), 0.0) / 8
                dc = sum((v.z for v in world_corners), 0.0) / 8
                bevel_dist = min(h.x, h.y, h.z) * 0.08
            else:
                # Generic fallback: use AABB
                min_b, max_b = field.bounding_box()
                corners = [
                    (min_b.x, min_b.y, min_b.z), (max_b.x, min_b.y, min_b.z),
                    (max_b.x, max_b.y, min_b.z), (min_b.x, max_b.y, min_b.z),
                    (min_b.x, min_b.y, max_b.z), (max_b.x, min_b.y, max_b.z),
                    (max_b.x, max_b.y, max_b.z), (min_b.x, max_b.y, max_b.z),
                ]
                wc = (min_b.x + max_b.x) / 2
                hc = (min_b.y + max_b.y) / 2
                dc = (min_b.z + max_b.z) / 2
                bevel_dist = min(max_b.x - min_b.x, max_b.y - min_b.y, max_b.z - min_b.z) * 0.08

            corner_pts = list(corners)
            handle_pts = [
                (x + (wc - x) / max(abs(wc-x), 1e-6) * bevel_dist,
                 y + (hc - y) / max(abs(hc-y), 1e-6) * bevel_dist,
                 z + (dc - z) / max(abs(dc-z), 1e-6) * bevel_dist)
                for (x,y,z) in corners
            ]

            all_pts = corner_pts + handle_pts
            self._frep_corner_coords.point.setValues(corner_pts)
            self._frep_corner_pts.numPoints.setValue(len(corner_pts))
            self._frep_handle_coords.point.setValues(all_pts)
            self._frep_handle_lines.numVertices.setValues([2] * len(corners))
        except Exception as e:
            from . import dm_logger
            dm_logger.debug(f"[FREP] _update_frep_corners failed: {e}")




    def _setup_coin_overlay(self, vobj):
        if not coin: return
        from . import dm_logger
        dm_logger.debug(f"DMViewProvider._setup_coin_overlay: {vobj.Object.Label}")
        
        self._ctrl_cage_sep = coin.SoSeparator()
        
        # Style for dashed handle lines
        self._style = coin.SoDrawStyle()
        self._style.linePattern = 0x0F0F # Dashed
        self._style.lineWidth = 1
        self._ctrl_cage_sep.addChild(self._style)
        
        # Coordinates shared by lines and points
        self._ctrl_coords = coin.SoCoordinate3()
        self._ctrl_cage_sep.addChild(self._ctrl_coords)
        
        # Handle lines
        self._ctrl_lines = coin.SoLineSet()
        self._ctrl_cage_sep.addChild(self._ctrl_lines)
        
        # Control points (markers)
        pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(pts_sep)

        pts_mat = coin.SoMaterial()
        pts_mat.diffuseColor = coin.SbColor(1.0, 0.5, 0.0) # Orange
        pts_sep.addChild(pts_mat)
        
        pt_style = coin.SoDrawStyle()
        pt_style.pointSize.setValue(8) 
        pts_sep.addChild(pt_style)
        
        self._ctrl_points = coin.SoPointSet()
        pts_sep.addChild(self._ctrl_points)

        # Handle markers (endpoints of handle lines)
        h_pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(h_pts_sep)
        
        h_pts_mat = coin.SoMaterial()
        h_pts_mat.diffuseColor = coin.SbColor(0.2, 0.7, 1.0) # Light Blue
        h_pts_sep.addChild(h_pts_mat)
        
        h_pt_style = coin.SoDrawStyle()
        h_pt_style.pointSize.setValue(5)
        h_pts_sep.addChild(h_pt_style)
        
        self._ctrl_handle_points = coin.SoPointSet()
        h_pts_sep.addChild(self._ctrl_handle_points)
        
        # Knot markers (always visible)
        k_pts_sep = coin.SoSeparator()
        self._ctrl_cage_sep.addChild(k_pts_sep)
        
        k_pts_mat = coin.SoMaterial()
        k_pts_mat.diffuseColor = coin.SbColor(1.0, 1.0, 1.0) # White/Default
        k_pts_sep.addChild(k_pts_mat)
        
        k_pt_style = coin.SoDrawStyle()
        k_pt_style.pointSize.setValue(3) # Small
        k_pts_sep.addChild(k_pt_style)
        
        self._knot_points = coin.SoPointSet()
        k_pts_sep.addChild(self._knot_points)
        
        vobj.addDisplayMode(self._ctrl_cage_sep, "ControlCage")
        # Add to the root node if we want it visible in standard modes
        vobj.RootNode.addChild(self._ctrl_cage_sep)
        
        self._rebuild_control_cage(self.Object)

    def _rebuild_control_cage(self, fp):
        """Rebuild the interactive Coin3D overlay for control points and handles."""
        if not coin: return
        
        # Lazy initialization if attach() missed it
        if not self._ctrl_coords:
            vobj = fp.ViewObject
            if vobj and hasattr(fp, "ShapeType") and fp.ShapeType == "curve":
                self._setup_coin_overlay(vobj)
        
        if not self._ctrl_coords:
            return

        if not hasattr(fp, "Points") or not fp.Points:
            self._ctrl_coords.point.setNum(0)
            return

        pts = list(fp.Points)
        h_in = list(fp.HandleIn) if hasattr(fp, "HandleIn") else []
        h_out = list(fp.HandleOut) if hasattr(fp, "HandleOut") else []
        edit_mode = getattr(fp, "EditMode", False)
        
        line_coords = []
        marker_coords = []
        handle_marker_coords = []
        num_vertices = []
        knot_coords = []
        
        for i, p in enumerate(pts):
            # Always add knots
            knot_coords.append(coin.SbVec3f(p.x, p.y, p.z))
            
            if edit_mode:
                marker_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                
                # Handle In line: point p to handle h_in[i]
                if i < len(h_in) and h_in[i] is not None and (h_in[i] - p).Length > 1e-4:
                    line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                    line_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                    handle_marker_coords.append(coin.SbVec3f(h_in[i].x, h_in[i].y, h_in[i].z))
                    num_vertices.append(2)
                
                # Handle Out line: point p to handle h_out[i]
                if i < len(h_out) and h_out[i] is not None and (h_out[i] - p).Length > 1e-4:
                    line_coords.append(coin.SbVec3f(p.x, p.y, p.z))
                    line_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                    handle_marker_coords.append(coin.SbVec3f(h_out[i].x, h_out[i].y, h_out[i].z))
                    num_vertices.append(2)

        # Update Coin3D coordinates
        # Coords order: knots | control markers | handle markers | line vertices
        final_coords = knot_coords + marker_coords + handle_marker_coords + line_coords
        self._ctrl_coords.point.setNum(len(final_coords))
        self._ctrl_coords.point.setValues(0, final_coords)
        
        # Knot point set (ALWAYS VISIBLE)
        self._knot_points.numPoints.setValue(len(knot_coords))
        self._knot_points.startIndex.setValue(0)
        
        # Main control points (EDIT ONLY)
        self._ctrl_points.numPoints.setValue(len(marker_coords))
        self._ctrl_points.startIndex.setValue(len(knot_coords))
        
        # Handle markers (EDIT ONLY)
        self._ctrl_handle_points.numPoints.setValue(len(handle_marker_coords))
        self._ctrl_handle_points.startIndex.setValue(len(knot_coords) + len(marker_coords))
        
        # Handle lines (EDIT ONLY)
        self._ctrl_lines.numVertices.setNum(len(num_vertices))
        self._ctrl_lines.numVertices.setValues(0, num_vertices)
        self._ctrl_lines.startIndex.setValue(len(knot_coords) + len(marker_coords) + len(handle_marker_coords))

    def updateData(self, fp, prop):
        from . import dm_logger
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            # Called after execute() sets fp.Shape — safe to update Coin3D here
            proxy = getattr(fp, "Proxy", None)
            if proxy:
                self._update_frep_mesh(
                    getattr(proxy, "_frep_verts", None),
                    getattr(proxy, "_frep_idx", None)
                )
                field = getattr(proxy, "FRepField", None)
                if field:
                    self._update_frep_corners(field)
        elif prop == "DisplayMode" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            # Toggle between shaded and wireframe rendering
            if hasattr(self, "_frep_draw_style") and self._frep_draw_style:
                vobj = fp.ViewObject
                if vobj.DisplayMode == "Wireframe":
                    try:
                        self._frep_draw_style.style = coin.SoDrawStyle.LINES
                    except AttributeError:
                        self._frep_draw_style.style = 2 # LINES = 2
                else:
                    try:
                        self._frep_draw_style.style = coin.SoDrawStyle.FILLED
                    except AttributeError:
                        self._frep_draw_style.style = 1 # FILLED = 1
        elif not prop or prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
            self._rebuild_control_cage(fp)

        

    def getIcon(self):
        # Orange stairstep icon (Part)
        return """
            /* XPM */
            static char * orange_part_xpm[] = {
            "16 16 3 1",
            " 	c None",
            ".	c #FFA500",
            "+	c #000000",
            "                ",
            "  ++++++        ",
            "  +....++       ",
            "  +.....+       ",
            "  +..+..+       ",
            "  +..+..+       ",
            "  +..++++++     ",
            "  +..+....++    ",
            "  +..+.....+    ",
            "  +..+..+..+    ",
            "  ++++..++++++  ",
            "     +..+....++ ",
            "     +..+.....+ ",
            "     +..+..+..+ ",
            "     ++++++++++ ",
            "                "};
            """

    def claimChildren(self):
        return self.Object.OutList

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_dm_object(name, shape_type, params=None, placement=None):
    """
    Create a DM object (Part::FeaturePython).
    """
    from . import dm_logger
    
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    try:
        # dm_logger.debug(f"create_dm_object: name={name}, type={shape_type}, has_placement={placement is not None}")
        obj = doc.addObject("Part::FeaturePython", name)
        DMObjectProxy(obj, shape_type, params, placement=placement)

        if FreeCAD.GuiUp:
            DMViewProvider(obj.ViewObject)
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                try:
                    obj.ViewObject.Visibility = True
                except Exception as e:
                    dm_logger.debug(f"create_dm_object: Failed to set visibility for {name}: {e}")
        
        dm_logger.debug(f"create_dm_object: Created {name} ({shape_type}), triggering recompute...")
        obj.touch()
        doc.recompute()
        
        import FreeCADGui
        if FreeCAD.GuiUp:
            try:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(obj)
                
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view:
                    active_view.viewSelection()
                
                FreeCADGui.updateGui()
            except Exception as e:
                dm_logger.debug(f"create_dm_object: Selection/View setup failed for {name}: {e}")
        
        dm_logger.debug(f"create_dm_object: {name} created successfully")
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
            obj.ViewObject.LineWidth = get_line_width()
            obj.ViewObject.PointSize = get_point_size()
            # Disable wireframe for F-Rep mesh objects - reduces render overhead
            if shape_type == "frep":
                try:
                    obj.ViewObject.DisplayMode = "Shaded"
                except Exception:
                    pass
        
        return obj
        
    except Exception:
        from . import dm_logger
        dm_logger.exception(f"create_dm_object FAILED for {name}")
        return None

def refresh_all_dm_objects():
    """Update LineWidth and PointSize of all DM objects in the active document."""
    import FreeCAD
    doc = FreeCAD.activeDocument()
    if not doc:
        return
    
    lw = get_line_width()
    ps = get_point_size()
    
    for obj in doc.Objects:
        if hasattr(obj, "ShapeType") and obj.ViewObject:
            obj.ViewObject.LineWidth = lw
            obj.ViewObject.PointSize = ps
    
    import FreeCADGui
    if FreeCAD.GuiUp:
        FreeCADGui.updateGui()

