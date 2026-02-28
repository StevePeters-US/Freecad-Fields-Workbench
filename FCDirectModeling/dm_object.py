"""
FCDirectModeling/dm_object.py

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

# ─────────────────────────────────────────────────────────────────────────────
# DM Settings helpers
# ─────────────────────────────────────────────────────────────────────────────

_PARAM_PATH = "User parameter:FCDirectModeling"


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







# ─────────────────────────────────────────────────────────────────────────────
# DMObjectProxy — used for NURBS/BRep objects
# ─────────────────────────────────────────────────────────────────────────────

class DMObjectProxy:
    def __init__(self, obj, shape_type, params=None, placement=None):
        obj.Proxy = self
        
        if not hasattr(obj, "ShapeType"):
            obj.addProperty("App::PropertyString", "ShapeType", "DM", "Type of primitive")
        obj.ShapeType = shape_type
        
        from FCDirectModeling import dm_logger
        dm_logger.debug(f"DMObjectProxy.__init__: type={shape_type}, has_placement={placement is not None}")
        
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
            obj.Points = params.get("Points", [])
            obj.HandleIn = params.get("HandleIn", [])
            obj.HandleOut = params.get("HandleOut", [])
        elif shape_type == "point":
            if not hasattr(obj, "Position"):
                obj.addProperty("App::PropertyVector", "Position", "Point", "Position")
            obj.Position = params.get("Position", FreeCAD.Vector(0,0,0))
        elif shape_type == "patch":
            if not hasattr(obj, "ControlGrid"):
                # Nested list of vectors is not a standard FreeCAD property type.
                # Use App::PropertyString to store it as a JSON string or just use 
                # a flat PropertyVectorList and store the grid dimensions.
                # For now, let's use PropertyVectorList and a separate UCount/VCount.
                obj.addProperty("App::PropertyVectorList", "ControlGrid", "Patch", "Control point grid")
                obj.addProperty("App::PropertyInteger", "UCount", "Patch", "Width of grid")
                obj.addProperty("App::PropertyInteger", "VCount", "Patch", "Height of grid")
            
            grid = params.get("ControlGrid", [[]])
            if grid and grid[0]:
                obj.UCount = len(grid[0])
                obj.VCount = len(grid)
                flat_list = [p for row in grid for p in row]
                obj.ControlGrid = flat_list

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""
        from . import nurbs_primitives as np_builders
        
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
                
            curve = DMCurve(dm_points)
            return curve.to_shape()
        elif st == "point":
            return Part.Point(fp.Position).toShape()
        elif st == "patch":
            from .nurbs_geometry import DMPatch
            
            grid = []
            u_count = fp.UCount
            v_count = fp.VCount
            flat_list = fp.ControlGrid
            
            if u_count > 0 and v_count > 0 and len(flat_list) == u_count * v_count:
                for v in range(v_count):
                    row = flat_list[v*u_count : (v+1)*u_count]
                    grid.append(row)
            
            patch = DMPatch(grid)
            return patch.to_shape()
            
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from FCDirectModeling import dm_logger
            # dm_logger.debug(f"DMObjectProxy: Recomputing {fp.Label} ({fp.ShapeType})")
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
            from FCDirectModeling import dm_logger
            dm_logger.exception(f"DMObject.execute error for {fp.Label}")

    def __setstate__(self, state):
        pass

class DMViewProvider:
    """ViewProvider for DM objects. Shows an orange part icon."""
    def __init__(self, vobj):
        vobj.Proxy = self
        self.setup_view(vobj)
        
    def setup_view(self, vobj):
        vobj.PointColor = (1.0, 0.5, 0.0)
        vobj.LineColor = (1.0, 0.5, 0.0)
        if hasattr(vobj, "PointStyle"):
            vobj.PointStyle = "Spheres"
        vobj.DisplayMode = "Flat Lines"

    def attach(self, vobj):
        self.Object = vobj.Object
        
    def updateData(self, fp, prop):
        pass

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
    from FCDirectModeling import dm_logger
    
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    try:
        dm_logger.debug(f"create_dm_object: name={name}, type={shape_type}, has_placement={placement is not None}")
        obj = doc.addObject("Part::FeaturePython", name)
        DMObjectProxy(obj, shape_type, params, placement=placement)

        if FreeCAD.GuiUp:
            DMViewProvider(obj.ViewObject)
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                try:
                    obj.ViewObject.Visibility = True
                except Exception:
                    pass
        
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
            except Exception:
                pass
        
        dm_logger.debug(f"create_dm_object: {name} created successfully")
        return obj
        
    except Exception:
        dm_logger.exception(f"create_dm_object FAILED for {name}")
        return None

