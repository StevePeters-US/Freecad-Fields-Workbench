class FakeVec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z
    def __add__(self, o): return FakeVec(self.x+o.x, self.y+o.y, self.z+o.z)
    def __sub__(self, o): return FakeVec(self.x-o.x, self.y-o.y, self.z-o.z)
    @property
    def Length(self): return (self.x**2 + self.y**2 + self.z**2)**0.5
    def __mul__(self, o): return FakeVec(self.x*o, self.y*o, self.z*o)

class FakePlacement:
    def toMatrix(self):
        class M:
            A11=1; A12=0; A13=0; A14=0
            A21=0; A22=1; A23=0; A24=0
            A31=0; A32=0; A33=1; A34=0
            A41=0; A42=0; A43=0; A44=1
            def invert(self): pass
        return M()
    def multVec(self, v): return v

import sys
sys.modules['FreeCAD'] = type('MockFC', (), {'Vector': FakeVec, 'Placement': FakePlacement})()

from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.glsl_compiler import compile_field_to_glsl, build_multi_raymarch_fragment_shader

s = SdfSphereField(FakeVec(0,0,0), 10.0, placement=FakePlacement())
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
