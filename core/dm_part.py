import FreeCAD
import Part
from pivy import coin

class DMPart:
    def __init__(self, obj):
        obj.Proxy = self
        
        # We might need properties to store what kind of DM part this is
        # Or we just store the explicit points for the NURBS logic later
        if not hasattr(obj, "ControlPoints"):
            obj.addProperty("App::PropertyVectorList", "ControlPoints", "DirectModeling", "List of explicit control points")
            
        if not hasattr(obj, "Shape"):
            obj.addProperty("Part::PropertyPartShape", "Shape", "DirectModeling", "The resulting BRep Shape")

    def execute(self, obj):
        """Called by FreeCAD when the object needs recomputation."""
        # For now, it just holds the Shape. 
        # In the future, this is where we'd rebuild the shape from ControlPoints
        pass

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


class ViewProviderDMPart:
    def __init__(self, vobj):
        vobj.Proxy = self
        
        # We need these properties to style the explicit handles correctly
        if not hasattr(vobj, "VertexColor"):
             vobj.addProperty("App::PropertyColor", "VertexColor", "DirectModeling", "Color of the vertex handles")
             vobj.VertexColor = (1.0, 0.0, 0.0)
        if not hasattr(vobj, "VertexSize"):
             vobj.addProperty("App::PropertyFloat", "VertexSize", "DirectModeling", "Size of the vertex handles")
             vobj.VertexSize = 2.0
             
    def attach(self, vobj):
        self.Object = vobj.Object
        
        # Create a container node for our custom Coin3D visuals
        self.root_node = coin.SoSeparator()
        vobj.addDisplayMode(self.root_node, "Standard")
        
        # Setup nodes for Vertices
        self.vertex_sep = coin.SoSeparator()
        self.vertex_mat = coin.SoMaterial()
        self.vertex_mat.diffuseColor.setValue(1.0, 0.0, 0.0) # Red
        self.vertex_sep.addChild(self.vertex_mat)
        
        # For clickable volume, we use SoMultipleCopy and SoSphere for vertices
        # We need a matrix for each vertex translation
        self.vertex_matrices = coin.SoMultipleCopy()
        self.vertex_sep.addChild(self.vertex_matrices)
        
        # A template sphere
        self.sphere = coin.SoSphere()
        self.sphere.radius.setValue(0.5) # The actual picking radius
        self.vertex_sep.addChild(self.sphere)
        
        self.root_node.addChild(self.vertex_sep)
        
        self.updateData(vobj, "Shape")

    def updateData(self, fp, prop):
        if prop == "Shape" and hasattr(self.Object, "Shape") and self.Object.Shape is not None:
            try:
                # Part OCCException might be thrown if the shape isn't valid yet
                if not self.Object.Shape.isValid():
                    return
            except Exception as e:
                from . import dm_logger
                dm_logger.debug(f"ViewProviderDMPart.updateData: Shape validation failed: {e}")
                return
                
            shape = self.Object.Shape
            
            # Extract internal vertices completely
            self.vertex_matrices.matrix.deleteValues(0)
            idx = 0
            for v in shape.Vertexes:
                 pt = v.Point
                 mat = coin.SbMatrix()
                 mat.setTranslate(coin.SbVec3f(pt.x, pt.y, pt.z))
                 self.vertex_matrices.matrix.set1Value(idx, mat)
                 idx += 1

    def getDisplayModes(self, obj):
        return ["Standard"]

    def getDefaultDisplayMode(self):
        return "Standard"

    def setDisplayMode(self, mode):
        return mode

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def create_dm_part(name="DM_Part"):
    doc = FreeCAD.activeDocument()
    if not doc:
         doc = FreeCAD.newDocument()
         
    obj = doc.addObject("Part::FeaturePython", name)
    DMPart(obj)
    ViewProviderDMPart(obj.ViewObject)
    
    return obj
