import FreeCADGui
import FreeCAD

try:
    # See if projectPoint exists
    v = FreeCADGui.ActiveDocument.ActiveView
    
    # Let's inspect getViewer()
    viewer = v.getViewer()
    print("Has projectPoint in view?", hasattr(v, "projectPoint"))
    print("Has projectPoint in viewer?", hasattr(viewer, "projectPoint"))
except Exception as e:
    pass
