
import FreeCAD
import FreeCADGui
import Part
from pivy import coin
import numpy as np

# Try to import local lib
import sys
import os
def log_to_file(msg):
    try:
        with open("/tmp/fc_debug.log", "a") as f:
            f.write(f"[SDF] {msg}\n")
            f.flush()
            os.fsync(f.fileno())
    except:
        pass

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
    
    # 2. Primitives
    # We attempt to detect type by properties, or explicit "SDFType" property if we add one.
    # For now, we infer.
    
    # Torus
    if hasattr(obj, "MajorRadius") and hasattr(obj, "MinorRadius"):
        R = get_val(obj, "MajorRadius", 10.0)
        r = get_val(obj, "MinorRadius", 2.0)
        sdf = sdf_lib.SDFTorus(major_radius=R, minor_radius=r)

    # Cone (Radius and Height, but NO Length/Width)
    elif hasattr(obj, "Radius") and hasattr(obj, "Height") and not hasattr(obj, "Length"):
        r = get_val(obj, "Radius", 5.0)
        h = get_val(obj, "Height", 10.0)
        sdf = sdf_lib.SDFCone(radius=r, height=h)

    # Sphere (Radius only, no Height)
    elif hasattr(obj, "Radius") and not hasattr(obj, "Height") and not hasattr(obj, "MajorRadius"):
        r = get_val(obj, "Radius", 5.0)
        sdf = sdf_lib.SDFSphere(radius=r)

    # Box
    elif hasattr(obj, "Length") and hasattr(obj, "Width") and hasattr(obj, "Height"):
        l = get_val(obj, "Length", 10.0)
        w = get_val(obj, "Width", 10.0)
        h = get_val(obj, "Height", 10.0)
        
        sdf = sdf_lib.SDFBox(size=(l, w, h))
        
        # Center Offset Logic (Box only? Or all?)
        # Box matches centered logic in lib, but FreeCAD Box is corner-based usually.
        # Implemented offset for Box previously.
        # Other primitives (Sphere, Torus) are usually centered in FreeCAD Part primitives too, 
        # except Cone (often base at 0).
        # My SDFCone implementation expects Base at 0.
        # SDFSphere/Torus are centered.
        
        # Apply offset to placement ONLY for Box if needed to match FreeCAD convention.
        # FreeCAD Box: Corner at placement.
        # SDFBox: Center at 0.
        # So we shift by +Size/2.
        
        import FreeCAD
        offset = FreeCAD.Placement(FreeCAD.Vector(l/2.0, w/2.0, h/2.0), FreeCAD.Rotation())
        
        # Check if we already have a placement set on 'sdf'.
        # We will handle placement generically at the end, but this offset is specific to Box shape definition.
        
        total_p = obj.Placement.multiply(offset)
        sdf.set_placement(total_p)
        return sdf 

    # If we created a non-Box primitive, we might need to handle placement.
    # Sphere, Torus, Cone are usually centered or have defined origin matching SDF lib (or close enough).
    # Sphere: Center at 0. FreeCAD Sphere: Center at 0. Match.
    # Torus: Center at 0. FreeCAD Torus: Center at 0. Match.
    # Cone: Base at 0. FreeCAD Cone: Base at 0. Match.
    
    if sdf and hasattr(obj, "Placement"):
        sdf.set_placement(obj.Placement)
        return sdf
        
    FreeCAD.Console.PrintError(f"create_sdf_from_obj: Could not detect SDF type for {obj.Name}. Props: {dir(obj)}\n")
    return None # Failed to detect


