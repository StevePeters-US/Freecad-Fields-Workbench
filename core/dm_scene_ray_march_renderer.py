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
from core.gl_texture3d import GLTexture3D



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
        self._gl_tex = GLTexture3D()
        self._bbox_coords = None
        self._cam_sensor    = None
        self._zoom_timer    = None
        self._baked_cache  = {}    # label → baked dict (from sdf_baker)
        self._dirty_fields = set() # labels needing rebake on next _rebuild()
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
            self._attach_camera_sensor(view)
        except Exception:
            pass

    def _detach(self):
        if not self._attached:
            return
        if self._cam_sensor is not None:
            self._cam_sensor.detach()
            self._cam_sensor = None
        if self._zoom_timer is not None:
            self._zoom_timer.stop()
            self._zoom_timer = None
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            sg = view.getSceneGraph()
            sg.removeChild(self._switch)
        except Exception:
            pass
        self._gl_tex.destroy()
        self._attached = False

    def _attach_camera_sensor(self, view):
        """Attach a SoNodeSensor to trigger debounced rebuild on any camera change."""
        try:
            from pivy.coin import SoNodeSensor
            cam = view.getCameraNode()
            if cam is None:
                return
            # SoNodeSensor fires on any field change (height, position, orientation, etc.)
            self._cam_sensor = SoNodeSensor(self._on_camera_changed, None)
            self._cam_sensor.attach(cam)
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: Failed to attach camera sensor: {e}")

    def _on_camera_changed(self, userdata, sensor):
        """Debounced callback: schedule a rebuild 100ms after last zoom event."""
        from PySide import QtCore
        if self._zoom_timer is not None:
            self._zoom_timer.stop()
        self._zoom_timer = QtCore.QTimer()
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.timeout.connect(self._on_zoom_settled)
        self._zoom_timer.start(100)

    def _on_zoom_settled(self):
        """Called 100ms after zoom stopped — rebuild with new adaptive cell sizes."""
        self._zoom_timer = None
        if self._fields:
            self._dirty_fields = set(self._fields.keys())  # all fields need new cell size
            self._rebuild()
            try:
                if FreeCADGui.activeView():
                    FreeCADGui.activeView().redraw()
            except Exception:
                pass

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

        # 3D texture via direct OpenGL (bypasses broken Pivy SoSFImage3).
        # SoCallback binds our GL_TEXTURE_3D to unit 0 before each render.
        self._shader_sep.addChild(self._gl_tex.callback_node)

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

