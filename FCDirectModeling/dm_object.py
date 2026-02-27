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
        if shape_type == "box":
            for p in ["Length", "Width", "Height"]:
                if not hasattr(obj, p): obj.addProperty("App::PropertyFloat", p, "Box", p)
            obj.Length = params.get("length", 10.0)
            obj.Width  = params.get("width", 10.0)
            obj.Height = params.get("height", 10.0)
                
        elif shape_type == "sphere":
            if not hasattr(obj, "Radius"): obj.addProperty("App::PropertyFloat", "Radius", "Sphere", "Radius")
            obj.Radius = params.get("radius", 5.0)
            
        elif shape_type == "cone":
            if not hasattr(obj, "Radius"): obj.addProperty("App::PropertyFloat", "Radius", "Cone", "Radius")
            if not hasattr(obj, "Height"): obj.addProperty("App::PropertyFloat", "Height", "Cone", "Height")
            obj.Radius = params.get("radius", 5.0)
            obj.Height = params.get("height", 10.0)
            
        elif shape_type == "torus":
            if not hasattr(obj, "MajorRadius"): obj.addProperty("App::PropertyFloat", "MajorRadius", "Torus", "Major Radius")
            if not hasattr(obj, "MinorRadius"): obj.addProperty("App::PropertyFloat", "MinorRadius", "Torus", "Minor Radius")
            obj.MajorRadius = params.get("major_r", 10.0)
            obj.MinorRadius = params.get("minor_r", 2.0)

    def build_shape(self, fp):
        """Return a Part.Shape based on the object's properties."""
        # This is a stub to be implemented in subsequent tasks.
        import Part
        return Part.Shape()

    def execute(self, fp):
        """Called by FreeCAD to recompute the object."""
        try:
            from FCDirectModeling import dm_logger
            dm_logger.debug(f"DMObjectProxy: Recomputing {fp.Label} ({fp.ShapeType})")
            
            # Ensure the object has a shape
            fp.Shape = self.build_shape(fp)
            
        except Exception as e:
            from FCDirectModeling import dm_logger
            dm_logger.error(f"DMObject.execute error: {e}")

    def __setstate__(self, state):
        pass

class DMViewProvider:
    """ViewProvider for DM objects. Shows an orange part icon."""
    def __init__(self, vobj):
        vobj.Proxy = self
        
    def attach(self, vobj):
        self.Object = vobj.Object
        
    def getDisplayModes(self, vobj):
        return ["Standard"]
        
    def getDefaultDisplayMode(self):
        return "Standard"
        
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
        
    except Exception as e:
        dm_logger.error(f"create_dm_object FAILED: {e}")
        import traceback
        dm_logger.error(traceback.format_exc())
        return None

