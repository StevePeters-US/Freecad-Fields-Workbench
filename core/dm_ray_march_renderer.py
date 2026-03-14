"""
core/dm_ray_march_renderer.py

GPU ray marching renderer for F-Rep fields using Coin3D.
Bakes the SDF to a 2D texture atlas and performs sphere tracing in a fragment shader.
"""

import FreeCAD
import pivy.coin as coin
from core.frep.sdf_baker import bake_sdf_to_atlas



class DMRayMarchRenderer:
    def __init__(self, vobj):
        self.vobj = vobj  # FreeCAD ViewProvider
        self.root = coin.SoSeparator()
        self._switch = coin.SoSwitch()
        self._switch.addChild(self.root)
        self._switch.whichChild = 0 if vobj.Visibility else -1  # respect initial visibility
        self._u = {}      # Uniform nodes
        self._tex = None
        self._coords = None
        self._setup_nodes()
        vobj.RootNode.addChild(self._switch)

    def _setup_nodes(self):
        # 1. Bounding box proxy (added to root FIRST for correct near/far clipping)
        #    Must be OUTSIDE the shader separator so it doesn't inherit the ray march shader.
        self._bbox_sep = coin.SoSeparator()
        
        # Invisible draw style (reliably hides lines while keeping bbox contribution)
        draw = coin.SoDrawStyle()
        draw.style.setValue(coin.SoDrawStyle.INVISIBLE)
        self._bbox_sep.addChild(draw)
        
        # Prevent picking
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self._bbox_sep.addChild(pick)
        
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        
        # Ensure we have actual bounded edges (12 edges of a box = 36 indices)
        bbox_lines = coin.SoIndexedLineSet()
        bbox_lines.coordIndex.setValues(0, 36, [
            0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
            4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1
        ])
        self._bbox_sep.addChild(bbox_lines)
        
        self.root.addChild(self._bbox_sep)

        # 2. Shader-scoped separator (isolates shader from bbox proxy)
        self._shader_sep = coin.SoSeparator()

        # Force opaque classification — without this, the quad inherits the parent
        # ViewProvider's material, which may have transparency > 0, causing Coin3D
        # to render the quad in the transparent pass with depth writes DISABLED.
        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        # Explicit depth buffer control (belt-and-suspenders with opaque material)
        try:
            depth_buf = coin.SoDepthBuffer()
            depth_buf.test.setValue(True)   # GL_DEPTH_TEST enabled
            depth_buf.write.setValue(True)  # glDepthMask(GL_TRUE)
            self._shader_sep.addChild(depth_buf)
        except AttributeError:
            pass  # SoDepthBuffer not available in this Coin3D/pivy version

        # 2a. Force GL_NEAREST filtering (shader does its own trilinear;
        #     hardware bilinear bleeds across atlas tile boundaries)
        complexity = coin.SoComplexity()
        complexity.textureQuality.setValue(0.0)   # GL_NEAREST / GL_NEAREST
        self._shader_sep.addChild(complexity)

        # 2b. Texture Atlas
        self._tex = coin.SoTexture2()
        self._tex.model.setValue(coin.SoTexture2.REPLACE) # We use our own shading
        self._tex.wrapS.setValue(coin.SoTexture2.CLAMP)
        self._tex.wrapT.setValue(coin.SoTexture2.CLAMP)
        self._shader_sep.addChild(self._tex)

        # 2c. Shader Program
        shader = coin.SoShaderProgram()
        v_shader = coin.SoVertexShader()
        v_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
        f_shader = coin.SoFragmentShader()
        f_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
        
        v_shader.sourceProgram.setValue("""
varying vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")
        
        f_shader.sourceProgram.setValue("""
varying vec2  v_uv;
uniform sampler2D u_sdf_tex;
uniform int   u_nx;
uniform int   u_ny;
uniform int   u_nz;
uniform int   u_atz;
uniform float u_atlas_w;
uniform float u_atlas_h;
uniform vec3  u_bbox_min;
uniform vec3  u_bbox_max;
uniform float u_max_dist;
uniform int   u_debug_mode;

float sample_texel(float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz)));
    float r = floor(iz / float(u_atz));
    float u = (c * (float(u_nx) + 1.0) + ix + 0.5) / u_atlas_w;
    float v = (r * (float(u_ny) + 1.0) + iy + 0.5) / u_atlas_h;
    vec4 t = texture2D(u_sdf_tex, vec2(u, v));
    // Mathematically exact uint16 reconstruction for OpenGL
    return (t.r * 65280.0 + t.a * 255.0) / 65535.0;
}

