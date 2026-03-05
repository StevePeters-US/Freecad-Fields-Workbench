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
            
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from . import dm_logger
            
            # Syncing placement here causes infinite recompute loops.
            # Handle this in the SourceCurve property's onChanged if desired.
            pass

            # Ensure the object has a shape
            new_shape = self.build_shape(fp)
            
            if new_shape.isNull():
                 dm_logger.debug(f"DMObjectProxy: Built NULL shape for {fp.Label} (expected if object is empty)")
            else:
                # dm_logger.debug(f"DMObjectProxy: Shape built. Faces={len(new_shape.Faces)}, BoundBox={new_shape.BoundBox}")
                pass
                 
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
        vobj.Proxy = self
        self.setup_view(vobj)
        
    def setup_view(self, vobj):
        vobj.PointColor = (1.0, 0.5, 0.0)
        vobj.LineColor = (1.0, 0.5, 0.0)
        vobj.LineWidth = get_line_width()
        vobj.PointSize = get_point_size()
        if hasattr(vobj.Object, "ShapeType") and vobj.Object.ShapeType == "surface":
            vobj.DisplayMode = "Shaded"
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

    def attach(self, vobj):
        from . import dm_logger
        self.Object = vobj.Object
        dm_logger.debug(f"DMViewProvider.attach: obj={self.Object.Label}, coin_avail={coin is not None}")
        
        if coin:
            # Setup curve overlay if it's a curve
            if hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "curve":
                 self._setup_coin_overlay(vobj)

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
        # dm_logger.debug(f"DMViewProvider.updateData: obj={fp.Label}, prop={prop}")
        if not prop or prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
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

