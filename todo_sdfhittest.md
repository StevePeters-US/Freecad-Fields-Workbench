# Direct Modeling Workbench — SDF Hit Test Task List

> Tasks are ordered by priority. Each task is atomic and self-contained.
> Intended audience: junior developer or AI model (Gemini Flash).
> Each task includes the exact file(s) and line numbers to change.

---

## Background

F-Rep SDF objects are rendered via a GPU ray-marcher (baked texture shader), but there is currently
no CPU-side way to ask "does this ray hit this SDF surface, and where?". This means:

1. Workplane placement cannot snap to SDF surface normals — it only snaps to NURBS geometry.
2. Point and curve-point tools cannot place points on SDF surfaces.
3. LMB-clicking an SDF object in the viewport does not select it (FreeCAD's normal Coin3D picker
   only works for objects with a Part.Shape, not for pure Coin3D F-Rep objects).

The solution is sphere-tracing: step along the ray by the SDF value at each position until the
distance drops below a threshold. This is the same algorithm the GPU shader already uses, but
implemented in Python on the CPU for interactive hit testing.

After all tasks complete, tools that call `projector.get_mouse_plane_pt()` or
`projector.get_geometry_info()` will automatically snap to SDF surfaces, and LMB clicks in the
viewport (with no tool active) will select SDF objects.

### Key APIs

| Symbol | Location | Purpose |
|--------|----------|---------|
| `FRepField` | `core/frep/frep_field.py:4` | Base class for all SDF fields |
| `FRepField.evaluate()` | `core/frep/frep_field.py:9` | Scalar SDF at a point |
| `FRepField.gradient()` | `core/frep/frep_field.py:13` | Numerical gradient (= outward normal direction) |
| `FRepField.bounding_box()` | `core/frep/frep_field.py:20` | Returns `(min_corner, max_corner)` as Vector pair |
| `ViewProjector` | `core/view_projector.py:8` | Ray/projection helpers for all tools |
| `ViewProjector.get_geometry_info()` | `core/view_projector.py:270` | NURBS surface hit (point + normal + obj) |
| `ViewProjector.get_mouse_plane_pt()` | `core/view_projector.py:180` | Main point-in-3D resolver for tools |
| `DMInputManager.get_ray()` | `core/input_manager.py` | Returns `(ray_origin, ray_direction)` for mouse pos |
| `WorkPlaneCreator.get_snapped_placement()` | `tools/work_plane_tool.py:161` | NURBS surface snap for workplane |
| `DMInputManager.eventFilter()` | `core/input_manager.py:30` | Qt event filter; handles global hotkeys/selection |
| `obj.Proxy.FRepField` | set in `tools/primitive_tool.py:70` | Access field from a FreeCAD F-Rep object |

---

## Tier 1 — Core Algorithm (Do First)

Implement sphere-trace ray marching on the `FRepField` base class. This unblocks all integration tasks.

### H-001: Add `ray_march()` to `FRepField`

**File:** `core/frep/frep_field.py` — append after line 72 (end of file, after `curvature_grid`)

**What:** Sphere-trace a ray against the SDF. Starts at the AABB entry point for efficiency.
Returns `(hit_point, hit_normal)` as a `FreeCAD.Vector` pair, or `None` if no hit.

```python
    def ray_march(self, ray_origin, ray_direction, max_steps=128, surface_eps=0.5):
        """
        Sphere-trace a ray against this SDF field.
        Returns (hit_point, hit_normal) as FreeCAD.Vector pair, or None if no hit.

        ray_origin:    world-space ray origin (FreeCAD.Vector)
        ray_direction: world-space direction (will be normalized internally)
        max_steps:     maximum sphere-trace iterations (default 128)
        surface_eps:   surface hit threshold in mm (default 0.5)
        """
        # Normalize direction
        d = FreeCAD.Vector(ray_direction)
        dlen = d.Length
        if dlen < 1e-10:
            return None
        d = d * (1.0 / dlen)

        # AABB slab test — find ray entry/exit t values along the bounding box
        try:
            bb_min, bb_max = self.bounding_box()
        except NotImplementedError:
            bb_min = ray_origin - FreeCAD.Vector(50000, 50000, 50000)
            bb_max = ray_origin + FreeCAD.Vector(50000, 50000, 50000)

        t_near = -1e18
        t_far  =  1e18
        axes = [
            (d.x, ray_origin.x, bb_min.x, bb_max.x),
            (d.y, ray_origin.y, bb_min.y, bb_max.y),
            (d.z, ray_origin.z, bb_min.z, bb_max.z),
        ]
        for d_comp, o_comp, mn, mx in axes:
            if abs(d_comp) < 1e-10:
                if o_comp < mn or o_comp > mx:
                    return None  # parallel to slab and outside — miss
            else:
                t1 = (mn - o_comp) / d_comp
                t2 = (mx - o_comp) / d_comp
                if t1 > t2:
                    t1, t2 = t2, t1
                t_near = max(t_near, t1)
                t_far  = min(t_far,  t2)
                if t_near > t_far:
                    return None  # missed AABB

        if t_far < 0:
            return None  # AABB entirely behind the ray

        t = max(t_near, 0.0)
        max_dist = t_far

        # Sphere trace from AABB entry point
        for _ in range(max_steps):
            if t > max_dist:
                break
            pos = ray_origin + d * t
            dist = self.evaluate(pos)
            if dist < surface_eps:
                # Hit — compute outward normal via gradient
                normal = self.gradient(pos)
                nl = normal.Length
                if nl > 1e-10:
                    normal = normal * (1.0 / nl)
                else:
                    normal = FreeCAD.Vector(0, 0, 1)
                return pos, normal
            # Advance by the SDF value; min step prevents stalling at a near-zero surface
            t += max(dist, surface_eps * 0.1)

        return None
```