float sample_sdf(vec3 p) {
    vec3 uvw = (p - u_bbox_min) / (u_bbox_max - u_bbox_min);
    float gx = clamp(uvw.x * float(u_nx), 0.0, float(u_nx));
    float gy = clamp(uvw.y * float(u_ny), 0.0, float(u_ny));
    float gz = clamp(uvw.z * float(u_nz), 0.0, float(u_nz));
    float x0=floor(gx); float x1=min(x0+1.0,float(u_nx));
    float y0=floor(gy); float y1=min(y0+1.0,float(u_ny));
    float z0=floor(gz); float z1=min(z0+1.0,float(u_nz));
    float fx=gx-x0; float fy=gy-y0; float fz=gz-z0;
    float s = mix(
        mix(mix(sample_texel(x0,y0,z0),sample_texel(x1,y0,z0),fx),
            mix(sample_texel(x0,y1,z0),sample_texel(x1,y1,z0),fx),fy),
        mix(mix(sample_texel(x0,y0,z1),sample_texel(x1,y0,z1),fx),
            mix(sample_texel(x0,y1,z1),sample_texel(x1,y1,z1),fx),fy),
        fz);
    return (s * 2.0 - 1.0) * u_max_dist;
}

vec3 sdf_normal(vec3 p) {
    // Scale epsilon with voxel size for stable normals
    float cell = (u_bbox_max.x - u_bbox_min.x) / max(float(u_nx), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

// AABB-ray intersection: returns (tNear, tFar).
vec2 intersect_aabb(vec3 ro, vec3 rd) {
    vec3 t1 = (u_bbox_min - ro) / rd;
    vec3 t2 = (u_bbox_max - ro) / rd;
    vec3 tmin = min(t1, t2);
    vec3 tmax = max(t1, t2);
    float tNear = max(max(tmin.x, tmin.y), tmin.z);
    float tFar  = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tNear, tFar);
}

void main() {
    // 1. Unproject NDC to world-space ray
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;

    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;

    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    // 2. AABB-ray intersection — skip rays that miss the bounding box
    vec2 tBox = intersect_aabb(ro, rd);
    float tNear = max(tBox.x, 0.0);  // clamp to ray origin
    float tFar  = tBox.y;
    if (tNear > tFar) discard;        // ray misses box entirely

    // 3. Sphere-trace from tNear to tFar
    float hit_thresh = u_max_dist * 0.001;
    float min_step   = hit_thresh;
    float t = tNear;
    bool hit = false;
    float d;
    int march_iters = 0;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;
        d = sample_sdf(p);
        if (abs(d) < hit_thresh) { hit = true; break; }
        t += max(abs(d), min_step);
        if (t > tFar) break;
    }
    if (!hit) discard;



    // 5. Shading — transform light from eye space to world space
    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);

    // gl_LightSource[0].position is in EYE space (OpenGL convention).
    // For directional lights (w==0), .xyz is the light direction in eye space.
    // For positional lights (w==1), .xyz is the position in eye space.
    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        // Directional light: transform direction to world space
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        // Positional light: transform position to world space, then direction
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }

    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);

    // Debug colour overrides
    if (u_debug_mode == 1) {
        float v = sample_sdf(hp) / u_max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    // 6. Depth write — use gl_DepthRange for Coin3D compatibility
    vec4 clip     = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z   = clip.z / clip.w;
    gl_FragDepth  = gl_DepthRange.near
                  + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")
        # 2d. Uniforms
        u_sdf_tex = coin.SoShaderParameter1i()
        u_sdf_tex.name.setValue("u_sdf_tex")
        u_sdf_tex.value.setValue(0)
        
        self._u["u_nx"] = coin.SoShaderParameter1i()
        self._u["u_nx"].name.setValue("u_nx")
        self._u["u_nx"].value.setValue(0)
        
        self._u["u_ny"] = coin.SoShaderParameter1i()
        self._u["u_ny"].name.setValue("u_ny")
        self._u["u_ny"].value.setValue(0)
        
        self._u["u_nz"] = coin.SoShaderParameter1i()
        self._u["u_nz"].name.setValue("u_nz")
        self._u["u_nz"].value.setValue(0)
        
        self._u["u_atz"] = coin.SoShaderParameter1i()
        self._u["u_atz"].name.setValue("u_atz")
        self._u["u_atz"].value.setValue(1)
        
        self._u["u_atlas_w"] = coin.SoShaderParameter1f()
        self._u["u_atlas_w"].name.setValue("u_atlas_w")
        self._u["u_atlas_w"].value.setValue(1.0)
        
        self._u["u_atlas_h"] = coin.SoShaderParameter1f()
        self._u["u_atlas_h"].name.setValue("u_atlas_h")
        self._u["u_atlas_h"].value.setValue(1.0)
        
        self._u["u_bbox_min"] = coin.SoShaderParameter3f()
        self._u["u_bbox_min"].name.setValue("u_bbox_min")
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(0,0,0))
        
        self._u["u_bbox_max"] = coin.SoShaderParameter3f()
        self._u["u_bbox_max"].name.setValue("u_bbox_max")
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(0,0,0))
        
        self._u["u_max_dist"] = coin.SoShaderParameter1f()
        self._u["u_max_dist"].name.setValue("u_max_dist")
        self._u["u_max_dist"].value.setValue(1.0)
        
        f_shader.parameter.setNum(0)
        f_shader.parameter.set1Value(0, u_sdf_tex)
        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        for i, name in enumerate(["u_nx", "u_ny", "u_nz", "u_atz", "u_atlas_w", "u_atlas_h", 
                                  "u_bbox_min", "u_bbox_max", "u_max_dist", "u_debug_mode"]):
            f_shader.parameter.set1Value(i + 1, self._u[name])
            
        shader.shaderObject.set1Value(0, v_shader)
        shader.shaderObject.set1Value(1, f_shader)
        self._shader_sep.addChild(shader)
        
        # 2e. Quad Geometry + AABB expansion points
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self._shader_sep.addChild(hints)
        
        self._coords = coin.SoCoordinate3()
        # Points 0-7: AABB corners (updated in update())
        # Points 8-11: NDC quad [-1, 1]
        self._coords.point.setValues(8, 4, [
            (-1, -1, 0), ( 1, -1, 0), ( 1,  1, 0), (-1,  1, 0)
        ])
        self._shader_sep.addChild(self._coords)

        # Invisible point set using points 0-7 to expand the separator's bbox
        bbox_style = coin.SoDrawStyle()
        bbox_style.style.setValue(coin.SoDrawStyle.INVISIBLE)
        self._shader_sep.addChild(bbox_style)
        
        self._bbox_expansion = coin.SoPointSet()
        self._bbox_expansion.numPoints.setValue(8) # First 8 points
        self._shader_sep.addChild(self._bbox_expansion)
        
        # Reset draw style for the quad
        quad_style = coin.SoDrawStyle()
        quad_style.style.setValue(coin.SoDrawStyle.FILLED)
        self._shader_sep.addChild(quad_style)

        faceset = coin.SoIndexedFaceSet()
        # Use indices 8-11 for the quad triangles, plus 8 degenerate triangles 
        # (one for each corner 0-7) to force the shape's bbox to exactly include
        # the entire scene bounding box. This prevents Coin3D from culling the quad
        # when zooming into a part of the SDF while other parts (or the origin) 
        # are off-screen.
        indices = [8, 9, 10, -1, 8, 10, 11, -1]
        for i in range(8):
            indices.extend([i, i, i, -1])
        faceset.coordIndex.setValues(0, len(indices), indices)
        self._shader_sep.addChild(faceset)

        self.root.addChild(self._shader_sep)

    def update(self, field, cell_size):
        baked = bake_sdf_to_atlas(field, cell_size)
        
        # 1. Texture upload (LUMINANCE_ALPHA, 2 channels)
        self._tex.image.setValue(coin.SbVec2s(baked["atlas_w"], baked["atlas_h"]), 2, baked["atlas_bytes"])
        
        # 2. Update uniforms
        self._u["u_nx"].value.setValue(int(baked["nx"]))
        self._u["u_ny"].value.setValue(int(baked["ny"]))
        self._u["u_nz"].value.setValue(int(baked["nz"]))
        self._u["u_atz"].value.setValue(int(baked["atz"]))
        self._u["u_atlas_w"].value.setValue(float(baked["atlas_w"]))
        self._u["u_atlas_h"].value.setValue(float(baked["atlas_h"]))
        
        mn, mx = baked["bbox_min"], baked["bbox_max"]
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(mn.x, mn.y, mn.z))
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(mx.x, mx.y, mx.z))
        
        self._bbox_coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])
        
        self._u["u_max_dist"].value.setValue(float(baked["max_dist"]))
        
        # 3. Update expansion points in shader-sep to prevent culling
        self._coords.point.setValues(0, 8, [
            (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
            (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
            (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
            (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z)
        ])

    def set_visible(self, visible):
        """Toggle visibility of the ray march render."""
        if hasattr(self, "_switch"):
            self._switch.whichChild = 0 if visible else -1

    def set_debug_mode(self, mode):
        """Set debug colour mode: 0=normal, 1=SDF heat-map, 2=normals, 3=iterations."""
        self._u["u_debug_mode"].value.setValue(int(mode))
