import numpy as np
import FreeCAD
import math
from core.frep.sdf.sdf_field import SdfField

class SdfCylinderField(SdfField):
    """A finite cylinder exact SDF."""
    def __init__(self, base_center: FreeCAD.Vector, axis: FreeCAD.Vector, radius: float, height: float, placement: FreeCAD.Placement = None):
        self.base_center = base_center
        self.axis = axis
        self.axis.normalize()
        self.radius = radius
        self.height = height
        self.placement = placement
        
        self.inv_matrix = None
        if self.placement is not None:
             m = self.placement.toMatrix()
             m.invert()
             self.inv_matrix = np.array([
                 [m.A11, m.A12, m.A13, m.A14],
                 [m.A21, m.A22, m.A23, m.A24],
                 [m.A31, m.A32, m.A33, m.A34],
                 [m.A41, m.A42, m.A43, m.A44]
             ], dtype=np.float32)

    def evaluate(self, point: FreeCAD.Vector) -> float:
        local_pt = point
        if self.placement is not None:
            local_pt = self.placement.inverse().multVec(point)
            
        pa = local_pt - self.base_center
        h = pa.dot(self.axis)
        radial_vec = pa - self.axis * h
        d_radial = radial_vec.Length - self.radius
        h_center = h - (self.height / 2.0)
        d_axial = abs(h_center) - (abs(self.height) / 2.0)
        out_dist = FreeCAD.Vector(max(d_radial, 0.0), max(d_axial, 0.0), 0).Length
        in_dist = min(max(d_radial, d_axial), 0.0)
        return out_dist + in_dist

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        """Vectorized cylinder SDF over (N, 3) points."""
        if self.inv_matrix is not None:
            N = points.shape[0]
            pts_hom = np.hstack((points, np.ones((N, 1), dtype=np.float32)))
            local_pts = (pts_hom @ self.inv_matrix.T)[:, :3]
        else:
            local_pts = points

        c = np.array([self.base_center.x, self.base_center.y, self.base_center.z])
        ax = np.array([self.axis.x, self.axis.y, self.axis.z])

        pa = local_pts - c                          # (N, 3) vectors from base center
        h = pa @ ax                                 # (N,) projection along axis
        radial = pa - np.outer(h, ax)              # (N, 3) radial component
        d_radial = np.linalg.norm(radial, axis=1) - self.radius  # (N,)

        h_center = h - self.height / 2.0
        d_axial = np.abs(h_center) - np.abs(self.height) / 2.0            # (N,)

        d_r_pos = np.maximum(d_radial, 0.0)
        d_a_pos = np.maximum(d_axial, 0.0)
        out_dist = np.sqrt(d_r_pos**2 + d_a_pos**2)
        in_dist = np.minimum(np.maximum(d_radial, d_axial), 0.0)
        return (out_dist + in_dist).astype(np.float32)

    def bounding_box(self):
        center_local = self.base_center + self.axis * (self.height / 2.0)
        R = math.sqrt(self.radius**2 + (self.height / 2.0)**2)
        
        # Rotated bounding box can be significantly larger if just taking min/max of local corners
        c = center_local
        r = self.radius
        h = self.height / 2.0
        
        # A cylinder's bounding box can be approximated by its oriented bounding box's corners
        # but for simplicity and safety, we can use a sphere that contains the cylinder,
        # or calculate the 8 corners of the cylinder's bounding box and transform them.
        # Let's do the latter for accuracy.
        
        # Cylinder axis-aligned local corners (assuming axis is the orientation axis)
        # This is tricky because self.axis might not be (0,0,1) in local space.
        # However, for primitives created via the tool, self.axis IS usually (0,0,1) in local space.
        
        if self.placement is not None:
            # Sphere fallback for speed/simplicity in non-axial cylinders or just transform 8 corners of the AABB
            # Let's use the sphere fallback for now as it's conservative
            center_global = self.placement.multVec(center_local)
            r_vec = FreeCAD.Vector(R, R, R)
            return (center_global - r_vec, center_global + r_vec)

        r_vec = FreeCAD.Vector(R, R, R)
        return (center_local - r_vec, center_local + r_vec)
