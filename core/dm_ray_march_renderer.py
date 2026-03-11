"""
core/dm_ray_march_renderer.py

GPU ray marching renderer for F-Rep fields using Coin3D.
Bakes the SDF to a 2D texture atlas and performs sphere tracing in a fragment shader.
"""

import FreeCAD
import pivy.coin as coin
from core.frep.sdf_baker import bake_sdf_to_atlas

_FACE_IDX = [0,1,2,3,-1, 4,7,6,5,-1, 0,4,5,1,-1, 1,5,6,2,-1, 2,6,7,3,-1, 0,3,7,4,-1]

def _bbox_corners(mn, mx):
    x0,y0,z0 = mn.x,mn.y,mn.z;  x1,y1,z1 = mx.x,mx.y,mx.z
    return [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
            (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]

class DMRayMarchRenderer:
    def __init__(self, vobj):
        self.vobj = vobj  # FreeCAD ViewProvider
        self.root = coin.SoSeparator()
        self._u = {}      # Uniform nodes
        self._tex = None
        self._coords = None
        self._setup_nodes()
        vobj.addFrontRoot(self.root)

    def _setup_nodes(self):
        # 1. Texture Atlas
        self._tex = coin.SoTexture2()
        self._tex.model.setValue(coin.SoTexture2.REPLACE) # We use our own shading
        self._tex.wrapS.setValue(coin.SoTexture2.CLAMP_TO_EDGE)
        self._tex.wrapT.setValue(coin.SoTexture2.CLAMP_TO_EDGE)
        self.root.addChild(self._tex)

        # 2. Shader Program
        shader = coin.SoShaderProgram()
        v_shader = coin.SoVertexShader()
        f_shader = coin.SoFragmentShader()
        
        v_shader.sourceProgram.setValue("""
varying vec3 v_world_pos;
void main() {
    v_world_pos = gl_Vertex.xyz;
    gl_Position = ftransform();
}
""")
        
        f_shader.sourceProgram.setValue("""
varying vec3  v_world_pos;
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

float sample_texel(float ix, float iy, float iz) {
    float c = floor(mod(iz, float(u_atz)));
    float r = floor(iz / float(u_atz));
    float u = (c * (float(u_nx) + 1.0) + ix + 0.5) / u_atlas_w;
    float v = (r * (float(u_ny) + 1.0) + iy + 0.5) / u_atlas_h;
    return texture2D(u_sdf_tex, vec2(u, v)).r;
}

float sample_sdf(vec3 p) {
    if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max)))
        return u_max_dist;
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
    float h = u_max_dist * 0.015;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

void main() {
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;
    vec3 ro   = v_world_pos;
    vec3 rd   = normalize(v_world_pos - cam);
    float hit_thresh = u_max_dist * 0.04;

    float t  = 0.0;
    bool hit = false;
    for (int i = 0; i < 128; i++) {
        vec3 p = ro + t * rd;
        if (any(lessThan(p, u_bbox_min)) || any(greaterThan(p, u_bbox_max))) break;
        float d = sample_sdf(p);
        if (d < hit_thresh) { hit = true; break; }
        t += max(d, hit_thresh);
    }
    if (!hit) discard;

    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec3 ld  = normalize(gl_LightSource[0].position.xyz - hp);
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;

    gl_FragColor = vec4(color, 1.0);
    vec4 clip    = gl_ProjectionMatrix * gl_ModelViewMatrix * vec4(hp, 1.0);
    gl_FragDepth = (clip.z / clip.w + 1.0) * 0.5;
}
""")
        # 3. Uniforms
        u_sdf_tex = coin.SoUniformShaderParameter1i()
        u_sdf_tex.name.setValue("u_sdf_tex")
        u_sdf_tex.value.setValue(0)
        
        self._u["u_nx"] = coin.SoUniformShaderParameter1i()
        self._u["u_nx"].name.setValue("u_nx")
        self._u["u_nx"].value.setValue(0)
        
        self._u["u_ny"] = coin.SoUniformShaderParameter1i()
        self._u["u_ny"].name.setValue("u_ny")
        self._u["u_ny"].value.setValue(0)
        
        self._u["u_nz"] = coin.SoUniformShaderParameter1i()
        self._u["u_nz"].name.setValue("u_nz")
        self._u["u_nz"].value.setValue(0)
        
        self._u["u_atz"] = coin.SoUniformShaderParameter1i()
        self._u["u_atz"].name.setValue("u_atz")
        self._u["u_atz"].value.setValue(1)
        
        self._u["u_atlas_w"] = coin.SoUniformShaderParameter1f()
        self._u["u_atlas_w"].name.setValue("u_atlas_w")
        self._u["u_atlas_w"].value.setValue(1.0)
        
        self._u["u_atlas_h"] = coin.SoUniformShaderParameter1f()
        self._u["u_atlas_h"].name.setValue("u_atlas_h")
        self._u["u_atlas_h"].value.setValue(1.0)
        
        self._u["u_bbox_min"] = coin.SoUniformShaderParameter3f()
        self._u["u_bbox_min"].name.setValue("u_bbox_min")
        self._u["u_bbox_min"].value.setValue(coin.SbVec3f(0,0,0))
        
        self._u["u_bbox_max"] = coin.SoUniformShaderParameter3f()
        self._u["u_bbox_max"].name.setValue("u_bbox_max")
        self._u["u_bbox_max"].value.setValue(coin.SbVec3f(0,0,0))
        
        self._u["u_max_dist"] = coin.SoUniformShaderParameter1f()
        self._u["u_max_dist"].name.setValue("u_max_dist")
        self._u["u_max_dist"].value.setValue(1.0)
        
        f_shader.parameter.setNum(0)
        f_shader.parameter.addChild(u_sdf_tex)
        for name in ["u_nx", "u_ny", "u_nz", "u_atz", "u_atlas_w", "u_atlas_h", 
                     "u_bbox_min", "u_bbox_max", "u_max_dist"]:
            f_shader.parameter.addChild(self._u[name])
            
        shader.addChild(v_shader)
        shader.addChild(f_shader)
        self.root.addChild(shader)
        
        # 4. AABB Geometry (Proxy)
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self.root.addChild(hints)
        
        self._coords = coin.SoCoordinate3()
        self.root.addChild(self._coords)
        
        faceset = coin.SoIndexedFaceSet()
        faceset.coordIndex.setValues(0, len(_FACE_IDX), _FACE_IDX)
        self.root.addChild(faceset)

    def update(self, field, cell_size):
        baked = bake_sdf_to_atlas(field, cell_size)
        
        # 1. Texture upload
        self._tex.image.setValue(coin.SbVec2s(baked["atlas_w"], baked["atlas_h"]), 1, baked["atlas_bytes"])
        
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
        
        self._u["u_max_dist"].value.setValue(float(baked["max_dist"]))
        
        # 3. Update AABB corners
        corners = _bbox_corners(mn, mx)
        self._coords.point.setNum(0)
        self._coords.point.setValues(0, 8, corners)
