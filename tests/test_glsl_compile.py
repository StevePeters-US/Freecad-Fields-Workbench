import test_imports
import sys
import types
pivy = types.ModuleType('pivy')
pivy.coin = types.ModuleType('coin')
pivy.coin.SoCallback = lambda: None
pivy.coin.SoGLRenderAction = lambda: None
sys.modules['pivy'] = pivy
sys.modules['pivy.coin'] = pivy.coin

import FreeCAD
from core.sdf.sdf.sphere import SdfSphereField
from core.sdf.glsl_compiler import compile_field_to_glsl, build_multi_raymarch_fragment_shader

s = SdfSphereField(FreeCAD.Vector(0,0,0), 10.0)
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

# Now try to compile it using GLProgram! But wait, GLProgram requires an active GL context!
# We can't actually compile it here.
# But we CAN print the source to see if it's syntactically valid or obviously broken.
print("--- SHADER SOURCE ---")
print(source)
