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

def get_meshing_type():
    """
    Return the meshing approach:
    0: Marching Cubes (Standard SDF)
    1: Adaptive Marching Cubes
    2: NURBS based F-Rep approach
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("MeshingType", 0)

def set_meshing_type(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("MeshingType", int(val))

def get_meshing_cell_size():
    """Return the global meshing cell size in mm (defaults to 10.0)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("MeshingCellSize", 10.0)

def set_meshing_cell_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("MeshingCellSize", float(val))

def get_frep_storage_type(): # Deprecated alias
    return get_meshing_type()

def set_frep_storage_type(val): # Deprecated alias
    set_meshing_type(val)

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

        if shape_type == "frep":
            if not hasattr(obj, "MeshingCellSize"):
                obj.addProperty("App::PropertyFloat", "MeshingCellSize", "FRep", "Meshing cell size in mm (smaller = more detail)")
                obj.MeshingCellSize = get_meshing_cell_size()
            if not hasattr(obj, "ShowWireframe"):
                obj.addProperty("App::PropertyBool", "ShowWireframe", "FRep", "Show triangle wireframe")
                obj.ShowWireframe = get_show_wireframe()
            if not hasattr(obj, "MeshingType"):
                obj.addProperty("App::PropertyEnumeration", "MeshingType", "FRep", "Meshing algorithm")
                obj.MeshingType = [
                    "Marching Cubes (Standard SDF)",
                    "Adaptive Marching Cubes",
                    "NURBS based F-Rep approach"
                ]
                obj.MeshingType = get_meshing_type()

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""

        
        st = fp.ShapeType
        if st == "curve":
            from .dm_curve import DMCurve
            from .dm_point import DMPoint
            
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
            from .dm_surface import DMSurface
            from .dm_curve import DMCurve
            from .dm_point import DMPoint
            
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
                from core.dm_mesher import get_active_mesher
                m_type = getattr(fp, "MeshingType", None)
                mesher = get_active_mesher(type_override=m_type)
                # Favor object property over internal resolution override
                res = float(getattr(fp, "MeshingCellSize", getattr(self, "_final_resolution", get_meshing_cell_size())))
                return mesher.mesh(self.FRepField, cell_size=res)
            return Part.Shape()
            
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from . import dm_logger
            st = fp.ShapeType if hasattr(fp, "ShapeType") else "nurbs"

            if st == "frep":
                if hasattr(self, "FRepField") and self.FRepField is not None:
                    from core.dm_mesher import get_active_mesher
                    m_type = getattr(fp, "MeshingType", None)
                    mesher = get_active_mesher(type_override=m_type)
                    res = float(getattr(fp, "MeshingCellSize", getattr(self, "_final_resolution", get_meshing_cell_size())))
                    result = mesher.mesh(self.FRepField, cell_size=res)
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
        from . import dm_logger
        shape_type = getattr(vobj.Object, "ShapeType", None)
        if shape_type == "frep":
            try:
                vobj.DisplayMode = "Shaded"
            except ValueError as e:
                dm_logger.debug(f"DMViewProvider.setup_view: Failed to set DisplayMode to 'Shaded': {e}")
        else:
            try:
                vobj.DisplayMode = "Flat Lines"
            except ValueError as e:
                dm_logger.debug(f"DMViewProvider.setup_view: Failed to set DisplayMode to 'Flat Lines': {e}")

        # DMRenderer handles all Coin3D overlays (meshes, handles, etc)
        self.renderer = None

    def attach(self, vobj):
        from . import dm_logger
        self.Object = vobj.Object
        dm_logger.debug(f"DMViewProvider.attach: obj={self.Object.Label}, coin_avail={coin is not None}")

        if coin:
            from core.dm_renderer import DMRenderer
            self.renderer = DMRenderer(vobj)

            # Setup curve overlay if it's a curve
            if hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "curve":
                self.renderer.setup_coin_overlay()
                self.renderer.rebuild_control_cage(self.Object)
            # Setup direct mesh rendering for F-Rep objects
            elif hasattr(self.Object, "ShapeType") and self.Object.ShapeType == "frep":
                self.renderer.setup_frep_mesh_nodes()

    def on_prefs_changed(self):
        """Update Coin3D styles and visibility based on global preferences."""
        if self.renderer:
            self.renderer.on_prefs_changed(self.Object)
            
        # Generic FreeCAD ViewObject properties
        try:
            vobj = self.Object.ViewObject
            lw = get_line_width() # Get current line width
            vobj.LineWidth = lw
            vobj.PointSize = get_point_size()
        except:
            pass

    def updateData(self, fp, prop):
        from . import dm_logger
        
        if prop in ["ShowWireframe", "MeshingCellSize"]:
            self.on_prefs_changed()
            
        if prop == "Shape" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            # Called after execute() sets fp.Shape — safe to update Coin3D here
            proxy = getattr(fp, "Proxy", None)
            if proxy and self.renderer:
                self.renderer.update_frep_mesh(
                    getattr(proxy, "_frep_verts", None),
                    getattr(proxy, "_frep_idx", None)
                )
                field = getattr(proxy, "FRepField", None)
                if field:
                    self.renderer.update_frep_corners(field)
        elif prop == "DisplayMode" and hasattr(fp, "ShapeType") and fp.ShapeType == "frep":
            # Toggle between shaded and wireframe rendering
            if self.renderer:
                vobj = fp.ViewObject
                self.renderer.set_frep_display_mode(vobj.DisplayMode)
        elif not prop or prop in ["Points", "HandleIn", "HandleOut", "Closed", "EditMode"]:
            if self.renderer:
                self.renderer.rebuild_control_cage(fp)
        
        if not prop:
            self.on_prefs_changed()
            # Also ensure visibility is correct
            if self.renderer:
                vobj = fp.ViewObject
                self.renderer.update_visibility(vobj.Visibility)

    def onChanged(self, vobj, prop):
        """Called when a property of the ViewObject changes (e.g. Visibility)."""
        if prop == "Visibility" and self.renderer:
            self.renderer.update_visibility(vobj.Visibility)

        

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
    res = get_meshing_cell_size()
    show_wire = get_show_wireframe()
    m_type = get_meshing_type()
    
    for obj in doc.Objects:
        if hasattr(obj, "ShapeType") and obj.ViewObject:
            # Native FreeCAD properties
            obj.ViewObject.LineWidth = lw
            # DM Proxy properties
            if hasattr(obj, "MeshingCellSize"):
                obj.MeshingCellSize = float(res)
            if hasattr(obj, "ShowWireframe"):
                obj.ShowWireframe = bool(show_wire)
            if hasattr(obj, "MeshingType"):
                obj.MeshingType = int(m_type)
                
            proxy = getattr(obj.ViewObject, "Proxy", None)
            if proxy and hasattr(proxy, "on_prefs_changed"):
                proxy.on_prefs_changed()
            obj.ViewObject.PointSize = ps
    
    import FreeCADGui
    if FreeCAD.GuiUp:
        FreeCADGui.updateGui()

