import FreeCAD
import FreeCADGui
from pivy import coin
from FCDirectModeling.dm_part import DMPart, ViewProviderDMPart

class CommandDMTweak:
    """Activates the Tweak mode to select and drag vertices, edges, or faces on a DM_Part."""
    
    def __init__(self):
        self.active = False
        self.cb_id = None
        self.dragger = None
        self.selected_subobj = None

    def GetResources(self):
        return {
            'Pixmap': 'DirectModeling.svg', # TODO: Need a tweak icon
            'MenuText': "Tweak sub-elements",
            'ToolTip': "Click to select a vertex, edge, or face and drag it."
        }

    def Activated(self):
        if self.active:
            self.deactivate()
        else:
            self.activate()

    def IsActive(self):
        return FreeCAD.activeDocument() is not None

    def activate(self):
        self.active = True
        FreeCAD.Console.PrintMessage("DM Tweak Mode: ON. Select a vertex, edge, or face.\n")
        
        # FreeCAD Selection Observer
        FreeCADGui.Selection.addObserver(self)

    def deactivate(self):
        self.active = False
        FreeCAD.Console.PrintMessage("DM Tweak Mode: OFF.\n")
        FreeCADGui.Selection.removeObserver(self)
        self.remove_dragger()

    def remove_dragger(self):
        if self.dragger:
             # Find where it was attached and remove it
             # For now, just removing from active view root might be tricky if not tracked.
             # We should attach it to the ViewProvider's root node so we can remove it cleanly.
             pass
        self.dragger = None

    def addSelection(self, doc, obj, sub, p):
        """Called when something is selected."""
        if not self.active: return
        
        FreeCAD.Console.PrintMessage(f"Tweak Selected: {obj}, {sub}\n")
        
        # Check if it's a DM_Part
        fc_obj = FreeCAD.activeDocument().getObject(obj)
        if not fc_obj or not hasattr(fc_obj.Proxy, '__class__') or fc_obj.Proxy.__class__.__name__ != 'DMPart':
             FreeCAD.Console.PrintError("Selected object is not a DM_Part.\n")
             return
             
        self.selected_subobj = (fc_obj, sub)
        self.attach_dragger(fc_obj, sub)

    def removeSelection(self, doc, obj, sub):
        # Clean up if deselected
        pass

    def clearSelection(self, doc):
        self.remove_dragger()
        self.selected_subobj = None

    def attach_dragger(self, obj, subname):
        self.remove_dragger()
        
        # Determine center of the sub-element
        shape = obj.Shape
        try:
             subelement = shape.getElement(subname)
             center = subelement.CenterOfMass
        except Exception as e:
             FreeCAD.Console.PrintError(f"Could not get center of {subname}: {e}\n")
             return
             
        # create visual dragger
        self.dragger = coin.SoTransformBoxDragger()
        
        # We need a matrix to place the dragger at the center
        mat = coin.SbMatrix()
        mat.setTranslate(coin.SbVec3f(center.x, center.y, center.z))
        
        # Add to viewprovider root
        vp = obj.ViewObject.Proxy
        if hasattr(vp, "root_node"):
             vp.root_node.addChild(self.dragger)
             # Wait, the dragger modifies a transform node in its path.
             # Better way: Setup a field sensor or callback on the dragger's motionMatrix
             self.dragger_cb = self.dragger.addMotionCallback(self.on_drag)
             self.dragger_finish_cb = self.dragger.addFinishCallback(self.on_drag_finish)
             
             # Initial placement
             self.dragger.motionMatrix.setValue(mat)

    def on_drag(self, *args):
        # Live update of the BRep is too slow for python. 
        # We'll just let the bounding box Move visually
        pass

    def on_drag_finish(self, *args):
        if not self.dragger or not self.selected_subobj: return
        
        matrix = self.dragger.motionMatrix.getValue()
        translation = matrix.getTranslate()
        
        # We now have the Delta Translation of the vertex/edge/face.
        delta = FreeCAD.Vector(translation[0], translation[1], translation[2])
        
        FreeCAD.Console.PrintMessage(f"Dragged {self.selected_subobj[1]} by {delta}\n")
        
        # In a real DM engine, we now reconstruct the BRep.
        # For now, we just print and we will flesh this out.
        
        self.remove_dragger()
        self.selected_subobj = None

FreeCADGui.addCommand('DM_Tweak', CommandDMTweak())