---

## Tier 2 — ViewProjector Integration

Add SDF hit testing to `ViewProjector` and wire it into the main point-resolution pipeline.

### H-002: Add `get_sdf_hit()` to `ViewProjector`

**File:** `core/view_projector.py` — append after line 344 (end of file, after `get_geometry_info`)

**What:** Ray-march against all visible F-Rep objects in the document. Returns the closest hit as
`(hit_point, hit_normal, obj)` or `None`. Called by `get_mouse_plane_pt()` and tool code.

```python
    def get_sdf_hit(self, event_dict, skip_objects=None):
        """
        Ray-march against all visible F-Rep SDF objects in the scene.
        Returns (hit_point, hit_normal, obj) for the closest hit, or None.
        """
        if not self.view:
            return None
        doc = FreeCAD.ActiveDocument
        if not doc:
            return None
        skip_names = {o.Name for o in skip_objects} if skip_objects else set()
        try:
            ray_p, ray_d = DMInputManager.get_instance().get_ray(self.view, event_dict)
            if not ray_p or not ray_d:
                return None
            ray_d_norm = FreeCAD.Vector(ray_d)
            ray_d_norm.normalize()
            try:
                cam_vals = self.view.getCameraNode().position.getValue()
                cam_pos = FreeCAD.Vector(cam_vals[0], cam_vals[1], cam_vals[2])
            except Exception:
                cam_pos = ray_p

            best_t = float('inf')
            best = None
            for obj in doc.Objects:
                if obj.Name in skip_names:
                    continue
                if not hasattr(obj, 'Proxy') or not hasattr(obj.Proxy, 'FRepField'):
                    continue
                field = obj.Proxy.FRepField
                if field is None:
                    continue
                try:
                    visible = obj.ViewObject.Visibility
                except Exception:
                    visible = True
                if not visible:
                    continue
                result = field.ray_march(ray_p, ray_d_norm)
                if result is None:
                    continue
                hit_pt, hit_normal = result
                t = (hit_pt - cam_pos).dot(ray_d_norm)
                if t > 0 and t < best_t:
                    best_t = t
                    best = (hit_pt, hit_normal, obj)
            return best
        except Exception as e:
            dm_logger.debug(f"get_sdf_hit failed: {e}")
            return None
```

**Depends on:** H-001

---

### H-003: Integrate SDF surface hit into `get_mouse_plane_pt()`

**File:** `core/view_projector.py` — modify `get_mouse_plane_pt()` starting at line 180.

**What:** SDF surface hits compete with workplane hits and NURBS geometry hits by camera depth.
Add the SDF query between the existing `geom_pt` block (step 2) and the "return closest" block
(step 3). Replace lines 241–254 with the code below.

Replace this block (lines 241–254):
```python
            # 2. Geometry hit (if enabled).
            geom_pt = None
            geom_t = float('inf')
            if place_on_geometry:
                geom_pt = self._get_geometry_point(event_dict, skip_names=skip_names)
                if geom_pt is not None:
                    geom_t = (geom_pt - cam_pos).dot(ray_d)

            # 3. Return closest of bounded wp and geometry.
            if wp_pt is not None and wp_t <= geom_t:
                return wp_pt, wp_hit
            if geom_pt is not None:
                return geom_pt
```

