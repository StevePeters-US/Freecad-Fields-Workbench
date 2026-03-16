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
        # Disable frustum culling and caching. renderCulling is the key one:
        # without it, Coin3D culls the separator when the field's AABB is
        # partially behind the near plane (camera close-up).
        for attr in ["renderCulling", "renderCaching", "cullCaching", "boundingBoxCaching"]:
            try:
                getattr(self._root, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError:
                pass
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
        except Exception:
            pass

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
        self._bbox_switch = coin.SoSwitch()
        self._bbox_sep = coin.SoSeparator()
        self._bbox_switch.addChild(self._bbox_sep)
        self._root.addChild(self._bbox_switch)
        
        from core.dm_object import get_render_debug_mode
        self._bbox_switch.whichChild = 0 if get_render_debug_mode() else -1
        
        # Transparent material (Coin3D BBox action ignores INVISIBLE draw style)
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
        # self._root.addChild(self._bbox_sep)  <- moved to switch above

        # 2. Shader-scoped separator (isolates shader from bbox proxy)
        self._shader_sep = coin.SoSeparator()
        for attr in ["renderCulling", "renderCaching", "cullCaching", "boundingBoxCaching"]:
            try:
                getattr(self._shader_sep, attr).setValue(coin.SoSeparator.OFF)
            except AttributeError:
                pass

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

        # Single combined 3D texture (all fields stacked along z-axis)
        # The texture stores raw float32 bytes as RGBA8. The shader uses
        # texelFetch() for exact texel access and manual trilinear on decoded floats.
        self._tex = coin.SoTexture3()
        self._tex.wrapR.setValue(coin.SoTexture3.CLAMP)
        self._tex.wrapS.setValue(coin.SoTexture3.CLAMP)
        self._tex.wrapT.setValue(coin.SoTexture3.CLAMP)
        self._shader_sep.addChild(self._tex)

        # Shader Program — identical vertex+fragment shader to DMRayMarchRenderer
        shader = coin.SoShaderProgram()
        v_shader = coin.SoVertexShader()
        v_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)
        f_shader = coin.SoFragmentShader()
        f_shader.sourceType.setValue(coin.SoShaderObject.GLSL_PROGRAM)

        v_shader.sourceProgram.setValue("""
#version 330 compatibility
out vec2 v_uv;
void main() {
    v_uv = gl_Vertex.xy;
    gl_Position = vec4(gl_Vertex.xy, 0.0, 1.0);
}
""")

        # when outside all active fields.
        f_shader.sourceProgram.setValue("""
#version 330 compatibility
in vec2 v_uv;
uniform sampler3D u_sdf_vol;
uniform int   u_num_fields;
uniform int   u_debug_mode;

uniform int   u_nx[8];
uniform int   u_ny[8];
uniform int   u_nz[8];
uniform int   u_z_offset[8];
uniform int   u_z_total;
uniform vec3  u_bbox_min[8];
uniform vec3  u_bbox_max[8];

float decode_texel(ivec3 tc) {
    vec4 c = texelFetch(u_sdf_vol, tc, 0);
    uvec4 b = uvec4(round(c * 255.0));
    uint bits = b.r | (b.g << 8u) | (b.b << 16u) | (b.a << 24u);
    return uintBitsToFloat(bits);
}

float sample_sdf_field(int fi, vec3 p) {
    vec3 uvw = (p - u_bbox_min[fi]) / (u_bbox_max[fi] - u_bbox_min[fi]);
    uvw = clamp(uvw, vec3(0.0), vec3(1.0));
    // Map field-local uvw to integer texel coords in combined volume
    ivec3 sz = textureSize(u_sdf_vol, 0);
    // Field spans [0, u_nx+1) texels in x, [0, u_ny+1) in y,
    // [u_z_offset, u_z_offset + u_nz+1) in z within the combined volume.
    vec3 tc = vec3(
        uvw.x * float(u_nx[fi]),
        uvw.y * float(u_ny[fi]),
        float(u_z_offset[fi]) + uvw.z * float(u_nz[fi])
    );
    tc = clamp(tc, vec3(0.0), vec3(float(u_nx[fi]), float(u_ny[fi]),
               float(u_z_offset[fi] + u_nz[fi])));
    ivec3 c0 = ivec3(floor(tc));
    ivec3 c1 = min(c0 + 1, ivec3(u_nx[fi], u_ny[fi], u_z_offset[fi] + u_nz[fi]));
    vec3 f = tc - vec3(c0);
    float d000 = decode_texel(ivec3(c0.x, c0.y, c0.z));
    float d100 = decode_texel(ivec3(c1.x, c0.y, c0.z));
    float d010 = decode_texel(ivec3(c0.x, c1.y, c0.z));
    float d110 = decode_texel(ivec3(c1.x, c1.y, c0.z));
    float d001 = decode_texel(ivec3(c0.x, c0.y, c1.z));
    float d101 = decode_texel(ivec3(c1.x, c0.y, c1.z));
    float d011 = decode_texel(ivec3(c0.x, c1.y, c1.z));
    float d111 = decode_texel(ivec3(c1.x, c1.y, c1.z));
    float dx00 = mix(d000, d100, f.x);
    float dx10 = mix(d010, d110, f.x);
    float dx01 = mix(d001, d101, f.x);
    float dx11 = mix(d011, d111, f.x);
    float dxy0 = mix(dx00, dx10, f.y);
    float dxy1 = mix(dx01, dx11, f.y);
    return mix(dxy0, dxy1, f.z);
}

vec3 sdf_normal_field(int fi, vec3 p) {
    float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
    float h = cell * 0.5;
    vec2 k = vec2(1.0, -1.0);
    return normalize(
        k.xyy * sample_sdf_field(fi, p + k.xyy*h) +
        k.yyx * sample_sdf_field(fi, p + k.yyx*h) +
        k.yxy * sample_sdf_field(fi, p + k.yxy*h) +
        k.xxx * sample_sdf_field(fi, p + k.xxx*h));
}

vec2 intersect_aabb(vec3 ro, vec3 rd, vec3 bmin, vec3 bmax) {
    vec3 t1 = (bmin - ro) / rd;
    vec3 t2 = (bmax - ro) / rd;
    vec3 tmin = min(t1, t2);
    vec3 tmax = max(t1, t2);
    return vec2(max(max(tmin.x, tmin.y), tmin.z),
                min(min(tmax.x, tmax.y), tmax.z));
}

void main() {
    vec4 ndc_near = vec4(v_uv, -1.0, 1.0);
    vec4 world_near = gl_ModelViewProjectionMatrixInverse * ndc_near;
    world_near /= world_near.w;
    vec4 ndc_far = vec4(v_uv, 1.0, 1.0);
    vec4 world_far = gl_ModelViewProjectionMatrixInverse * ndc_far;
    world_far /= world_far.w;
    vec3 cam = (gl_ModelViewMatrixInverse * vec4(0.0,0.0,0.0,1.0)).xyz;

    vec3 ro, rd;
    bool is_persp = (gl_ProjectionMatrix[3][3] < 0.5);
    if (is_persp) {
        ro = cam;
        rd = normalize(world_near.xyz - cam);
    } else {
        ro = world_near.xyz;
        rd = normalize(world_far.xyz - world_near.xyz);
    }

    // Combined AABB early discard
    vec3 scene_min = u_bbox_min[0];
    vec3 scene_max = u_bbox_max[0];
    for (int fi = 1; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        scene_min = min(scene_min, u_bbox_min[fi]);
        scene_max = max(scene_max, u_bbox_max[fi]);
    }
    vec2 tBox = intersect_aabb(ro, rd, scene_min, scene_max);
    float tNear = is_persp ? max(tBox.x, 0.0) : tBox.x;
    float tFar  = tBox.y;
    if (tNear > tFar) discard;

    // Per-field AABB intervals
    float ftn[8];
    float ftf[8];
    for (int fi = 0; fi < 8; fi++) {
        if (fi < u_num_fields) {
            vec2 fi_int = intersect_aabb(ro, rd, u_bbox_min[fi], u_bbox_max[fi]);
            ftn[fi] = is_persp ? max(fi_int.x, 0.0) : fi_int.x;
            ftf[fi] = fi_int.y;
        } else {
            ftn[fi] =  1.0e10;
            ftf[fi] = -1.0e10;
        }
    }

    float t = tNear;
    bool hit = false;
    int hit_field = 0;
    int march_iters = 0;

    for (int i = 0; i < 256; i++) {
        march_iters = i;
        vec3 p = ro + t * rd;
        float min_d = 1.0e10;

        for (int fi = 0; fi < 8; fi++) {
            if (fi >= u_num_fields) break;
            if (ftn[fi] > ftf[fi]) continue;
            if (t > ftf[fi])       continue;
            if (t < ftn[fi]) {
                min_d = min(min_d, ftn[fi] - t);
                continue;
            }
            float d = sample_sdf_field(fi, p);
            float cell = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
            float thresh = cell * 0.01;
            if (abs(d) < thresh) { hit = true; hit_field = fi; break; }
            min_d = min(min_d, abs(d));
        }
        if (hit) break;

        t += max(min_d, 0.0001);
        if (t > tFar) break;
    }
    if (!hit) discard;

    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal_field(hit_field, hp);

    vec4 light_eye = gl_LightSource[0].position;
    vec3 ld;
    if (light_eye.w < 0.5) {
        ld = normalize((gl_ModelViewMatrixInverse * vec4(light_eye.xyz, 0.0)).xyz);
    } else {
        vec3 light_world = (gl_ModelViewMatrixInverse * light_eye).xyz;
        ld = normalize(light_world - hp);
    }
    float diff = max(dot(n, ld), 0.0);
    vec3 vd   = normalize(cam - hp);
    float spec = pow(max(dot(reflect(-ld, n), vd), 0.0), 32.0);
    vec3 color = vec3(1.0,0.5,0.0)*(0.15 + 0.75*diff) + vec3(0.4)*spec;
    gl_FragColor = vec4(color, 1.0);

    if (u_debug_mode == 1) {
        float max_dist = (u_bbox_max[hit_field].x - u_bbox_min[hit_field].x) * 0.5;
        float v = sample_sdf_field(hit_field, hp) / max_dist * 0.5 + 0.5;
        gl_FragColor = vec4(v, 0.0, 1.0 - v, 1.0);
    } else if (u_debug_mode == 2) {
        gl_FragColor = vec4(n * 0.5 + 0.5, 1.0);
    } else if (u_debug_mode == 3) {
        gl_FragColor = vec4(vec3(float(march_iters) / 256.0), 1.0);
    }

    vec4 clip    = gl_ModelViewProjectionMatrix * vec4(hp, 1.0);
    float ndc_z  = clip.z / clip.w;
    gl_FragDepth = gl_DepthRange.near
                 + gl_DepthRange.diff * (ndc_z * 0.5 + 0.5);
}
""")

        # Uniforms
        u_sdf_vol = coin.SoShaderParameter1i()
        u_sdf_vol.name.setValue("u_sdf_vol")
        u_sdf_vol.value.setValue(0)

        self._u["u_num_fields"] = coin.SoShaderParameter1i()
        self._u["u_num_fields"].name.setValue("u_num_fields")
        self._u["u_num_fields"].value.setValue(0)

        self._u["u_debug_mode"] = coin.SoShaderParameter1i()
        self._u["u_debug_mode"].name.setValue("u_debug_mode")
        self._u["u_debug_mode"].value.setValue(0)

        self._u["u_z_total"] = coin.SoShaderParameter1i()
        self._u["u_z_total"].name.setValue("u_z_total")
        self._u["u_z_total"].value.setValue(1)

        # Per-field uniform arrays
        per_field_int  = ["u_nx", "u_ny", "u_nz", "u_z_offset"]
        per_field_vec3 = ["u_bbox_min", "u_bbox_max"]

        for fi in range(self.MAX_FIELDS):
            for name in per_field_int:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter1i()
                node.name.setValue(key)
                node.value.setValue(0)
                self._u[key] = node
            for name in per_field_vec3:
                key = f"{name}[{fi}]"
                node = coin.SoShaderParameter3f()
                node.name.setValue(key)
                node.value.setValue(coin.SbVec3f(0, 0, 0))
                self._u[key] = node

        # Register all uniforms with fragment shader
        f_shader.parameter.setNum(0)
        idx = 0
        f_shader.parameter.set1Value(idx, u_sdf_vol); idx += 1
        for key in ["u_num_fields", "u_debug_mode", "u_z_total"]:
            f_shader.parameter.set1Value(idx, self._u[key]); idx += 1
        for fi in range(self.MAX_FIELDS):
            for name in (per_field_int + per_field_vec3):
                f_shader.parameter.set1Value(idx, self._u[f"{name}[{fi}]"]); idx += 1
        f_shader.parameter.setNum(idx)

        shader.shaderObject.set1Value(0, v_shader)
        shader.shaderObject.set1Value(1, f_shader)
        self._shader_sep.addChild(shader)

        # 3. Quad Geometry + AABB expansion points
        hints = coin.SoShapeHints()
        hints.vertexOrdering.setValue(coin.SoShapeHints.UNKNOWN_ORDERING)
        self._shader_sep.addChild(hints)

        self._coords = coin.SoCoordinate3()
        # Points 0-7: Combined AABB corners (updated in _rebuild())
        # Points 8-11: NDC quad [-1, 1]
        self._coords.point.setValues(8, 4, [
            (-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)
        ])
        self._shader_sep.addChild(self._coords)

        # Expansion points with INVISIBLE style to prevent culling without rendering dots
        bbox_mat = coin.SoMaterial()
        bbox_mat.transparency.setValue(1.0)
        self._shader_sep.addChild(bbox_mat)

        bbox_style = coin.SoDrawStyle()
        bbox_style.style.setValue(coin.SoDrawStyle.INVISIBLE)
        self._shader_sep.addChild(bbox_style)
        
        self._bbox_expansion = coin.SoPointSet()
        self._bbox_expansion.numPoints.setValue(8)
        self._shader_sep.addChild(self._bbox_expansion)
        
        # Reset for quad
        quad_style = coin.SoDrawStyle()
        quad_style.style.setValue(coin.SoDrawStyle.FILLED)
        self._shader_sep.addChild(quad_style)

        # Force opaque material EXPLICITLY before the quad geometry.
        # This prevents the transparent material from expansion points
        # from leaking into the quad's state.
        quad_mat = coin.SoMaterial()
        quad_mat.transparency.setValue(0.0)
        self._shader_sep.addChild(quad_mat)

        faceset = coin.SoIndexedFaceSet()
        # Use indices 8-11 for the quad triangles.
        # Points 0-7 are use by SoPointSet + SoDrawStyle(INVISIBLE) to expand the
        # separator's bounding box and prevent culling.
        indices = [8, 9, 10, -1, 10, 11, 8, -1]
        faceset.coordIndex.setValues(0, len(indices), indices)
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

    def on_prefs_changed(self):
        """Update renderer based on global prefs."""
        from core.dm_object import get_render_debug_mode
        debug = get_render_debug_mode()
        self._bbox_switch.whichChild = 0 if debug else -1
        # Also sync shader debug mode if we're in debug
        self.set_debug_mode(3 if debug else 0) # Use iterations mode by default for debug
        

    MAX_FIELDS = 8

    def _rebuild(self):
        """Bake each field independently and upload a stacked 3D volume."""
        from core.dm_object import get_meshing_cell_size
        from core.frep.sdf_baker import bake_sdf_to_volume
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

        # Bake each field to a 3D float32 volume
        baked_list = [bake_sdf_to_volume(f, cell_size) for _, f in visible]
        n_fields = len(baked_list)

        # Stack volumes along z-axis into one combined 3D texture.
        # All fields are padded to the max x/y dimensions.
        max_nx = max(b["nx"] for b in baked_list) + 1  # +1 for sample points
        max_ny = max(b["ny"] for b in baked_list) + 1
        total_nz = sum(b["nz"] + 1 for b in baked_list)

        # Build combined volume (float32, then reinterpret as RGBA8)
        combined = np.zeros((total_nz, max_ny, max_nx), dtype=np.float32)
        z_offsets = []
        z_cursor = 0
        for b in baked_list:
            nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
            # bake_sdf_to_volume returns bytes in (nz+1, ny+1, nx+1) order
            vol = np.frombuffer(b["volume_bytes"], dtype=np.float32).reshape(nz1, ny1, nx1)
            combined[z_cursor:z_cursor + nz1, :ny1, :nx1] = vol
            z_offsets.append(z_cursor)
            z_cursor += nz1

        # Upload as RGBA8 (float32 reinterpreted as 4×uint8)
        volume_bytes = combined.astype(np.float32).tobytes()
        self._tex.images.setValue(
            coin.SbVec3s(max_nx, max_ny, total_nz), 4, volume_bytes)

        # Update per-field uniforms
        for fi in range(self.MAX_FIELDS):
            if fi < n_fields:
                b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_z_offset[{fi}]"].value.setValue(int(z_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(
                    coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(
                    coin.SbVec3f(mx.x, mx.y, mx.z))
            else:
                self._u[f"u_nx[{fi}]"].value.setValue(0)

        self._u["u_num_fields"].value.setValue(n_fields)
        self._u["u_z_total"].value.setValue(int(total_nz))

        # Combined bbox proxy
        import FreeCAD
        all_mn = [b["bbox_min"] for b in baked_list]
        all_mx = [b["bbox_max"] for b in baked_list]
        mn_all = FreeCAD.Vector(min(v.x for v in all_mn),
                                min(v.y for v in all_mn),
                                min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(max(v.x for v in all_mx),
                                max(v.y for v in all_mx),
                                max(v.z for v in all_mx))
        pts = [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z)
        ]
        self._bbox_coords.point.setValues(0, 8, pts)
        self._coords.point.setValues(0, 8, pts)

        self._switch.whichChild = 0

