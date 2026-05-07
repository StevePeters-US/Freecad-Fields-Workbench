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
import FreeCADGui
import Part
try:
    from pivy import coin
except ImportError:
    coin = None

from . import dm_logger

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
    try: # ParamGet might fail in some contexts
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnablePerfProfiler", bool(val))
    except Exception as e:
        dm_logger.debug(f"set_perf_profiler_enabled failed: {e}")

def get_render_debug_mode():
    """Return whether render debug mode is enabled."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("RenderDebugMode", False)

def set_render_debug_mode(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("RenderDebugMode", bool(val))


def get_near_clip_distance():
    """Return the near clip distance override in mm (0 = auto)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("NearClipDistance", 0.0)

def set_near_clip_distance(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("NearClipDistance", float(val))

def get_ray_march_cell_size():
    """Return the SDF baking cell size for GPU ray march renderer in mm (default 2.0)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("RayMarchCellSize", 2.0)

def set_ray_march_cell_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("RayMarchCellSize", float(val))

def get_max_sdf_render_size():
    """Return the maximum SDF bounding box dimension in mm for rendering (default 2000)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("MaxSdfRenderSize", 2000.0)

def set_max_sdf_render_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("MaxSdfRenderSize", float(val))

def apply_near_clip_override():
    """Apply near clip distance override to the active camera, if set."""
    dist = get_near_clip_distance()
    if dist <= 0.0:
        return  # auto mode
    try:
        from . import dm_logger
        view = FreeCADGui.ActiveDocument.ActiveView
        if view:
            cam = view.getCameraNode()
            if cam:
                cam.nearDistance.setValue(dist)
    except Exception:
        pass

def get_interactive_throttle_interval():
    """Return the interactive remeshing/update throttle interval in seconds."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("InteractiveThrottleInterval", 0.025)

def set_interactive_throttle_interval(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("InteractiveThrottleInterval", float(val))




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
                                "Rendering group: Group 1 (orange/additive) or Group 2 (blue/subtractive)")
                obj.Group = ["Group 1", "Group 2"]
                obj.Group = "Group 2" if was_sub else "Group 1"

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

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from . import dm_logger

            st = fp.ShapeType if hasattr(fp, "ShapeType") else "nurbs"

            if st == "sdf":
                # Parametric boolean recompute
                if hasattr(fp, "BooleanInputs") and fp.BooleanInputs:
                    try:
                        from commands.cmd_boolean import _recompose_boolean
                        new_field = _recompose_boolean(fp)
                        if new_field is not None:
                            self.SdfField = new_field
                            # Push updated field to renderer
                            from core.dm_renderer import SdfRendererStrategy
                            vp = getattr(fp, "ViewObject", None)
                            vp_proxy = getattr(vp, "Proxy", None) if vp else None
                            strategy = getattr(vp_proxy, "_strategy", None) if vp_proxy else None
                            if strategy and hasattr(strategy, "label") and strategy.label:
                                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                                DMSceneRayMarchRenderer.get_instance().update_field(
                                    strategy.label, new_field
                                )
                    except Exception as e:
                        from . import dm_logger
                        dm_logger.error(f"Boolean recompute failed for {fp.Label}: {e}")
                
                # SDF objects bypass native B-Rep meshing.
                fp.Shape = Part.Shape()
                return

            new_shape = self.build_shape(fp)
            fp.Shape = new_shape

        except Exception:
            from . import dm_logger
            dm_logger.exception(f"DMObject.execute error for {fp.Label}")



    def _recompute_primitive_field(self, fp):
        """Reconstruct a primitive SDF field from the 'Points' vector list."""
        pts = getattr(fp, "Points", [])
        if not pts:
            return None
            
        try:
            from core.sdf.sdf.box import SdfBoxField
            from core.sdf.sdf.sphere import SdfSphereField
            from core.sdf.sdf.cylinder import SdfCylinderField
            
            # Use original placement as field coordinate system
            placement = fp.Placement
            
            import math
            if len(pts) == 8:
                # Box reconstruction: 8 corners (already mapped locally)
                local_pts = pts
                min_v = FreeCAD.Vector(min(p.x for p in local_pts), min(p.y for p in local_pts), min(p.z for p in local_pts))
                max_v = FreeCAD.Vector(max(p.x for p in local_pts), max(p.y for p in local_pts), max(p.z for p in local_pts))
                center = (min_v + max_v) / 2.0
                size = (max_v - min_v)
                return SdfBoxField(center, size, placement=placement)
                
            elif len(pts) == 2:
                # Sphere: center and radius point
                c_local = pts[0]
                r_local = pts[1]
                radius = (r_local - c_local).Length
                return SdfSphereField(c_local, radius, placement=placement)
                
            elif len(pts) == 3:
                # Cylinder: base, radius point, height point
                base_loc = pts[0]
                r_loc = pts[1]
                h_loc = pts[2]
                radius = math.sqrt((r_loc.x - base_loc.x)**2 + (r_loc.y - base_loc.y)**2)
                height = (h_loc - base_loc).z
                return SdfCylinderField(base_loc, FreeCAD.Vector(0,0,1), radius, height, placement=placement)
                
        except Exception as e:
            from . import dm_logger
            dm_logger.debug(f"Primitive field reconstruction failed for {fp.Label}: {e}")
        return None


    def __setstate__(self, state):
        from . import dm_logger
        # dm_logger.debug(f"DMObjectProxy.__setstate__: {state}")
        pass

class DMViewProvider:
    """ViewProvider for DM objects. Shows an orange part icon."""
    def __init__(self, vobj):
        # setup_view MUST run before vobj.Proxy = self.
        # Assigning vobj.Proxy triggers attach() synchronously in FreeCAD,
        # which creates the DMRenderer. If setup_view ran after, it would
        # overwrite renderer=None and destroy the renderer reference.
        self.setup_view(vobj)
        vobj.Proxy = self

    def setup_view(self, vobj):
        vobj.PointColor = (1.0, 0.5, 0.0)
        vobj.LineColor = (1.0, 0.5, 0.0)
        vobj.LineWidth = get_line_width()
        vobj.PointSize = get_point_size()
        
        shape_type = getattr(vobj.Object, "ShapeType", None)
        if shape_type == "point":
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
        elif shape_type == "surface":
            vobj.DisplayMode = "Shaded"
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
        elif shape_type == "sdf":
            vobj.PointSize = 0.0
            vobj.LineWidth = 0.0
            try:
                vobj.DisplayMode = "Shaded"
            except Exception:
                pass
        else:
            try:
                vobj.DisplayMode = "Flat Lines"
            except Exception:
                pass

        # DMRenderer handles all Coin3D overlays (meshes, handles, etc)
        self.renderer = None
        self._strategy = None

    def attach(self, vobj):
        from . import dm_logger
        self.Object = vobj.Object
        if coin:
            from core.dm_renderer import DMRenderer, SdfRendererStrategy, NURBSRendererStrategy
            self.renderer = DMRenderer(vobj)

            st = getattr(self.Object, "ShapeType", None)
            self._strategy = SdfRendererStrategy() if st == "sdf" else NURBSRendererStrategy()
            self._strategy.setup(self.renderer, vobj)
            
            # Keep label for backward compatibility with other methods
            if hasattr(self._strategy, "label"):
                self._scene_rm_label = self._strategy.label

    def on_prefs_changed(self):
        """Update Coin3D styles and visibility based on global preferences."""
        if self.renderer:
            self.renderer.on_prefs_changed(self.Object)
            
        if hasattr(self, "_scene_rm_label"):
            from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
            DMSceneRayMarchRenderer.get_instance().on_prefs_changed()

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
        
        if prop in ["ShowWireframe", "Group"]:
            self.on_prefs_changed()
        
        if prop == "Group" and hasattr(fp, "Group"):
            try:
                if fp.Group == "Group 2":
                    fp.ViewObject.ShapeColor = (0.3, 0.5, 1.0)
                else:
                    fp.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
            except Exception:
                pass
            
        if prop == "DisplayMode":
            self._strategy.set_display_mode(self.renderer, fp.ViewObject.DisplayMode)
            
        self._strategy.update(self.renderer, fp, prop)
        
        if not prop:
            self.on_prefs_changed()
            # Also ensure visibility is correct
            vobj = fp.ViewObject
            if self.renderer:
                self.renderer.update_visibility(vobj.Visibility)
            
            if hasattr(self, "_scene_rm_label"):
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.set_field_visible(self._scene_rm_label, vobj.Visibility)
                if FreeCADGui.activeView():
                    FreeCADGui.activeView().redraw()

    def onChanged(self, vobj, prop):
        """Called when a property of the ViewObject changes (e.g. Visibility)."""
        if prop == "Visibility":
            if self.renderer:
                self.renderer.update_visibility(vobj.Visibility)
            if hasattr(self, "_scene_rm_label"):
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                sr.set_field_visible(self._scene_rm_label, vobj.Visibility)

        # Re-apply near clip override (FreeCAD navigation resets camera params)
        apply_near_clip_override()

    def onDelete(self, vobj, subelements):
        """Called when the object is about to be deleted."""
        try:
            from . import dm_logger
            # Check for the label we registered
            label = getattr(self, "_scene_rm_label", None)
            if not label:
                # Fallback: compute it just in case
                if hasattr(self.Object, "Document") and self.Object.Document:
                    label = f"{self.Object.Document.Name}.{self.Object.Name}"
            
            if label:
                from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
                sr = DMSceneRayMarchRenderer.get_instance()
                # dm_logger.debug(f"DMObject: onDelete unregistering field '{label}'")
                sr.unregister_field(label)
        except Exception as e:
            from . import dm_logger
            dm_logger.debug(f"DMObject.onDelete error: {e}")
        return True

        

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
        

        obj.touch()
        doc.recompute()
        
        if FreeCAD.GuiUp:
            try:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(obj)
                
                active_document = FreeCADGui.ActiveDocument
                active_view = getattr(active_document, "ActiveView", None) if active_document else None
                if not active_view:
                    active_view = FreeCADGui.activeView()

                if active_view and hasattr(active_view, "viewSelection"):
                    active_view.viewSelection()
                
                FreeCADGui.updateGui()
            except Exception as e:
                dm_logger.debug(f"create_dm_object: Selection/View setup failed for {name} ({type(e).__name__}): {e}")
        
        # dm_logger.debug(f"create_dm_object: {name} created successfully")
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            if getattr(obj, "Group", "Group 1") == "Group 2":
                obj.ViewObject.ShapeColor = (0.3, 0.5, 1.0)
            else:
                obj.ViewObject.ShapeColor = (1.0, 0.5, 0.0)
            obj.ViewObject.LineWidth = get_line_width()
            obj.ViewObject.PointSize = get_point_size()
            # Disable wireframe for SDF mesh objects - reduces render overhead
            if shape_type == "sdf":
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
    doc = FreeCAD.activeDocument()
    if not doc:
        return
    
    lw = get_line_width()
    ps = get_point_size()
    show_wire = get_show_wireframe()
    
    for obj in doc.Objects:
        if hasattr(obj, "ShapeType") and obj.ViewObject:
            # Native FreeCAD properties
            obj.ViewObject.LineWidth = lw
            # DM Proxy properties
            if hasattr(obj, "ShowWireframe"):
                obj.ShowWireframe = bool(show_wire)
                
            proxy = getattr(obj.ViewObject, "Proxy", None)
            if proxy and hasattr(proxy, "on_prefs_changed"):
                proxy.on_prefs_changed()
            obj.ViewObject.PointSize = ps
    
    if FreeCAD.GuiUp:
        FreeCADGui.updateGui()