With:
```python
            # 2. Geometry hit (if enabled).
            geom_pt = None
            geom_t = float('inf')
            if place_on_geometry:
                geom_pt = self._get_geometry_point(event_dict, skip_names=skip_names)
                if geom_pt is not None:
                    geom_t = (geom_pt - cam_pos).dot(ray_d)

            # 2b. SDF surface hit (always tested — not gated by place_on_geometry).
            sdf_pt = None
            sdf_t = float('inf')
            sdf_result = self.get_sdf_hit(event_dict, skip_objects=skip_objects)
            if sdf_result is not None:
                sdf_pt, _sdf_n, _sdf_obj = sdf_result
                sdf_t = (sdf_pt - cam_pos).dot(ray_d)

            # 3. Return closest of bounded workplane / SDF surface / NURBS geometry.
            if wp_pt is not None and wp_t <= sdf_t and wp_t <= geom_t:
                return wp_pt, wp_hit
            if sdf_pt is not None and sdf_t <= geom_t:
                return sdf_pt, None
            if geom_pt is not None:
                return geom_pt
```

**Depends on:** H-002

---

## Tier 3 — Tool Integration

Wire SDF hit testing into workplane placement and LMB selection.

### H-004: Snap workplane normal to SDF surface in `get_snapped_placement()`

**File:** `tools/work_plane_tool.py` — modify `get_snapped_placement()` starting at line 161.

**What:** After the existing NURBS geometry snap block (which ends at line 183 with
`return FreeCAD.Placement(m)`), add an SDF surface snap fallback before the working-plane fallback.
Insert the following block between line 183 (`return FreeCAD.Placement(m)`) and
line 185 (`# Fallback to current working plane or camera-facing plane`):

```python
        # SDF surface snap — snap normal to SDF surface when no NURBS geometry is under mouse
        skip = [self.preview_obj] if self.preview_obj else None
        sdf_result = self.projector.get_sdf_hit(event_dict, skip_objects=skip)
        if sdf_result:
            world_hit, world_n, _sdf_obj = sdf_result
            # Ensure normal faces toward viewer
            vd = self.view.getViewDirection()
            view_dir = FreeCAD.Vector(vd[0], vd[1], vd[2])
            if world_n.dot(view_dir) > 0:
                world_n = world_n.negative()
            z_axis = world_n
            global_z = FreeCAD.Vector(0, 0, 1)
            x_axis = global_z.cross(z_axis) if abs(z_axis.dot(global_z)) < 0.99 else FreeCAD.Vector(1, 0, 0)
            x_axis.normalize()
            y_axis = z_axis.cross(x_axis); y_axis.normalize()
            m = FreeCAD.Matrix(
                x_axis.x, y_axis.x, z_axis.x, world_hit.x,
                x_axis.y, y_axis.y, z_axis.y, world_hit.y,
                x_axis.z, y_axis.z, z_axis.z, world_hit.z,
                0.0,      0.0,      0.0,      1.0
            )
            return FreeCAD.Placement(m)
```

**Depends on:** H-002

---

### H-005: Select SDF objects on LMB click (no active tool)

**File:** `core/input_manager.py` — modify `eventFilter()` at line 30.

**What:** When the left mouse button is pressed and no DM tool is active, ray-march against all
SDF objects and select the closest hit via `FreeCADGui.Selection`. Do not consume the event so
FreeCAD's normal navigation still runs.

Insert the following block inside `eventFilter` after line 52
(after `self._middle_mouse_down = is_press`), still inside the outer `try`:

```python
            # SDF object selection on LMB press when no tool is active
            if event.type() == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                from core.dm_tool_manager import DMToolManager
                if not DMToolManager.get_instance().has_active_tool():
                    try:
                        view = FreeCADGui.ActiveDocument.ActiveView if FreeCADGui.ActiveDocument else None
                        if view:
                            from core.view_projector import ViewProjector
                            proj = ViewProjector(view)
                            sdf_result = proj.get_sdf_hit(
                                {"QtPosition": (event.pos().x(), event.pos().y())}
                            )
                            if sdf_result:
                                _, _, sdf_obj = sdf_result
                                FreeCADGui.Selection.clearSelection()
                                FreeCADGui.Selection.addSelection(sdf_obj)
                    except Exception as e:
                        dm_logger.debug(f"SDF LMB selection failed: {e}")
                    # Do NOT return True — let FreeCAD's navigation also handle this click
```

**Depends on:** H-002

---

## Agent Skills

| Skill | Purpose |
|-------|---------|
| `dm_sdf_hit_test` | Sphere-tracing algorithm, AABB slab test, `ray_march` signature, `get_sdf_hit` contract |
| `dm_sdf_primitive_pattern` | How `FRepField.evaluate()` and `bounding_box()` work |
| `dm_event_pipeline` | How Coin3D SoEvent and Qt eventFilter interact |
