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
ray_d = Vector(-0.58, 0.58, -0.58)

view_dir = Vector(-0.58, 0.58, -0.58) # IF view_dir is WITH ray

normal = Vector(-0.2519, -0.4821, 0.8390)

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
print("t2:", t2)

