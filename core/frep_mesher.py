import FreeCAD
import Mesh
import Part
import numpy as np

from core import dm_logger
from core.frep.frep_field import FRepField
from core.frep.marching_cubes.mc_tables import edgeTable, triTable
from core.dm_object import get_frep_storage_type

class FRepMesher:
    """Abstract base class for all F-Rep meshing protocols."""
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        raise NotImplementedError("mesher must implement mesh()")

class MarchingCubesMesher(FRepMesher):
    """Uniform-grid marching cubes generating a triangle mesh."""
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        min_b, max_b = field.bounding_box()
        
        # Prevent 0 dimension error if bounding box is flat
        if max_b.x - min_b.x < 1e-4: max_b.x += 1; min_b.x -= 1
        if max_b.y - min_b.y < 1e-4: max_b.y += 1; min_b.y -= 1
        if max_b.z - min_b.z < 1e-4: max_b.z += 1; min_b.z -= 1

        res_x = res_y = res_z = resolution

        x = np.linspace(min_b.x, max_b.x, res_x + 1)
        y = np.linspace(min_b.y, max_b.y, res_y + 1)
        z = np.linspace(min_b.z, max_b.z, res_z + 1)
        
        X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
        pts = np.vstack([X.ravel(), Y.ravel(), Z.ravel()]).T
        
        # Vectorized evaluation
        vals = field.evaluate_grid(pts).reshape((res_x+1, res_y+1, res_z+1))
        
        triangles = []
        
        # MC 8 corners
        cube_offsets = [
            (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)
        ]
        
        # MC 12 edges
        edge_pairs = [
            (0,1), (1,2), (2,3), (3,0),
            (4,5), (5,6), (6,7), (7,4),
            (0,4), (1,5), (2,6), (3,7)
        ]

        for i in range(res_x):
            for j in range(res_y):
                for k in range(res_z):
                    v_corners = [vals[i+ox, j+oy, k+oz] for ox, oy, oz in cube_offsets]
                    
                    cube_index = 0
                    for c_idx in range(8):
                        if v_corners[c_idx] < 0:
                            cube_index |= (1 << c_idx)
                            
                    edges = edgeTable[cube_index]
                    if edges == 0: 
                        continue
                    
                    p_corners = [FreeCAD.Vector(x[i+ox], y[j+oy], z[k+oz]) for ox, oy, oz in cube_offsets]
                    
                    edge_pts = [None] * 12
                    for e_idx in range(12):
                        if edges & (1 << e_idx):
                            c1, c2 = edge_pairs[e_idx]
                            v1 = v_corners[c1]
                            v2 = v_corners[c2]
                            
                            if abs(v1 - v2) < 1e-5: 
                                edge_pts[e_idx] = p_corners[c1]
                            else:
                                t = (0 - v1) / (v2 - v1)
                                edge_pts[e_idx] = p_corners[c1] + (p_corners[c2] - p_corners[c1]) * t
                            
                    t_edges = triTable[cube_index]
                    idx = 0
                    while idx < len(t_edges) and t_edges[idx] != -1:
                        p1 = edge_pts[t_edges[idx]]
                        # Reverse winding order for correct outward-facing normals because 
                        # negative inside means standard isosurface gradients point outward.
                        p2 = edge_pts[t_edges[idx+2]]
                        p3 = edge_pts[t_edges[idx+1]]
                        
                        if p1 is not None and p2 is not None and p3 is not None:
                            triangles.append((
                                (p1.x, p1.y, p1.z), 
                                (p2.x, p2.y, p2.z), 
                                (p3.x, p3.y, p3.z)
                            ))
                        idx += 3
        
        if not triangles:
            return Part.Shape()
            
        mesh = Mesh.Mesh(triangles)
        # Convert Mesh to a shell of Part faces, then try to make a solid
        shape = Part.Shape()
        shape.makeShapeFromMesh(mesh.Topology, 0.1)
        try:
            solid = Part.makeSolid(shape)
            return solid
        except Exception:
            return shape


class AdaptiveMCMesher(FRepMesher):
    """Octree-accelerated adaptive marching cubes."""
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        dm_logger.warn("AdaptiveMCMesher not fully implemented. Falling back.")
        return MarchingCubesMesher().mesh(field, resolution)

class NurbsFRepMesher(FRepMesher):
    """Direct NURBS meshing."""
    def mesh(self, field: FRepField, resolution: int) -> Part.Shape:
        dm_logger.warn("NurbsFRepMesher not fully implemented. Falling back.")
        return MarchingCubesMesher().mesh(field, resolution)

def get_active_mesher() -> FRepMesher:
    """Factory evaluating the F-Rep Storage Type setting."""
    st = get_frep_storage_type()
    if st == 1:
        return AdaptiveMCMesher()
    elif st == 2:
        return NurbsFRepMesher()
    else:
        return MarchingCubesMesher()
