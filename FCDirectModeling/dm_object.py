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







# ─────────────────────────────────────────────────────────────────────────────
# DMObjectProxy — used for NURBS/BRep objects
# ─────────────────────────────────────────────────────────────────────────────

class DMObjectProxy:
    def __init__(self, obj, shape_type, params=None, placement=None):
        obj.Proxy = self
        self.is_preview = False
        
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
            obj.Points = params.get("points", [])
        elif shape_type == "point":
            if not hasattr(obj, "Position"):
                obj.addProperty("App::PropertyVector", "Position", "Point", "Position")
            obj.Position = params.get("position", FreeCAD.Vector(0,0,0))
        # Future: "surface" shape type for BSplineSurface patches

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""
        from . import nurbs_primitives as np_builders
        
        st = fp.ShapeType
        if st == "curve":
            return np_builders.build_curve(fp.Points)
        elif st == "point":
            return Part.Point(fp.Position).toShape()
        # Future: "surface" shape type
            
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from FCDirectModeling import dm_logger
            dm_logger.debug(f"DMObjectProxy: Recomputing {fp.Label} ({fp.ShapeType})")
            
            # Ensure the object has a shape
            new_shape = self.build_shape(fp)
            
            if new_shape.isNull():
                 dm_logger.warning(f"DMObjectProxy: Built NULL shape for {fp.Label}")
            else:
                 dm_logger.debug(f"DMObjectProxy: Shape built. Faces={len(new_shape.Faces)}, BoundBox={new_shape.BoundBox}")
                 
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

def create_dm_object(name, shape_type, params=None, is_preview=False, placement=None):
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
        obj.Proxy.is_preview = is_preview

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

