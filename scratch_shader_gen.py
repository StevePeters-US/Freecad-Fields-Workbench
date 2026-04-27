class FakeVec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z
    def __add__(self, o): return FakeVec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return FakeVec(self.x-o.x, self.y-o.y, self.z-o.z)

import sys
sys.modules['FreeCAD'] = type('MockFC', (), {'Vector': FakeVec, 'Placement': lambda: None})()

from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.glsl_compiler import compile_field_to_glsl, build_multi_raymarch_fragment_shader

s = SdfSphereField(FakeVec(0,0,0), 10.0)
bmin, bmax = s.bounding_box()

expr, ctx = compile_field_to_glsl(s)

data = [{
    "expr": expr,
    "ctx": ctx,
    "bbox_min": bmin,
    "bbox_max": bmax,
    "is_subtractive": False
}]

source = build_multi_raymarch_fragment_shader(data)
print(source)
