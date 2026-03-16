"""
Diagnostic: Test GLTexture3D helper (bypasses SoTexture3).

Run in FreeCAD Python console:
    exec(open("/home/steve/Documents/Github/Freecad-Direct-Modeling/tests/diag_gl_texture3d.py").read())
"""
import sys, os
_wb_root = "/home/steve/Documents/Github/Freecad-Direct-Modeling"
if _wb_root not in sys.path:
    sys.path.insert(0, _wb_root)

import numpy as np
import FreeCAD
import FreeCADGui
from pivy import coin
from core.gl_texture3d import GLTexture3D


def run():
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("GLTexDiag")
    FreeCADGui.updateGui()

    # Same test data as before: 2x3x5 volume, texel(x,y,z) = x + y*10 + z*100
    W, H, D = 2, 3, 5
    vol = np.zeros((D, H, W), dtype=np.float32)
    for iz in range(D):
        for iy in range(H):
            for ix in range(W):
                vol[iz, iy, ix] = float(ix + iy * 10 + iz * 100)
    vol_bytes = vol.tobytes()
    print(f"Test volume: {W}x{H}x{D}, {len(vol_bytes)} bytes")

    # Create GLTexture3D helper
    gl_tex = GLTexture3D()
    gl_tex.upload(W, H, D, vol_bytes)

    # Build scene graph
    sg = FreeCADGui.ActiveDocument.ActiveView.getSceneGraph()
    root = coin.SoSeparator()
    root.renderCulling.setValue(coin.SoSeparator.OFF)

    mat = coin.SoMaterial()
    mat.transparency.setValue(0.0)
    root.addChild(mat)

    # Add the GL callback BEFORE the shader — it binds the texture to unit 0
    root.addChild(gl_tex.callback_node)

    # Shader (same diagnostic as before)
    shader = coin.SoShaderProgram()
    vs = coin.SoVertexShader()
    vs.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
    fs = coin.SoFragmentShader()
    fs.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)

    vs.sourceProgram.setValue("""
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy * 0.5 + 0.5;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")

    fs.sourceProgram.setValue("""
#version 330 compatibility
in vec2 v_uv;
uniform sampler3D u_tex;

float decode_texel(ivec3 tc) {
    vec4 c = texelFetch(u_tex, tc, 0);
    uvec4 b = uvec4(round(c * 255.0));
    uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}

void main() {
    ivec3 sz = textureSize(u_tex, 0);
    float v000 = decode_texel(ivec3(0, 0, 0));
    float v100 = decode_texel(ivec3(1, 0, 0));
    float v010 = decode_texel(ivec3(0, 1, 0));
    float v001 = decode_texel(ivec3(0, 0, 1));

    // Top-left: textureSize as color (R=sz.x/10, G=sz.y/10, B=sz.z/10)
    if (v_uv.x < 0.5 && v_uv.y >= 0.5) {
        gl_FragColor = vec4(float(sz.x)/10.0, float(sz.y)/10.0, float(sz.z)/10.0, 1.0);
        return;
    }
    // Top-right: axis decode test — should be WHITE
    if (v_uv.x >= 0.5 && v_uv.y >= 0.5) {
        gl_FragColor = vec4(v100/1.0, v010/10.0, v001/100.0, 1.0);
        return;
    }
    // Bottom-left: texel(0,0,0) — should be BLACK
    if (v_uv.x < 0.5 && v_uv.y < 0.5) {
        gl_FragColor = vec4(vec3(v000/100.0), 1.0);
        return;
    }
    // Bottom-right: raw RGBA of texel(1,0,0)
    if (v_uv.x >= 0.5 && v_uv.y < 0.5) {
        vec4 raw = texelFetch(u_tex, ivec3(1, 0, 0), 0);
        gl_FragColor = vec4(raw.rgb, 1.0);
        return;
    }
    discard;
}
""")

    u_tex = coin.SoShaderParameter1i()
    u_tex.name.setValue("u_tex")
    u_tex.value.setValue(0)
    fs.parameter.set1Value(0, u_tex)

    shader.shaderObject.set1Value(0, vs)
    shader.shaderObject.set1Value(1, fs)
    root.addChild(shader)

    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 4, [(-1,-1,0), (1,-1,0), (1,1,0), (-1,1,0)])
    root.addChild(coords)

    faceset = coin.SoIndexedFaceSet()
    faceset.coordIndex.setValues(0, 8, [0, 1, 2, -1, 0, 2, 3, -1])
    root.addChild(faceset)

    sg.addChild(root)
    FreeCADGui.updateGui()

    import time; time.sleep(0.5)
    shot_path = os.path.join(_wb_root, "tests", "diag_gl_tex_screenshot.png")
    try:
        FreeCADGui.ActiveDocument.ActiveView.saveImage(shot_path, 800, 800, "Current")
        print(f"Screenshot saved: {shot_path}")
    except Exception as e:
        print(f"Screenshot failed: {e}")

    print("""
EXPECTED: Top-right = WHITE (all axes work)
  If WHITE: GLTexture3D works! Ready to integrate.
  If RED:   Only x-axis works (same as SoTexture3 — GL upload also broken)
""")

    global _diag_root, _diag_gl_tex
    _diag_root = root
    _diag_gl_tex = gl_tex


run()
