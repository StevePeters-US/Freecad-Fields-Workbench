
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
        
        # Shape Hints (Commented out due to Pivy AttributeError on Linux)
        # hints = coin.SoShapeHints()
        # hints.vertexOrdering = coin.SoShapeHints.COUNTER_CLOCKWISE
        # hints.shapeType = coin.SoShapeHints.SOLID
        # self.root.addChild(hints)
        
        # Coordinates
        self.coords = coin.SoCoordinate3()
        self.root.addChild(self.coords)
        
        # Normals (optional, let Coin compute or compute from marching cubes)
        # Normals (optional, let Coin compute or compute from marching cubes)
        self.norms = coin.SoNormal()
        self.root.addChild(self.norms)
        
        # Faces
        self.face_set = coin.SoIndexedFaceSet()
        self.root.addChild(self.face_set)
        
        vobj.addDisplayMode(self.root, "SDF Mesh")
        vobj.addDisplayMode(self.root, "SDF Mesh")
        vobj.addDisplayMode(self.root, "SDF Slices")
        
        # Wireframe Box (Always visible or separate mode? For now always visible semi-transparent or overlay)
        # Actually, let's make it part of the root group so it overlays
        self.box_sep = coin.SoSeparator()
        self.root.addChild(self.box_sep)
        
        self.box_mat = coin.SoMaterial()
        self.box_mat.diffuseColor.setValue(1.0, 1.0, 0.0) # Yellow
        self.box_mat.transparency.setValue(0.5)
        self.box_sep.addChild(self.box_mat)
        
        self.box_style = coin.SoDrawStyle()
        self.box_style.lineWidth = 2.0
        self.box_style.pointSize = 5.0
        self.box_sep.addChild(self.box_style)
        
        self.box_coords = coin.SoCoordinate3()
        self.box_sep.addChild(self.box_coords)
        
        self.box_lines = coin.SoIndexedLineSet()
        self.box_sep.addChild(self.box_lines)
        
        self.box_points = coin.SoPointSet()
        self.box_sep.addChild(self.box_points)
        
        # Slices
        self.slice_sep = coin.SoSeparator()
        self.root.addChild(self.slice_sep)
        
        self.slice_mat = coin.SoMaterial()
        self.slice_mat.diffuseColor.setValue(0.0, 1.0, 0.0) # Green for slices
        self.slice_sep.addChild(self.slice_mat)
        
        self.slice_coords = coin.SoCoordinate3()
        self.slice_sep.addChild(self.slice_coords)
        
        self.slice_lines = coin.SoLineSet()
        self.slice_sep.addChild(self.slice_lines)
        
        # Initial Update
        self.update()

    def updateData(self, fp, prop):
        if prop in ["Length", "Width", "Height", "Resolution", "Margin", "SliceAxis", 
                    "WireframeColor", "WireframeWidth", "ShowVertices", "VertexSize"]:
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
        # Ideally check self.sobj.Length.Value if it's a Quantity, or just float(self.sobj.Length)
        
        def get_val(name, default=10.0):
            if hasattr(self.sobj, name):
                val = getattr(self.sobj, name)
                if hasattr(val, "Value"): # Base.Quantity
                    return val.Value
                try:
                    return float(val)
                except:
                    return default
            return default

        l = get_val("Length", 10.0)
        w = get_val("Width", 10.0)
        h = get_val("Height", 10.0)
        res = getattr(self.sobj, "Resolution", 32) # Usually int
        margin = get_val("Margin", 0.1)
        
        # Create SDF Object
        sdf = sdf_lib.SDFBox(size=(l, w, h))
        
        # Center the SDF Box to match FreeCAD Part::Box (which is corner-based)
        # Part::Box is 0..L, SDFBox is -L/2..L/2
        # We need to move SDFBox by L/2, W/2, H/2
        import FreeCAD
        m = FreeCAD.Matrix()
        m.move(FreeCAD.Vector(l/2.0, w/2.0, h/2.0))
        sdf.set_placement(FreeCAD.Placement(m))
        
        # Generate Mesh
        # This can be slow! For interactive dragging we might want lower res
        # Prepare Coin3D Nodes based on Mode
        mode = self.vobj.DisplayMode
        
        # Reset everything first (simple approach) or toggle visibility
        # self.coords.point.setNum(0)
        # self.face_set.coordIndex.setNum(0)
        # self.norms.vector.setNum(0)
        # self.slice_coords.point.setNum(0)
        # self.slice_lines.numVertices.setNum(0)
        
        try:
            if mode == "SDF Mesh":
                # Clear Slices
                self.slice_coords.point.setNum(0)
                self.slice_lines.numVertices.setNum(0)
                
                # Updated to return normals
                verts, faces, normals = sdf_lib.mesh_from_sdf(sdf, resolution=res, margin=margin)
                
                if verts is None:
                    # No surface found
                    self.coords.point.setNum(0)
                    self.face_set.coordIndex.setNum(0)
                    self.norms.vector.setNum(0)
                    return
                    
                # Update Coin3D
                # Vertices
                self.coords.point.setValues(0, len(verts), verts.tolist())
                
                # Normals
                if normals is not None and len(normals) > 0:
                    self.norms.vector.setValues(0, len(normals), normals.tolist())
                else:
                    self.norms.vector.setNum(0)
                
                # Faces
                n_faces = faces.shape[0]
                indices = np.full((n_faces, 4), -1, dtype=np.int32)
                indices[:, :3] = faces
                self.face_set.coordIndex.setValues(0, indices.size, indices.flatten().tolist())
            
            # Update Wireframe Box
            # Box is 0..L, 0..W, 0..H (Standard FreeCAD convention)
            # The SDF object center was moved to L/2, W/2, H/2 to align with this.
            # So the visual representation should be a box from 0,0,0 to L,W,H
            
            # Wait, our mesh vertices (real_verts) are already in World Coordinates relative to the object placement.
            # 'real_verts' from mesh_from_sdf matches the grid which matches SDFBox (which is -L/2..L/2).
            # But we applied a placement to the SDFBox in `update` to move it by +L/2.
            # The `mesh_from_sdf` logic applies `sdf.set_placement`.
            # BUT `mesh_from_sdf` returns vertices *transformed by that placement*?
            # Let's check mesh_from_sdf... 
            # It uses `grid_min` which comes from `bound_min`. `bound_min` comes from `sdf.bounds()`.
            # `sdf.bounds()` applies the matrix.
            # So `real_verts` are already shifted to 0..L.
            
            # So we just need to draw a box 0..L, 0..W, 0..H.
            
            b_coords = [
                [0,0,0], [l,0,0], [l,w,0], [0,w,0],
                [0,0,h], [l,0,h], [l,w,h], [0,w,h]
            ]
            self.box_coords.point.setValues(0, 8, b_coords)
            
            b_lines = [
                0,1,2,3,0,-1, # Base
                4,5,6,7,4,-1, # Top
                0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1 # Sides
            ]
            self.box_lines.coordIndex.setValues(0, len(b_lines), b_lines)
            
            # Wireframe Style & Points
            wf_color = getattr(self.sobj, "WireframeColor", (1.0, 1.0, 0.0))
            if hasattr(wf_color, "__len__") and len(wf_color) > 3:
                wf_color = wf_color[:3]
                
            wf_width = getattr(self.sobj, "WireframeWidth", 2.0)
            show_verts = getattr(self.sobj, "ShowVertices", True)
            vert_size = getattr(self.sobj, "VertexSize", 5.0)
            
            self.box_mat.diffuseColor.setValue(wf_color)
            self.box_style.lineWidth.setValue(wf_width)
            self.box_style.pointSize.setValue(vert_size)
            
            if show_verts:
                self.box_points.startIndex.setValue(0)
                self.box_points.numPoints.setValue(8)
            else:
                self.box_points.numPoints.setValue(0)
                
            # Removed SDF Slices mode as per user request
            
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
