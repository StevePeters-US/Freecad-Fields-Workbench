import math

class Vector:
    def __init__(self, x=0, y=0, z=0):
        self.x=x; self.y=y; self.z=z
    def dot(self, o): return self.x*o.x + self.y*o.y + self.z*o.z
    def __sub__(self, o): return Vector(self.x-o.x, self.y-o.y, self.z-o.z)
    def __add__(self, o): return Vector(self.x+o.x, self.y+o.y, self.z+o.z)
    def __mul__(self, s): return Vector(self.x*s, self.y*s, self.z*s)
    def cross(self, o): return Vector(self.y*o.z-self.z*o.y, self.z*o.x-self.x*o.z, self.x*o.y-self.y*o.x)
    def normalize(self):
        l = math.sqrt(self.dot(self))
        if l>0:
            self.x/=l; self.y/=l; self.z/=l

def test():
    view_dir = Vector(0.58, -0.58, 0.58)
    view_dir.normalize()
    up = Vector(-0.33, 0.33, 0.66)
    up.normalize()
    right = up.cross(view_dir)
    right.normalize()

    normal = Vector(-0.25, -0.48, 0.83)
    normal.normalize()

    # Project normal onto screen
    screen_x = normal.dot(right)
    screen_y = normal.dot(up)
    
    # In pixels, +Y is DOWN. So UP on screen is -Y pixel movement.
    # If mouse moves DOWN (delta_px_X = -211, delta_px_Y = 374)
    mouse_x = -211
    mouse_y = -374  # standard math coordinates (UP is +Y, so +374 down means -374 in math Y)
    
    # Let's project mouse movement onto screen normal
    drag_dist = screen_x * mouse_x + screen_y * mouse_y
    
    print("Screen Normal (Right, Up):", screen_x, screen_y)
    print("Mouse Delta (Right, Up):", mouse_x, mouse_y)
    print("Drag Distance:", drag_dist)

test()