class SDFRenderer:
    def __init__(self, vobj):
        log_to_file(f"SDFRenderer.init: {vobj.Object.Name}")
        vobj.Proxy = self
        self.vobj = vobj
        self.sobj = vobj.Object
        
    def attach(self, vobj):
        log_to_file(f"SDFRenderer.attach: {vobj.Object.Name}")
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
        
        # Shape Hints (Enable smooth shading)
        try:
            hints = coin.SoShapeHints()
            hints.vertexOrdering = coin.SoShapeHints.COUNTER_CLOCKWISE
            hints.shapeType = coin.SoShapeHints.SOLID
            hints.creaseAngle = 0.8 # Approx 45 degrees
            self.root.addChild(hints)
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"SDFRenderer: Failed to create SoShapeHints: {e}\n")
        
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
        
        # Initial Update - REMOVED FOR STABILITY
        # self.update()

    def updateData(self, fp, prop):
        log_to_file(f"SDFRenderer.updateData: {fp.Name} - {prop}")
        if prop in ["Length", "Width", "Height", "Resolution", "Margin", "SliceAxis", 
                    "WireframeColor", "WireframeWidth", "ShowVertices", "VertexSize",
                    "Radius", "MajorRadius", "MinorRadius"]:
            self.update()

    def getDisplayModes(self, vobj):
        return ["SDF Mesh"]

    def getDefaultDisplayMode(self):
        return "SDF Mesh"
        
    def getIcon(self):
        return None # Return None to use default or standard
        
    def claimChildren(self):
        # Correct implementation: Return children objects (Base/Tool) for Booleans
        # Return empty for Primitives
        children = []
        if hasattr(self.sobj, "Base") and self.sobj.Base:
            children.append(self.sobj.Base)
        if hasattr(self.sobj, "Tool") and self.sobj.Tool:
            children.append(self.sobj.Tool)
        return children

    def update(self):
        try:
            log_to_file(f"SDFRenderer.update: Start for {self.sobj.Name}")
            if not hasattr(self, 'coords'):
                log_to_file("SDFRenderer.update: No coords, skipping")
                return
                
            # Get parameters from object
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
            log_to_file(f"SDFRenderer.update: Creating SDF...")
            sdf = create_sdf_from_obj(self.sobj)
            if not sdf:
                 log_to_file(f"SDFRenderer.update: Failed to create SDF for {self.sobj.Name}")
                 FreeCAD.Console.PrintError(f"SDFRenderer.update: Failed to create SDF for {self.sobj.Name}\n")
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
            log_to_file(f"SDFRenderer.update: Generating Mesh (Res={res})...")
            
            mode = self.vobj.DisplayMode
            
            if mode == "SDF Mesh":
                # Clear Slices
                self.slice_coords.point.setNum(0)
                self.slice_lines.numVertices.setNum(0)
                
                # Updated to return normals
                log_to_file(f"SDFRenderer.update: calling mesh_from_sdf...")
                verts, faces, normals = sdf_lib.mesh_from_sdf(sdf, resolution=res, margin=margin)
                log_to_file(f"SDFRenderer.update: Mesh Generated. Verts: {len(verts) if verts is not None else 'None'}")
                
                if verts is None:
                    # No surface found
                    log_to_file("SDFRenderer.update: No surface found.")
                    FreeCAD.Console.PrintWarning(f"SDFRenderer.update: No surface generated for {self.sobj.Name}. Check bounds/resolution.\n")
                    self.coords.point.setNum(0)
                    self.face_set.coordIndex.setNum(0)
                    self.norms.vector.setNum(0)
                    return
                    
                # Update Coin3D
                log_to_file("SDFRenderer.update: Updating Coin3D Nodes...")
                
                # Vertices
                if verts is not None:
                     log_to_file(f"SDFRenderer.update: Setting {len(verts)} vertices...")
                     self.coords.point.setValues(0, len(verts), verts.tolist())
                
                # Normals
                if normals is not None and len(normals) > 0:
                    log_to_file(f"SDFRenderer.update: Setting {len(normals)} normals...")
                    self.norms.vector.setValues(0, len(normals), normals.tolist())
                else:
                    self.norms.vector.setNum(0)
                
                # Indices
                n_faces = faces.shape[0]
                log_to_file(f"SDFRenderer.update: Preparing indices for {n_faces} faces...")
                indices = np.full((n_faces, 4), -1, dtype=np.int32)
                indices[:, :3] = faces
                log_to_file(f"SDFRenderer.update: Setting coordIndex...")
                self.face_set.coordIndex.setValues(0, indices.size, indices.flatten().tolist())
                

                
                log_to_file("SDFRenderer.update: Coin3D Update Complete.")
            
            # Wireframe disabled for now
            self.box_coords.point.setNum(0)
            self.box_lines.coordIndex.setNum(0)
            self.box_points.numPoints.setValue(0)
                
            log_to_file("SDFRenderer.update: Finished.")
            
        except Exception as e:
            msg = f"SDFRenderer.update: Error: {e}"
            log_to_file(msg)
            import traceback
            FreeCAD.Console.PrintError(msg + "\n")
            traceback.print_exc()
            self.root.removeAllChildren()

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