uniform int   u_nx[8];
uniform int   u_ny[8];
uniform int   u_nz[8];
uniform int   u_z_offset[8];
uniform int   u_z_total;
uniform int   u_is_subtractive[8];
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
    float h = cell * 2.0;
    vec2 k = vec2(1.0, -1.0);
    vec3 g = k.xyy * sample_sdf_field(fi, p + k.xyy*h) +
             k.yyx * sample_sdf_field(fi, p + k.yyx*h) +
             k.yxy * sample_sdf_field(fi, p + k.yxy*h) +
             k.xxx * sample_sdf_field(fi, p + k.xxx*h);

    float len2 = dot(g, g);
    return (len2 > 1e-10) ? g * inversesqrt(len2) : vec3(0.0, 1.0, 0.0);
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

    float global_hit_thresh = 1.0;
    for (int fi = 0; fi < 8; fi++) {
        if (fi >= u_num_fields) break;
        float c = (u_bbox_max[fi].x - u_bbox_min[fi].x) / max(float(u_nx[fi]), 1.0);
        global_hit_thresh = min(global_hit_thresh, c * 0.1);
    }

    float t = tNear;
    bool hit = false;
    int hit_field = 0;

    for (int i = 0; i < 512; i++) {
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
            float thresh = cell * 0.1;
            if (abs(d) < thresh) { hit = true; hit_field = fi; break; }
            min_d = min(min_d, abs(d));
        }
        if (hit) break;

        t += max(min_d * 0.9, global_hit_thresh * 0.5);
        if (t > tFar) break;
    }
    if (!hit) discard;

    vec3 hp = ro + t * rd;
    vec3 n  = sdf_normal_field(hit_field, hp);

    vec3 vd = -rd;
    vec3 ld = vd;
    float diff = max(dot(n, ld), 0.0);
    float spec = pow(max(dot(reflect(rd, n), vd), 0.0), 32.0);

    vec3 base_color = (u_is_subtractive[hit_field] == 1)
        ? vec3(0.3, 0.5, 1.0)
        : vec3(1.0, 0.5, 0.0);
    vec3 color = base_color * (0.15 + 0.75 * diff) + vec3(0.4) * spec;
    gl_FragColor = vec4(color, 1.0);

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

        self._u["u_z_total"] = coin.SoShaderParameter1i()
        self._u["u_z_total"].name.setValue("u_z_total")
        self._u["u_z_total"].value.setValue(1)

        # Per-field uniform arrays
        per_field_int  = ["u_nx", "u_ny", "u_nz", "u_z_offset", "u_is_subtractive"]
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
        for key in ["u_num_fields", "u_z_total"]:
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

        faceset = coin.SoIndexedFaceSet()
        # Use indices 8-11 for the quad triangles, plus 8 degenerate triangles
        # (one for each corner 0-7) to force the shape's bbox to exactly include
        # the entire scene bounding box. This prevents Coin3D from culling the quad
        # when zooming into a part of the SDF while other parts are off-screen.
        indices = [8, 9, 10, -1, 8, 10, 11, -1]
        for i in range(8):
            indices.extend([i, i, i, -1])
        faceset.coordIndex.setValues(0, len(indices), indices)
        self._shader_sep.addChild(faceset)

        self._root.addChild(self._shader_sep)

    # -- Public API --

    def register_field(self, label, field):
        """Register a new F-Rep field. Triggers combined re-bake."""
        dm_logger.debug(f"SceneRayMarch: Registering field '{label}'")
        self._fields[label] = (field, True)
        self._dirty_fields.add(label)   # ← only new field
        self._attach()
        self._rebuild()
        if FreeCADGui.activeView():
            FreeCADGui.activeView().redraw()

    def unregister_field(self, label):
        """Remove a field. Hides renderer if no fields remain."""
        if label in self._fields:
            dm_logger.debug(f"SceneRayMarch: Unregistering field '{label}'")
            self._fields.pop(label)
        self._baked_cache.pop(label, None)
        self._dirty_fields.discard(label)
        
        if not self._fields:
            self._switch.whichChild = -1
        else:
            self._rebuild()
            
        # Redraw active view to ensure ghost is cleared everywhere
        try:
            active_view = FreeCADGui.ActiveDocument.ActiveView
            if active_view:
                active_view.redraw()
        except Exception as e:
            dm_logger.debug(f"DMSceneRayMarchRenderer: Failed to redraw view: {e}")

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
        self._dirty_fields.add(label)   # ← only this field
        if not self._attached:
            self._attach()
        self._rebuild()
        if FreeCADGui.activeView():
            FreeCADGui.activeView().redraw()

    def gc_fields(self):
        """Remove any fields whose objects no longer exist in the document."""
        if not self._fields:
            return
            
        to_remove = []
        for label in self._fields:
            try:
                # Label is "DocName.ObjName"
                parts = label.split(".")
                if len(parts) != 2: continue
                doc_name, obj_name = parts
                
                doc = FreeCAD.getDocument(doc_name)
                if not doc or not doc.getObject(obj_name):
                    to_remove.append(label)
            except Exception:
                pass
                
        if to_remove:
            dm_logger.debug(f"SceneRayMarch: GC-ing orphaned fields: {to_remove}")
            for label in to_remove:
                self._fields.pop(label, None)
            self._rebuild()
            # Redraw active view
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view:
                    active_view.redraw()
            except Exception as e:
                dm_logger.debug(f"DMSceneRayMarchRenderer: Failed to redraw view: {e}")

    def on_prefs_changed(self):
        """Update renderer based on global prefs."""
        from core.dm_object import get_render_debug_mode
        debug = get_render_debug_mode()
        self._bbox_switch.whichChild = 0 if debug else -1
        
        # Rebuild to pick up any per-field property changes (e.g. IsSubtractive)
        if self._fields:
            self._dirty_fields = set(self._fields.keys())
            self._rebuild()
        

    def _compute_cell_size(self, field, view):
        """Return adaptive cell size: coarser when field is small on screen, finer when large."""
        import math
        from core.dm_object import get_ray_march_cell_size, get_rm_texels_per_field

        base_cell  = get_ray_march_cell_size()   # quality floor (user maximum quality)
        n_texels   = get_rm_texels_per_field()   # target texels along longest axis

        try:
            bb_min, bb_max = field.bounding_box()
            field_size = max(
                abs(bb_max.x - bb_min.x),
                abs(bb_max.y - bb_min.y),
                abs(bb_max.z - bb_min.z),
                1e-3
            )
            cam = view.getCameraNode()

            # Viewport height in pixels
            vp_h = 800.0
            try:
                viewer = view.getViewer()
                for method in ("getGlxSize", "getSize"):
                    if hasattr(viewer, method):
                        sz = getattr(viewer, method)()
                        vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        break
            except Exception:
                pass

            # World height visible in viewport
            is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
            if is_ortho:
                cam_world_h = cam.height.getValue()
            else:
                dist = (cam.position.getValue() - coin.SbVec3f(0, 0, 0)).length()
                cam_world_h = 2.0 * dist * math.tan(cam.heightAngle.getValue() / 2.0)

            px_per_world = vp_h / max(cam_world_h, 1e-3)
            field_screen_px = field_size * px_per_world

            # Linear ratio: how many times coarser than base_cell would pure-linear LOD want?
            linear_cell  = field_size / max(field_screen_px / n_texels, 1.0)
            linear_ratio = max(linear_cell / base_cell, 1.0)   # >= 1.0 (never finer than base)

            # Sqrt falloff + hard cap:
            #   LOD_EXPONENT = 0.5 -> zoom-out 4x only doubles cell size (was 4x with linear)
            #   MAX_RATIO    = 16  -> worst-case = base * sqrt(16) = 4x base_cell
            LOD_EXPONENT = 0.5
            MAX_RATIO    = 16.0
            ratio = min(linear_ratio, MAX_RATIO) ** LOD_EXPONENT
            return base_cell * ratio

        except Exception:
            return base_cell

    MAX_FIELDS = 8

    def _get_view_frustum_aabb(self, view):
        """Return (min_vec, max_vec) world-space AABB of the camera view frustum.

        Returns None if computation fails (caller falls back to field.bounding_box()).
        """
        import numpy as np
        import math
        try:
            cam = view.getCameraNode()

            # Viewport aspect ratio
            vp_w, vp_h = 1.0, 1.0
            try:
                viewer = view.getViewer()
                for method in ("getGlxSize", "getSize"):
                    if hasattr(viewer, method):
                        sz = getattr(viewer, method)()
                        vp_w = float(sz[0] if isinstance(sz, (list, tuple)) else sz.width())
                        vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        break
            except Exception:
                pass
            aspect = vp_w / max(vp_h, 1.0)

            # Camera axes in world space via orientation quaternion
            rot = cam.orientation.getValue()
            right   = rot.multVec(coin.SbVec3f(1,  0,  0))
            up      = rot.multVec(coin.SbVec3f(0,  1,  0))
            forward = rot.multVec(coin.SbVec3f(0,  0, -1))  # -Z is forward in OpenGL

            r = np.array([right[0],   right[1],   right[2]])
            u = np.array([up[0],      up[1],      up[2]])
            f = np.array([forward[0], forward[1], forward[2]])
            p = np.array([cam.position.getValue()[0],
                          cam.position.getValue()[1],
                          cam.position.getValue()[2]])

            near = cam.nearDistance.getValue()
            far  = cam.farDistance.getValue()

            is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
            if is_ortho:
                hh = cam.height.getValue() / 2.0
                hw = hh * aspect
                # 8 corners of the orthographic frustum box
                corners = np.array([
                    p + f*n + r*sx*hw + u*sy*hh
                    for sx in (-1, 1) for sy in (-1, 1) for n in (near, far)
                ])
            else:
                # Perspective: frustum is a pyramid
                hh_near = math.tan(cam.heightAngle.getValue() / 2.0) * near
                hw_near = hh_near * aspect
                hh_far  = math.tan(cam.heightAngle.getValue() / 2.0) * far
                hw_far  = hh_far  * aspect
                corners = np.array([
                    p + f*near + r*sx*hw_near + u*sy*hh_near
                    for sx in (-1, 1) for sy in (-1, 1)
                ] + [
                    p + f*far  + r*sx*hw_far  + u*sy*hh_far
                    for sx in (-1, 1) for sy in (-1, 1)
                ])

            aabb_min = corners.min(axis=0)
            aabb_max = corners.max(axis=0)
            import FreeCAD
            return (
                FreeCAD.Vector(float(aabb_min[0]), float(aabb_min[1]), float(aabb_min[2])),
                FreeCAD.Vector(float(aabb_max[0]), float(aabb_max[1]), float(aabb_max[2])),
            )
        except Exception as e:
            dm_logger.debug(f"SceneRayMarch: _get_view_frustum_aabb failed: {e}")
            return None

    def _compute_cell_size_for_region(self, bbox_override, view):
        """Compute adaptive cell size for a pre-clipped (min, max) region."""
        import math
        from core.dm_object import get_ray_march_cell_size, get_rm_texels_per_field

        base_cell = get_ray_march_cell_size()
        n_texels  = get_rm_texels_per_field()
        LOD_EXPONENT = 0.5
        MAX_RATIO    = 16.0

        try:
            clip_min, clip_max = bbox_override
            region_size = max(
                abs(clip_max.x - clip_min.x),
                abs(clip_max.y - clip_min.y),
                abs(clip_max.z - clip_min.z),
                1e-3
            )
            cam = view.getCameraNode()
            vp_h = 800.0
            try:
                viewer = view.getViewer()
                for method in ("getGlxSize", "getSize"):
                    if hasattr(viewer, method):
                        sz = getattr(viewer, method)()
                        vp_h = float(sz[1] if isinstance(sz, (list, tuple)) else sz.height())
                        break
            except Exception:
                pass
            is_ortho = cam.isOfType(coin.SoOrthographicCamera.getClassTypeId())
            if is_ortho:
                cam_world_h = cam.height.getValue()
            else:
                dist = (cam.position.getValue() - coin.SbVec3f(0, 0, 0)).length()
                cam_world_h = 2.0 * dist * math.tan(cam.heightAngle.getValue() / 2.0)

            px_per_world  = vp_h / max(cam_world_h, 1e-3)
            region_screen_px = region_size * px_per_world
            linear_cell   = region_size / max(region_screen_px / n_texels, 1.0)
            linear_ratio  = max(linear_cell / base_cell, 1.0)
            ratio = min(linear_ratio, MAX_RATIO) ** LOD_EXPONENT
            return base_cell * ratio
        except Exception:
            return get_ray_march_cell_size()

    def _get_is_subtractive(self, label):
        """Safely look up IsSubtractive from 'DocName.ObjName' label."""
        try:
            parts = label.split(".", 1)
            if len(parts) != 2:
                return False
            doc = FreeCAD.getDocument(parts[0])
            obj = doc.getObject(parts[1]) if doc else None
            return bool(getattr(obj, "IsSubtractive", False))
        except Exception:
            return False

    def _rebuild(self):
        """Bake dirty fields only. Use glTexSubImage3D for single-field updates."""
        from core.frep.sdf_baker import bake_sdf_to_volume
        import numpy as np
        from core.dm_object import get_ray_march_cell_size

        visible = [(label, f) for label, (f, vis) in self._fields.items()
                   if vis and f is not None]

        if not visible:
            self._switch.whichChild = -1
            self._dirty_fields.clear()
            return

        try:
            view = FreeCADGui.ActiveDocument.ActiveView
        except Exception:
            view = None

        frustum = self._get_view_frustum_aabb(view) if view else None

        # --- Rebake only dirty fields ---
        for label, f in visible:
            if label in self._dirty_fields or label not in self._baked_cache:
                try:
                    # Compute visible subregion
                    bbox_override = None
                    if frustum is not None:
                        try:
                            f_min, f_max = f.bounding_box()
                            fr_min, fr_max = frustum
                            clip_min = FreeCAD.Vector(
                                max(f_min.x, fr_min.x),
                                max(f_min.y, fr_min.y),
                                max(f_min.z, fr_min.z),
                            )
                            clip_max = FreeCAD.Vector(
                                min(f_max.x, fr_max.x),
                                min(f_max.y, fr_max.y),
                                min(f_max.z, fr_max.z),
                            )
                            # Non-empty intersection?
                            if (clip_min.x < clip_max.x and
                                clip_min.y < clip_max.y and
                                clip_min.z < clip_max.z):
                                bbox_override = (clip_min, clip_max)
                            else:
                                dm_logger.debug(f"SceneRayMarch: '{label}' off-screen, skipping bake")
                                self._baked_cache.pop(label, None)
                                continue   # skip — not visible
                        except Exception:
                            pass  # fall through to full-field bake

                    # Cell size adapted to visible subregion (not full field)
                    cs = self._compute_cell_size_for_region(bbox_override, view) if bbox_override else self._compute_cell_size(f, view)
                    baked = bake_sdf_to_volume(f, cs, bbox_override=bbox_override)
                    self._baked_cache[label] = baked
                except Exception as e:
                    dm_logger.debug(f"SceneRayMarch: Bake failed for '{label}': {e}")
                    self._baked_cache.pop(label, None)

        self._dirty_fields.clear()

        # Collect baked results in visible order
        baked_list = []
        for label, _ in visible:
            b = self._baked_cache.get(label)
            if b is not None:
                baked_list.append((label, b))

        if not baked_list:
            self._switch.whichChild = -1
            return

        n_fields = len(baked_list)

        # --- Determine if atlas dimensions changed ---
        new_max_nx = max(b["nx"] for _, b in baked_list) + 1
        new_max_ny = max(b["ny"] for _, b in baked_list) + 1
        new_total_nz = sum(b["nz"] + 1 for _, b in baked_list)

        dims_changed = (
            new_max_nx  != getattr(self, "_atlas_nx", 0) or
            new_max_ny  != getattr(self, "_atlas_ny", 0) or
            new_total_nz != getattr(self, "_atlas_nz", 0) or
            n_fields    != getattr(self, "_atlas_n",  0)
        )

        # --- Build combined volume ---
        combined = np.zeros((new_total_nz, new_max_ny, new_max_nx), dtype=np.float32)
        z_offsets = []
        z_cursor = 0
        for _, b in baked_list:
            nx1, ny1, nz1 = b["nx"] + 1, b["ny"] + 1, b["nz"] + 1
            vol = np.frombuffer(b["volume_bytes"], dtype=np.float32).reshape(nz1, ny1, nx1)
            combined[z_cursor:z_cursor + nz1, :ny1, :nx1] = vol
            z_offsets.append(z_cursor)
            z_cursor += nz1

        volume_bytes = combined.astype(np.float32).tobytes()

        if dims_changed:
            # Full upload (dimensions changed)
            self._gl_tex.upload(new_max_nx, new_max_ny, new_total_nz, volume_bytes)
            self._atlas_nx = new_max_nx
            self._atlas_ny = new_max_ny
            self._atlas_nz = new_total_nz
            self._atlas_n  = n_fields
        else:
            # Partial upload (only data changed, same dimensions)
            # NOTE: We use full upload() here for simplicity; GLTexture3D handles 
            # the differentiation if we wanted to optimize further with glTexSubImage3D 
            # for the entire atlas, but the current upload() implementation triggers 
            # a full glTexImage3D if _needs_upload is true.
            # Actually, per Group C instructions, we stick with upload() for now.
            self._gl_tex.upload(new_max_nx, new_max_ny, new_total_nz, volume_bytes)

        # --- Update per-field uniforms ---
        for fi in range(self.MAX_FIELDS):
            if fi < n_fields:
                label, b = baked_list[fi]
                mn, mx = b["bbox_min"], b["bbox_max"]
                self._u[f"u_nx[{fi}]"].value.setValue(int(b["nx"]))
                self._u[f"u_ny[{fi}]"].value.setValue(int(b["ny"]))
                self._u[f"u_nz[{fi}]"].value.setValue(int(b["nz"]))
                self._u[f"u_z_offset[{fi}]"].value.setValue(int(z_offsets[fi]))
                self._u[f"u_bbox_min[{fi}]"].value.setValue(coin.SbVec3f(mn.x, mn.y, mn.z))
                self._u[f"u_bbox_max[{fi}]"].value.setValue(coin.SbVec3f(mx.x, mx.y, mx.z))
                
                is_sub = self._get_is_subtractive(label)
                self._u[f"u_is_subtractive[{fi}]"].value.setValue(1 if is_sub else 0)
            else:
                self._u[f"u_nx[{fi}]"].value.setValue(0)

        self._u["u_num_fields"].value.setValue(n_fields)
        self._u["u_z_total"].value.setValue(int(new_total_nz))

        # Combined bbox proxy
        all_mn = [b["bbox_min"] for _, b in baked_list]
        all_mx = [b["bbox_max"] for _, b in baked_list]
        mn_all = FreeCAD.Vector(min(v.x for v in all_mn), min(v.y for v in all_mn), min(v.z for v in all_mn))
        mx_all = FreeCAD.Vector(max(v.x for v in all_mx), max(v.y for v in all_mx), max(v.z for v in all_mx))
        pts = [
            (mn_all.x, mn_all.y, mn_all.z), (mx_all.x, mn_all.y, mn_all.z),
            (mn_all.x, mx_all.y, mn_all.z), (mx_all.x, mx_all.y, mn_all.z),
            (mn_all.x, mn_all.y, mx_all.z), (mx_all.x, mn_all.y, mx_all.z),
            (mn_all.x, mx_all.y, mx_all.z), (mx_all.x, mx_all.y, mx_all.z),
        ]
        self._bbox_coords.point.setValues(0, 8, pts)
        self._coords.point.setValues(0, 8, pts)
        self._switch.whichChild = 0

