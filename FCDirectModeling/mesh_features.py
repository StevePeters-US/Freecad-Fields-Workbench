
import numpy as np
from FCDirectModeling import sdf_logger

def build_adjacency(faces):
    """
    Build adjacency graph for a mesh.
    faces: (N, 3) array of vertex indices.
    Returns: 
        adjacency: dict {face_idx: [neighbor_face_indices]}
        shared_edges: dict {(min_f, max_f): (v1, v2)} # Edge definition
    """
    adjacency = {i: [] for i in range(len(faces))}
    
    # Edge to faces map
    # Key: (min_v, max_v), Value: [face_indices]
    edge_map = {}
    
    for f_idx, face in enumerate(faces):
        # Edges: (0,1), (1,2), (2,0)
        edges = [
            tuple(sorted((face[0], face[1]))),
            tuple(sorted((face[1], face[2]))),
            tuple(sorted((face[2], face[0])))
        ]
        
        for edge in edges:
            if edge not in edge_map:
                edge_map[edge] = []
            edge_map[edge].append(f_idx)
            
    # Build adjacency
    shared_edges = {}
    
    for edge, f_indices in edge_map.items():
        if len(f_indices) == 2:
            f1, f2 = f_indices
            adjacency[f1].append(f2)
            adjacency[f2].append(f1)
            
            # Key by sorted face pair
            pair = tuple(sorted((f1, f2)))
            shared_edges[pair] = edge
            
        elif len(f_indices) > 2:
            # Non-manifold edge? 
            # Link all to all?
            for i in range(len(f_indices)):
                for j in range(i+1, len(f_indices)):
                    f1 = f_indices[i]
                    f2 = f_indices[j]
                    if f2 not in adjacency[f1]: adjacency[f1].append(f2)
                    if f1 not in adjacency[f2]: adjacency[f2].append(f1)
                    
    return adjacency, shared_edges

def compute_dihedral_angles(normals, adjacency):
    """
    Compute angle deviation (cosine similarity) between adjacent faces.
    normals: (N, 3) face normals.
    adjacency: dict {f: [neighbors]}
    Returns: dict {(f1, f2): dot_product}
    """
    angles = {}
    for f1, neighbors in adjacency.items():
        n1 = normals[f1]
        for f2 in neighbors:
            if f2 < f1: continue # Already computed
            n2 = normals[f2]
            
            # Dot product
            dot = np.dot(n1, n2)
            angles[(f1, f2)] = dot
            
    return angles

def segment_mesh(faces, normals, angle_threshold_deg=30.0):
    """
    Segment mesh into regions based on normal similarity.
    faces: (N, 3)
    normals: (N, 3) - these are per-face normals! (Marching Cubes usually returns per-vertex norms, need to compute face norms)
    angle_threshold_deg: Max deviation to be considered same region.
    
    Returns: 
        segments: list of lists of face indices.
    """
    
    # Convert threshold to cosine
    import math
    threshold = math.cos(math.radians(angle_threshold_deg))
    
    sdf_logger.debug("mesh_features: Building Adjacency...")
    adj, _ = build_adjacency(faces)
    
    sdf_logger.debug("mesh_features: Computing Angles...")
    # We compute angles on the fly during traversal
    
    num_faces = len(faces)
    visited = np.zeros(num_faces, dtype=bool)
    segments = []
    
    for i in range(num_faces):
        if visited[i]:
            continue
            
        # Start new region
        region = []
        stack = [i]
        visited[i] = True
        
        # Region representative normal (e.g. average normal of region so far? Or just neighbor-to-neighbor check?)
        # Simple region growing: neighbor check.
        # Issue: Slowly curving surface (cylinder) has high neighbor sim, but total deviation is large.
        # We want to segment "Smooth" patches vs "Sharp" edges.
        # So "same region" = "smooth transition".
        # Yes, neighbor check is correct for "smooth patch".
        # A cylinder IS a single smooth patch.
        # A box face IS a single smooth patch.
        # The edge between box faces is SHARP ( dot product near 0 ).
        
        while stack:
            curr = stack.pop()
            region.append(curr)
            n_curr = normals[curr]
            
            for neighbor in adj[curr]:
                if visited[neighbor]:
                    continue
                
                n_neighbor = normals[neighbor]
                dot = np.dot(n_curr, n_neighbor)
                
                # If dot > threshold (means angle is small), add to region
                if dot >= threshold:
                    visited[neighbor] = True
                    stack.append(neighbor)
        
        segments.append(region)
        
    return segments

def compute_face_normals(verts, faces):
    """
    Compute face normals from vertices.
    verts: (V, 3)
    faces: (F, 3)
    Returns: (F, 3) normalized normals
    """
    # Tris
    v0 = verts[faces[:, 0]]
    v1 = verts[faces[:, 1]]
    v2 = verts[faces[:, 2]]
    
    vec1 = v1 - v0
    vec2 = v2 - v0
    
    cross = np.cross(vec1, vec2)
    mags = np.linalg.norm(cross, axis=1, keepdims=True)
    mags[mags == 0] = 1.0
    
    return cross / mags
