
import FreeCAD
import FreeCADGui
import Part
from pivy import coin
import numpy as np

# Try to import local lib
import sys
import os
# Ensure we can find our local modules
# This might be redundant if InitGui sets it up, but safe to have
wb_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if wb_path not in sys.path:
    sys.path.append(wb_path)

from FCDirectModeling import sdf_lib

class SDFRenderer:
    def __init__(self, vobj):
        vobj.Proxy = self
        self.vobj = vobj
        self.sobj = vobj.Object
        
    def attach(self, vobj):
        self.vobj = vobj
        self.sobj = vobj.Object
        
        self.root = coin.SoSeparator()
        self.root.ref()
        
        # Material
        self.mat = coin.SoMaterial()
        self.mat.diffuseColor.setValue(0.8, 0.8, 0.8)
        self.mat.specularColor.setValue(0.2, 0.2, 0.2)
        self.mat.shininess.setValue(0.2)
        self.root.addChild(self.mat)
        
        # Shape Hints
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTER_CLOCKWISE
        hints.shapeType = coin.SoShapeHints.SOLID
        self.root.addChild(hints)
        
        # Coordinates
        self.coords = coin.SoCoordinate3()
        self.root.addChild(self.coords)
        
        # Normals (optional, let Coin compute or compute from marching cubes)
        # self.norms = coin.SoNormal()
        # self.root.addChild(self.norms)
        
        # Faces
        self.face_set = coin.SoIndexedFaceSet()
        self.root.addChild(self.face_set)
        
        vobj.addDisplayMode(self.root, "SDF Mesh")
        
        # Initial Update
        self.update()

    def updateData(self, fp, prop):
        if prop in ["Length", "Width", "Height", "Resolution", "Margin"]:
            self.update()

    def getDisplayModes(self, vobj):
        return ["SDF Mesh"]

    def getDefaultDisplayMode(self):
        return "SDF Mesh"

    def update(self):
        if not hasattr(self, 'coords'):
            return
            
        # Get parameters from object
        # Assuming Box for now
        l = getattr(self.sobj, "Length", 10.0)
        w = getattr(self.sobj, "Width", 10.0)
        h = getattr(self.sobj, "Height", 10.0)
        res = getattr(self.sobj, "Resolution", 32)
        margin = getattr(self.sobj, "Margin", 0.1)
        
        # Create SDF Object
        sdf = sdf_lib.SDFBox(size=(l, w, h))
        
        # Generate Mesh
        # This can be slow! For interactive dragging we might want lower res
        try:
            verts, faces = sdf_lib.mesh_from_sdf(sdf, resolution=res, margin=margin)
            
            if verts is None:
                # No surface found
                self.coords.point.setNum(0)
                self.face_set.coordIndex.setNum(0)
                return
                
            # Update Coin3D
            # Vertices
            self.coords.point.setValues(0, len(verts), verts.tolist())
            
            # Faces (Triangle Strip or Indexed Face Set)
            # Marching cubes returns triangles [v1, v2, v3]
            # Coin3D expects [v1, v2, v3, -1, v4, v5, v6, -1 ...]
            
            # Optimization: 
            # faces is (N, 3)
            # We want (N, 4) where last column is -1
            n_faces = faces.shape[0]
            indices = np.full((n_faces, 4), -1, dtype=np.int32)
            indices[:, :3] = faces
            
            self.face_set.coordIndex.setValues(0, indices.size, indices.flatten().tolist())
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"SDF Update Failed: {e}\n")

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None

class SDFBoxFeature:
    def __init__(self, obj):
        obj.Proxy = self
        self.obj = obj
        
    def execute(self, obj):
        # We don't necessarily generate a Shape here if it's purely visual
        # But if we want export/compatibility, we could generate a mesh or shape
        pass

    def onDocumentRestored(self, obj):
        # Re-initialize anything if needed
        pass
