import math

class Vector:
    def __init__(self, x, y, z):
        self.x = x
        self.y = y
        self.z = z
    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)
    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)
    def __mul__(self, scalar):
        return Vector(self.x * scalar, self.y * scalar, self.z * scalar)
    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z
    def __repr__(self):
        return f"({self.x:.2f}, {self.y:.2f}, {self.z:.2f})"
    def normalize(self):
        l = math.sqrt(self.x*self.x + self.y*self.y + self.z*self.z)
        if l > 0:
            self.x /= l; self.y /= l; self.z /= l
    @property
    def Length(self):
        return math.sqrt(self.x*self.x + self.y*self.y + self.z*self.z)

p2 = Vector(-15.38,43.93,80.22)
ray_p = Vector(122.24, -163.28, 145.82)
ray_d_start = Vector(-0.58, 0.58, -0.58)

view_dir = Vector(-0.58, 0.58, -0.58)  # Assuming view_dir is roughly -ray_d? Wait! 
# In the log: View Dir=(-0.58, 0.58, -0.58). This usually means the View Direction is (-0.58, 0.58, -0.58).
# In my code: vd = self.view.getViewDirection() --> view_dir = Vector(-vd[0], -vd[1], -vd[2])
# If the log printed `ray_d` or `view_dir`? 
# "View Dir=({ray_d.x:.2f}, {ray_d.y:.2f}, {ray_d.z:.2f})" in the code!
# So ray_d = (-0.58, 0.58, -0.58).

# IF ray_d = (-0.58, 0.58, -0.58).
# view_dir in freecad is usually exactly ray_d (for orthogonals and center pixels).
# Wait, view_dir computed as -vd[0]... means view_dir goes from scene to camera.
# FreeCAD vd points INTO the scene. So vd is approximately ray_d!
# So view_dir = -ray_d approx.
view_dir = Vector(0.58, -0.58, 0.58)
view_dir.normalize()
ray_d = Vector(-0.58, 0.58, -0.58)

normal = Vector(-0.2519157589081965, -0.48218657653651936, 0.8390676705854534)

denom = ray_d.dot(view_dir)
print("denom:", denom)
t = (p2 - ray_p).dot(view_dir) / denom
mouse_plane_pt = ray_p + ray_d * t

mouse_vec = mouse_plane_pt - p2

normal_proj = normal - view_dir * normal.dot(view_dir)
proj_len = normal_proj.Length

normal_proj.normalize()
move_dist = mouse_vec.dot(normal_proj)
scale = max(proj_len, 0.1)
t2 = move_dist / scale

print("mouse_vec:", mouse_vec)
print("normal_proj:", normal_proj)
print("move_dist:", move_dist)
print("t2:", t2)

