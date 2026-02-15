
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

def create_sdf_from_obj(obj):
    """
    Factory to create an sdf_lib.SDFObject from a FreeCAD FeaturePython object.
    Handles Boxes and Booleans recursively.
    """
    if not obj:
        return None

    # Helper to safe-get values
    def get_val(obj, name, default=None):
        if hasattr(obj, name):
            val = getattr(obj, name)
            if hasattr(val, "Value"): return val.Value # Base.Quantity
            try: return float(val) 
            except: return default
        return default

    sdf = None
    
    # 1. Boolean Operations
    if hasattr(obj, "Operation") and hasattr(obj, "Base") and hasattr(obj, "Tool"):
        base = obj.Base
        tool = obj.Tool
        op = getattr(obj, "Operation", "Union") # Enum property usually string
        
        # FreeCAD Link property returns None if empty, or the object
        if base and tool:
            sdf_base = create_sdf_from_obj(base)
            sdf_tool = create_sdf_from_obj(tool)
            
            if sdf_base and sdf_tool:
                if op == "Union":
                    sdf = sdf_lib.SDFUnion(sdf_base, sdf_tool)
                elif op == "Difference":
                    sdf = sdf_lib.SDFDifference(sdf_base, sdf_tool)
                elif op == "Intersection":
                    sdf = sdf_lib.SDFIntersection(sdf_base, sdf_tool)
    
    # 2. Box Primitive (Fallback)
    elif hasattr(obj, "Length") and hasattr(obj, "Width") and hasattr(obj, "Height"):
        l = get_val(obj, "Length", 10.0)
        w = get_val(obj, "Width", 10.0)
        h = get_val(obj, "Height", 10.0)
        
        sdf = sdf_lib.SDFBox(size=(l, w, h))
        
        # Center Offset Logic
        # Box is defined 0..L, SDF is -L/2..L/2
        # Apply offset to placement
        import FreeCAD
        offset = FreeCAD.Placement(FreeCAD.Vector(l/2.0, w/2.0, h/2.0), FreeCAD.Rotation())
        
        # We need to apply this offset as a 'child' transform of the object placement
        # But SDFObject only has one matrix.
        # We must multiply them.
        # Total = Obj.Placement * Offset
        
        total_p = obj.Placement.multiply(offset)
        sdf.set_placement(total_p)
        return sdf # Return early because we handled placement

    # Default return
    if sdf and hasattr(obj, "Placement"):
        sdf.set_placement(obj.Placement)
        
    return sdf


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

        # Create SDF Object
        sdf = create_sdf_from_obj(self.sobj)
        if not sdf:
             # If creation failed, clear everything
             self.coords.point.setNum(0)
             self.face_set.coordIndex.setNum(0)
             self.box_coords.point.setNum(0)
             self.box_lines.coordIndex.setNum(0)
             self.box_points.numPoints.setValue(0)
             return
        
        # Note: Placement is already handled inside create_sdf_from_obj

        # Get Parameters for Mesh Generation
        res = getattr(self.sobj, "Resolution", 32)
        margin = get_val("Margin", 0.1)
        
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
                
                # Indices
                n_faces = faces.shape[0]
                indices = np.full((n_faces, 4), -1, dtype=np.int32)
                indices[:, :3] = faces
                self.face_set.coordIndex.setValues(0, indices.size, indices.flatten().tolist())
            
            # Update Wireframe Box (Only for Box type)
            if hasattr(self.sobj, "Length") and hasattr(self.sobj, "Width") and hasattr(self.sobj, "Height"):
                l = get_val("Length", 10.0)
                w = get_val("Width", 10.0)
                h = get_val("Height", 10.0)
                
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
            else:
                # Hide box for non-box objects (e.g. Booleans)
                self.box_coords.point.setNum(0)
                self.box_lines.coordIndex.setNum(0)
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

class SDFBooleanFeature:
    def __init__(self, obj):
        obj.Proxy = self
        self.obj = obj
        
    def execute(self, obj):
        # Trigger recompute of dependent objects if needed
        # Visuals are handled by ViewProvider (SDFRenderer)
        pass

    def onDocumentRestored(self, obj):
        pass

