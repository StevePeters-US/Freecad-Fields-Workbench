import numpy as np
from FCDirectModeling.sdf_lib import SDFObject

class SDFCone(SDFObject):
    def __init__(self, radius, height):
        super().__init__()
        self.radius = abs(radius) # Base radius
        self.height = height # Total height
        
        # Avoid division by zero
        h_safe = height if abs(height) > 1e-6 else 1e-6
        r_safe = self.radius
        
        self.q = np.array([r_safe/h_safe, -1.0]) 
        
        hyp = np.sqrt(r_safe*r_safe + h_safe*h_safe)
        if hyp < 1e-9: hyp = 1e-9
        
        self.sin_a = r_safe / hyp
        self.cos_a = h_safe / hyp
        
    def _evaluate_local(self, points):
        # Exact SDF for Capped Cone
        # Modifying logic to place Base at Z=0.
        
        # User input: radius (base), height (total).
        # Internal Math expects a cone centered at origin with half-height h.
        # We need to map our domain [0, H] (or [0, -H]) to [-H/2, H/2].
        
        p = points
        h_input = self.height
        h_mag = abs(h_input)
        half_h = h_mag / 2.0
        
        # Shift Z so that Z=0 becomes Z=-half_h.
        # This places the base (at Z=0 in world) to the bottom of the centered cone.
        
        # If height is positive: 0 -> Base, H -> Tip.
        # Shift: p.z - half_h.
        # 0 -> -half_h. (Base)
        # H -> half_h. (Tip)
        
        # If height is negative: 0 -> Base, -H -> Tip.
        # We want to flip the cone upside down relative to the shifting.
        # Or just handle the shift and flip.
        
        # Standardize p.z
        z_shifted = p[:, 2] - half_h # Shift for positive case
        
        if h_input < 0:
            # If height is negative, we want 0 -> Base, -H -> Tip.
            # Local mathematical cone is defined -half to +half.
            # We want 0 -> -half.
            # -|H| -> +half.
            # Let's see: 
            # 0 mapped to -half.
            # -10 mapped to +half.
            # Formula: p_new = -(p.z + half_h) ?
            # -(0 + 5) = -5. Correct.
            # -(-10 + 5) = -(-5) = 5. Correct.
            z_shifted = -(p[:, 2] + half_h)

        # Reconstruct P with shifted Z
        # Note: X, Y remain same.
        # We can construct 'q' directly using z_shifted
        
        # 2D Profile:
        # x = horizontal dist, y = vertical dist (z)
        q = np.stack([np.linalg.norm(p[:, :2], axis=1), z_shifted], axis=1)
        
        # Parameters for centered cone
        h = half_h
        r1 = self.radius
        r2 = 0.0 # Tip
        
        # --- Inigo Quilez sdCappedCone (Centered) ---
        
        q_x = q[:, 0]
        q_y = q[:, 1]
        
        k1 = np.array([r2, h]) # (0, h)
        k2 = np.array([r2 - r1, 2.0 * h]) # (-r1, 2h)
        
        # ca calculation
        # min(q.x, (q.y < 0.0) ? r1 : r2)
        limit = np.where(q_y < 0.0, r1, r2)
        min_term = np.minimum(q_x, limit)
        ca = np.stack([q_x - min_term, np.abs(q_y) - h], axis=1)
        
        # cb calculation
        k1_min_q = k1 - q
        k2_dot_k2 = np.dot(k2, k2)
        dot_numerator = np.dot(k1_min_q, k2)
        f = np.clip(dot_numerator / k2_dot_k2, 0.0, 1.0)
        
        term_k2_f = np.outer(f, k2)
        cb = q - k1 + term_k2_f
        
        s = np.where((cb[:, 0] < 0.0) & (ca[:, 1] < 0.0), -1.0, 1.0)
        
        dot_ca = np.sum(ca * ca, axis=1)
        dot_cb = np.sum(cb * cb, axis=1)
        
        return s * np.sqrt(np.minimum(dot_ca, dot_cb))

    def _bounds_local(self):
        r = self.radius
        h = self.height
        # Range is [0, h] if h>0, or [h, 0] if h<0
        z_min = min(0, h)
        z_max = max(0, h)
        return (np.array([-r, -r, z_min]), np.array([r, r, z_max]))

    def get_vertices(self):
        # A cone has exactly 1 vertex (the tip).
        # We need to account for height being positive vs negative.
        return np.array([[0.0, 0.0, self.height]])

    def get_edges(self):
        # A cone has its circular base at z = 0, and we add 4 profile lines to the tip
        edges = []
        
        # Base circle
        theta = np.linspace(0, 2 * np.pi, 64)
        x = self.radius * np.cos(theta)
        y = self.radius * np.sin(theta)
        z = np.zeros_like(x)
        edges.append(np.column_stack([x, y, z]))
        
        # 4 vertical profile lines from base to tip
        tip_z = self.height
        for angle in [0, np.pi/2, np.pi, 3*np.pi/2]:
            base_x = self.radius * np.cos(angle)
            base_y = self.radius * np.sin(angle)
            
            # Line from base to tip
            line_x = np.array([base_x, 0.0])
            line_y = np.array([base_y, 0.0])
            line_z = np.array([0.0, tip_z])
            edges.append(np.column_stack([line_x, line_y, line_z]))
            
        return edges
