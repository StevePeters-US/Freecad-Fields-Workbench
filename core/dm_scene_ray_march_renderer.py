"""
core/dm_scene_ray_march_renderer.py

Scene-level GPU ray march renderer. One full-screen quad renders ALL F-Rep
fields combined via a single baked 3D texture atlas. Generic — works with any
FRepField subclass via evaluate_grid(). No per-primitive GLSL formulas.
"""
import FreeCAD
import FreeCADGui
import pivy.coin as coin
from core import dm_logger
from core.frep.sdf_baker import bake_sdf_to_atlas
from core.frep.frep_composer import UnionField


class DMSceneRayMarchRenderer:
    """Singleton scene-level ray march renderer."""
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def destroy(cls):
        if cls._instance is not None:
            cls._instance._detach()
            cls._instance = None

    def __init__(self):
        self._fields = {}          # label -> (field, visible)
        self._attached = False
        self._u = {}               # uniform nodes
        self._tex = None
        self._bbox_coords = None
        self._root = coin.SoSeparator()
        self._switch = coin.SoSwitch()
        self._switch.addChild(self._root)
        self._switch.whichChild = -1
        self._setup_nodes()

    def _attach(self):
        if self._attached:
            return
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.addChild(self._switch)
            self._attached = True
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: attach failed: {e}")

    def _detach(self):
        if not self._attached:
            return
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.removeChild(self._switch)
        except Exception:
            pass
        self._attached = False

    def _setup_nodes(self):
        # 1. Bounding box proxy (outside shader sep for correct near/far clipping)
        self._bbox_sep = coin.SoSeparator()
        mat = coin.SoMaterial()
        mat.transparency.setValue(1.0)
        self._bbox_sep.addChild(mat)
        pick = coin.SoPickStyle()
        pick.style.setValue(coin.SoPickStyle.UNPICKABLE)
        self._bbox_sep.addChild(pick)
        self._bbox_coords = coin.SoCoordinate3()
        self._bbox_sep.addChild(self._bbox_coords)
        bbox_lines = coin.SoIndexedLineSet()
        bbox_lines.coordIndex.setValues(0, 36, [
            0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
            4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
            0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1
        ])
        self._bbox_sep.addChild(bbox_lines)
        self._root.addChild(self._bbox_sep)

        # 2. Shader-scoped separator (isolates shader from bbox proxy)
        self._shader_sep = coin.SoSeparator()

        # Force opaque classification for correct depth writes
        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        # Explicit depth buffer control
        try:
            depth_buf = coin.SoDepthBuffer()
            depth_buf.test.setValue(True)
            depth_buf.write.setValue(True)
            self._shader_sep.addChild(depth_buf)
        except AttributeError:
            pass

        # GL_NEAREST filtering (shader does its own trilinear)
        complexity = coin.SoComplexity()
        complexity.textureQuality.setValue(0.0)
        self._shader_sep.addChild(complexity)

        # Texture Atlas
        self._tex = coin.SoTexture2()
        self._tex.model.setValue(coin.SoTexture2.REPLACE)
        self._tex.wrapS.setValue(coin.SoTexture2.CLAMP)
        self._tex.wrapT.setValue(coin.SoTexture2.CLAMP)
        self._shader_sep.addChild(self._tex)

        # Shader Program — identical vertex+fragment shader to DMRayMarchRenderer
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

        # Fragment shader: identical to per-object DMRayMarchRenderer
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
    float cell = (u_bbox_max.x - u_bbox_min.x) / max(float(u_nx), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf(p + k.xyy*h) +
        k.yyx * sample_sdf(p + k.yyx*h) +
        k.yxy * sample_sdf(p + k.yxy*h) +
        k.xxx * sample_sdf(p + k.xxx*h));
}

vec2 intersect_aabb(vec3 ro, vec3 rd) {
    vec3 inv_rd = 1.0 / rd;
    vec3 t1 = (u_bbox_min - ro) * inv_rd;
    vec3 t2 = (u_bbox_max - ro) * inv_rd;
    vec3 tmin = min(t1, t2);
    vec3 tmax = max(t1, t2);
    float tNear = max(max(tmin.x, tmin.y), tmin.z);
    float tFar  = min(min(tmax.x, tmax.y), tmax.z);
    return vec2(tNear, tFar);
}

