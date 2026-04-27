class FakeVec:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

import sys
sys.modules['FreeCAD'] = type('MockFC', (), {'Vector': FakeVec})()

from core.sdf.sdf.sphere import SphereField
from core.sdf.glsl_compiler import build_multi_raymarch_fragment_shader

s = SphereField(FakeVec(0,0,0), 10.0)
bmin, bmax = s.bounding_box()

from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer
ren = DMSceneRayMarchRenderer()
expr, ctx = ren._try_gpu_compile(s)

data = [{
    "expr": expr,
    "ctx": ctx,
    "bbox_min": bmin,
    "bbox_max": bmax,
    "is_subtractive": False
}]

source = build_multi_raymarch_fragment_shader(data)
print("--- SHADER SOURCE ---")
print(source)
