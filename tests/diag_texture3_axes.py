"""
Diagnostic: Determine the actual axis ordering of Coin3D SoTexture3.

Creates a tiny 3D texture with known float32 values at specific texel positions,
then renders with a shader that outputs raw texel values as color.

Run in FreeCAD Python console:
    exec(open("/home/steve/Documents/Github/Freecad-Direct-Modeling/tests/diag_texture3_axes.py").read())
"""
import sys, os, struct
_wb_root = "/home/steve/Documents/Github/Freecad-Direct-Modeling"
if _wb_root not in sys.path:
    sys.path.insert(0, _wb_root)

import numpy as np
import FreeCAD
import FreeCADGui
from pivy import coin


def run():
    # --- Step 1: Check SbVec3s constructor ordering ---
    v = coin.SbVec3s(11, 22, 33)
    # Pivy SbVec3s doesn't support direct subscript; use getX/getY/getZ or unpack
    try:
        print(f"SbVec3s(11,22,33) = ({v[0]}, {v[1]}, {v[2]})")
    except Exception:
        pass
    # Also try the SoTexture3 images field to see what size it reports back
    test_tex = coin.SoTexture3()
    tiny = np.zeros((1, 1, 1), dtype=np.float32).tobytes()
    test_tex.images.setValue(coin.SbVec3s(7, 13, 19), 4, tiny)
    # Read back the size from the images field
    sz = coin.SbVec3s()
    nc = coin.SbColor()  # dummy for num components
    # Can't easily read back, just proceed with the visual test
    print("SbVec3s test: uploaded SbVec3s(7,13,19) — visual test will confirm axis order")

    # --- Step 2: Check textureSize() axis order ---
    # Create a tiny asymmetric 3D texture: 2x3x4 texels (width x height x depth)
    # Fill with a known gradient: texel(x,y,z) stores float32 = x + y*10 + z*100
    W, H, D = 2, 3, 5  # Deliberately asymmetric
    vol = np.zeros((D, H, W), dtype=np.float32)  # C-order: W varies fastest
    for iz in range(D):
        for iy in range(H):
            for ix in range(W):
                vol[iz, iy, ix] = float(ix + iy * 10 + iz * 100)
    vol_bytes = vol.tobytes()
    print(f"\nCreated test volume: W={W} H={H} D={D}")
    print(f"  vol[0,0,0] = {vol[0,0,0]} (expect 0)")
    print(f"  vol[0,0,1] = {vol[0,0,1]} (expect 1)")
    print(f"  vol[0,1,0] = {vol[0,1,0]} (expect 10)")
    print(f"  vol[1,0,0] = {vol[1,0,0]} (expect 100)")
    print(f"  vol[D-1,H-1,W-1] = {vol[D-1,H-1,W-1]} (expect {(W-1) + (H-1)*10 + (D-1)*100})")
    print(f"  Total bytes: {len(vol_bytes)} (expect {W*H*D*4})")

    # --- Step 3: Upload texture and render with diagnostic shader ---
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("TexDiag")
    FreeCADGui.updateGui()
    sg = FreeCADGui.ActiveDocument.ActiveView.getSceneGraph()
    root = coin.SoSeparator()
    root.renderCulling.setValue(coin.SoSeparator.OFF)

    # Opaque material
    mat = coin.SoMaterial()
    mat.transparency.setValue(0.0)
    root.addChild(mat)

    # 3D Texture — upload as SbVec3s(W, H, D)
    tex = coin.SoTexture3()
    tex.wrapR.setValue(coin.SoTexture3.CLAMP)
    tex.wrapS.setValue(coin.SoTexture3.CLAMP)
    tex.wrapT.setValue(coin.SoTexture3.CLAMP)
    tex.images.setValue(coin.SbVec3s(W, H, D), 4, vol_bytes)
    root.addChild(tex)

    # Shader that probes specific texels and outputs diagnostic info
    shader = coin.SoShaderProgram()
    vs = coin.SoVertexShader()
    vs.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
    fs = coin.SoFragmentShader()
    fs.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)

    vs.sourceProgram.setValue("""
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy * 0.5 + 0.5;  // Map [-1,1] to [0,1]
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")

    # The fragment shader probes specific texels and outputs their decoded float32
    # values as color. Different screen quadrants show different test results.
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
    // Get actual texture size from textureSize()
    ivec3 sz = textureSize(u_tex, 0);

    // Probe specific texels:
    // texel(0,0,0) should decode to 0.0
    // texel(1,0,0) should decode to 1.0  (x+1)
    // texel(0,1,0) should decode to 10.0 (y*10)
    // texel(0,0,1) should decode to 100.0 (z*100)

    float v000 = decode_texel(ivec3(0, 0, 0));
    float v100 = decode_texel(ivec3(1, 0, 0));
    float v010 = decode_texel(ivec3(0, 1, 0));
    float v001 = decode_texel(ivec3(0, 0, 1));

    // Top-left quadrant: textureSize result as color
    // R = sz.x/10, G = sz.y/10, B = sz.z/10
    if (v_uv.x < 0.5 && v_uv.y >= 0.5) {
        gl_FragColor = vec4(float(sz.x) / 10.0, float(sz.y) / 10.0, float(sz.z) / 10.0, 1.0);
        return;
    }

    // Top-right quadrant: texel(0,0,0) value (should be 0 = black)
    // and texel(1,0,0) (should be 1.0 = R channel = bright red)
    if (v_uv.x >= 0.5 && v_uv.y >= 0.5) {
        float norm100 = v100 / 1.0;   // Should be 1.0 if x-axis is correct
        float norm010 = v010 / 10.0;  // Should be 1.0 if y-axis is correct
        float norm001 = v001 / 100.0; // Should be 1.0 if z-axis is correct
        gl_FragColor = vec4(norm100, norm010, norm001, 1.0);
        return;
    }

    // Bottom-left: v000 as gray (should be 0 = black)
    if (v_uv.x < 0.5 && v_uv.y < 0.5) {
        gl_FragColor = vec4(vec3(v000 / 100.0), 1.0);
        return;
    }

    // Bottom-right: raw RGBA bytes of texel(1,0,0) as color
    // This shows whether float32 packing survived the upload
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

    # Fullscreen quad
    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 4, [(-1,-1,0), (1,-1,0), (1,1,0), (-1,1,0)])
    root.addChild(coords)

    faceset = coin.SoIndexedFaceSet()
    faceset.coordIndex.setValues(0, 8, [0, 1, 2, -1, 0, 2, 3, -1])
    root.addChild(faceset)

    sg.addChild(root)

    # Take screenshot
    FreeCADGui.updateGui()
    import time; time.sleep(0.5)

    shot_path = os.path.join(_wb_root, "tests", "diag_axes_screenshot.png")
    try:
        FreeCADGui.ActiveDocument.ActiveView.saveImage(shot_path, 800, 800, "Current")
        print(f"\nScreenshot saved: {shot_path}")
    except Exception as e:
        print(f"Screenshot failed: {e}")

    print(f"""
=== EXPECTED RESULTS ===
textureSize(u_tex, 0) should return ivec3({W}, {H}, {D})

Top-left quadrant (textureSize):
  If correct: R={W/10:.1f} G={H/10:.1f} B={D/10:.1f} → dark reddish
  If S/R swapped: R={D/10:.1f} G={H/10:.1f} B={W/10:.1f}

Top-right quadrant (axis decode test):
  If correct: R=1.0 G=1.0 B=1.0 → WHITE
  Each channel tests one axis:
    R = texel(1,0,0)/1   → tests x-axis (should be 1.0)
    G = texel(0,1,0)/10  → tests y-axis (should be 1.0)
    B = texel(0,0,1)/100 → tests z-axis (should be 1.0)
  If any axis is swapped, that channel will be wrong

Bottom-left: texel(0,0,0) should be 0.0 → BLACK

Bottom-right: raw RGBA of texel(1,0,0)
  float32(1.0) = 0x3F800000 → bytes: 00, 00, 80, 3F
  So raw RGBA should be: R=0, G=0, B≈0.5, A≈0.25
""")

    # Store root ref so it doesn't get GC'd
    global _diag_root
    _diag_root = root


run()