void main() {
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;
    vec3 ro = world_near.xyz;
    vec3 rd = normalize(world_far.xyz - world_near.xyz);
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    vec2 tBox = intersect_aabb(ro, rd);
    float tNear = max(tBox.x, 0.0);
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

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

    vec3 hp  = ro + t * rd;
    vec3 n   = sdf_normal(hp);
    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }
    float diff = max(dot(n, ld), 0.0);
    vec3 vd    = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
    gl_FragColor = vec4(color, 1.0);

    if (u_debug_mode == 1) {
        float v = sample_sdf(hp) / u_max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    vec4 clip     = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z   = clip.z / clip.w;
    gl_FragDepth  = gl_DepthRange.near
                  + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")

        # Uniforms
        # Scene-level uniforms
        u_sdf_tex = coin.SoShaderParameter1i()
        u_sdf_tex.name.setValue("u_sdf_tex")
        u_sdf_tex.value.setValue(0)

        self._u["u_num_fields"] = coin.SoShaderParameter1i()
        self._u["u_num_fields"].name.setValue("u_num_fields")
        self._u["u_num_fields"].value.setValue(0)

        self._u["u_combined_atlas_w"] = coin.SoShaderParameter1f()
        self._u["u_combined_atlas_w"].name.setValue("u_combined_atlas_w")
        self._u["u_combined_atlas_w"].value.setValue(1.0)

        self._u["u_combined_atlas_h"] = coin.SoShaderParameter1f()
        self._u["u_combined_atlas_h"].name.setValue("u_combined_atlas_h")
        self._u["u_combined_atlas_h"].value.setValue(1.0)

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        # Per-field uniform arrays (Coin3D uses "u_nx[0]" naming for GLSL arrays)
        per_field_scalar_i = ["u_nx", "u_ny", "u_nz", "u_atz", "u_row_offset"]
        per_field_scalar_f = ["u_field_atlas_w", "u_field_atlas_h", "u_max_dist"]
        per_field_vec3     = ["u_bbox_min", "u_bbox_max"]

        for fi in range(self.MAX_FIELDS):
            for name in per_field_scalar_i:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1i()
                node.name.setValue(key)
                node.value.setValue(0)
                self._u[key] = node
            for name in per_field_scalar_f:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1f()
                node.name.setValue(key)
                node.value.setValue(1.0)
                self._u[key] = node
            for name in per_field_vec3:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter3f()
                node.name.setValue(key)
                node.value.setValue(coin.SbVec3f(0, 0, 0))
                self._u[key] = node

        # Register all uniforms with the fragment shader
        f_shader.parameter.setNum(0)
        idx = 0
        f_shader.parameter.set1Value(idx, u_sdf_tex); idx += 1
        for key in ["u_num_fields", "u_combined_atlas_w", "u_combined_atlas_h",
                    "u_debug_mode"]:
            f_shader.parameter.set1Value(idx, self._u[key]); idx += 1
        for fi in range(self.MAX_FIELDS):
            for name in (per_field_scalar_i + per_field_scalar_f + per_field_vec3):
                f_shader.parameter.set1Value(idx, self._u[f"{name}[{fi}]"]); idx += 1
        f_shader.parameter.setNum(idx)

        shader.shaderObject.set1Value(0, v_shader)
        shader.shaderObject.set1Value(1, f_shader)
        self._shader_sep.addChild(shader)

        # Quad Geometry
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self._shader_sep.addChild(hints)
        coords = coin.SoCoordinate3()
        coords.point.setValues(0, 4, [
            (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)
        ])
        self._shader_sep.addChild(coords)
        faceset = coin.SoIndexedFaceSet()
        faceset.coordIndex.setValues(0, 8, [0, 1, 2, -1, 0, 2, 3, -1])
        self._shader_sep.addChild(faceset)
        self._root.addChild(self._shader_sep)

    # -- Public API --

    def register_field(self, label, field):
        """Register a new F-Rep field. Triggers combined re-bake."""
        self._fields[label] = (field, True)
        self._attach()
        self._rebuild()

    def unregister_field(self, label):
        """Remove a field. Hides renderer if no fields remain."""
        self._fields.pop(label, None)
        if not self._fields:
            self._switch.whichChild = -1
        else:
            self._rebuild()

    def set_field_visible(self, label, visible):
        """Toggle a field's visibility. Triggers combined re-bake."""
        if label in self._fields:
            field, _ = self._fields[label]
            self._fields[label] = (field, visible)
            self._rebuild()

    def update_field(self, label, field):
        """Update (or register) a field. Triggers combined re-bake."""
        visible = self._fields.get(label, (None, True))[1]
        self._fields[label] = (field, visible)
        if not self._attached:
            self._attach()
        self._rebuild()

    def set_debug_mode(self, mode):
        """Set debug colour mode: 0=normal, 1=SDF heat-map, 2=normals, 3=iterations."""
        self._u["u_debug_mode"].value.setValue(int(mode))

    MAX_FIELDS = 8

    def _rebuild(self):
        """Bake each field independently and upload a stacked atlas."""
        from core.dm_object import get_meshing_cell_size
        import numpy as np

        visible = [(label, f) for label, (f, vis) in self._fields.items()
                   if vis and f is not None]
        if not visible:
            self._switch.whichChild = -1
            return
        if len(visible) > self.MAX_FIELDS:
            dm_logger.warning(f"SceneRayMarch: {len(visible)} fields exceeds "
                              f"MAX_FIELDS={self.MAX_FIELDS}, truncating")
            visible = visible[:self.MAX_FIELDS]

        cell_size = get_meshing_cell_size()

        # Bake each field independently
        baked_list = [bake_sdf_to_atlas(f, cell_size) for _, f in visible]
        n_fields = len(baked_list)

        # Stacked atlas: each field occupies its own row-band
        max_w = max(b["atlas_w"] for b in baked_list)
        total_h = sum(b["atlas_h"] for b in baked_list)

        combined = np.zeros((total_h, max_w, 2), dtype=np.uint8)
        row_offsets = []
        row = 0
        for b in baked_list:
            h, w = b["atlas_h"], b["atlas_w"]
            # Reshape flat bytes back to (h, w, 2) and place in combined
            tile = np.frombuffer(b["atlas_bytes"], dtype=np.uint8).reshape(h, w, 2)
            combined[row:row + h, :w, :] = tile
            row_offsets.append(row)
            row += h

        # Upload single combined texture
        self._tex.image.setValue(
            coin.SbVec2s(max_w, total_h), 2, combined.tobytes())

        # Per-field uniform arrays (indices 0..MAX_FIELDS-1)
        for fi in range(self.MAX_FIELDS):
            if fi < n_fields:
                b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_atz[{fi}]"].value.setValue(int(b["atz"]))
                self._u[f"u_field_atlas_w[{fi}]"].value.setValue(float(b["atlas_w"]))
                self._u[f"u_field_atlas_h[{fi}]"].value.setValue(float(b["atlas_h"]))
                self._u[f"u_max_dist[{fi}]"].value.setValue(float(b["max_dist"]))
                self._u[f"u_row_offset[{fi}]"].value.setValue(int(row_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(
                    coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(
                    coin.SbVec3f(mx.x, mx.y, mx.z))
            else:
                # Zero out unused slots so the shader skips them
                self._u[f"u_nx[{fi}]"].value.setValue(0)

        self._u["u_num_fields"].value.setValue(n_fields)
        self._u["u_combined_atlas_w"].value.setValue(float(max_w))
        self._u["u_combined_atlas_h"].value.setValue(float(total_h))

        # Combined bbox proxy (union of all visible fields' bboxes)
        all_mn = [baked_list[i]["bbox_min"] for i in range(n_fields)]
        all_mx = [baked_list[i]["bbox_max"] for i in range(n_fields)]
        import FreeCAD
        mn_all = FreeCAD.Vector(min(v.x for v in all_mn),
                                min(v.y for v in all_mn),
                                min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(max(v.x for v in all_mx),
                                max(v.y for v in all_mx),
                                max(v.z for v in all_mx))
        self._bbox_coords.point.setValues(0, 8, [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z)
        ])

        self._switch.whichChild = 0
        dm_logger.debug(f"SceneRayMarch: rebuilt ({n_fields} fields, "
                        f"stacked atlas {max_w}x{total_h})")
