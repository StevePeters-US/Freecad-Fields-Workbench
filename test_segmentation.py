
import FreeCAD
import Part
import Mesh
import numpy as np
import sys
import os

# Add path
sys.path.append("/home/steve/Documents/Github/Freecad-Direct-Modeling")

from FCDirectModeling import sdf_lib
from FCDirectModeling import mesh_features

def create_colored_mesh(doc, name, verts, faces, color):
    m = Mesh.Mesh()
    for f in faces:
        m.addFacet(verts[f[0]], verts[f[1]], verts[f[2]])
    
    obj = doc.addObject("Mesh::Feature", name)
    obj.Mesh = m
    obj.ViewObject.ShapeColor = color
    return obj

def test_segmentation():
    doc = FreeCAD.newDocument("SegmentationTest")
    
    # 1. Create SDF (Box usually)
    print("Creating SDF Box...")
    sdf = sdf_lib.SDFBox(20.0) # 20x20x20 box
    
    # 2. Mesh
    print("Meshing...")
    verts, faces, vertex_normals = sdf_lib.mesh_from_sdf(sdf, resolution=20, margin=0.1)
    
    if verts is None:
        print("Meshing failed.")
        return
        
    print(f"Mesh: {len(verts)} verts, {len(faces)} faces")
    
    # Convert numpy verts to FreeCAD Vectors for later use
    fc_verts = [FreeCAD.Vector(v[0], v[1], v[2]) for v in verts]
    
    # 3. Compute Face Normals (needed for segmentation)
    print("Computing Face Normals...")
    face_normals = mesh_features.compute_face_normals(verts, faces)
    
    # 4. Segment
    print("Segmenting...")
    # Angle threshold: 45 degrees. 
    # Box edges are 90 deg, so they should be sharp boundaries.
    # Flat faces are 0 deg deviation.
    segments = mesh_features.segment_mesh(faces, face_normals, angle_threshold_deg=45.0)
    
    print(f"Found {len(segments)} segments.")
    
    # 5. Visualize
    colors = [
        (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
        (1.0, 1.0, 0.0), (0.0, 1.0, 1.0), (1.0, 0.0, 1.0)
    ]
    
    for i, seg in enumerate(segments):
        # seg is list of face indices
        seg_faces = faces[seg]
        color = colors[i % len(colors)]
        create_colored_mesh(doc, f"Segment_{i}", fc_verts, seg_faces, color)
        print(f"Segment {i}: {len(seg)} faces")
        
    FreeCAD.Gui.SendMsgToActiveView("ViewFit")
    print("Done.")

if __name__ == "__main__":
    test_segmentation()
