# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Registry of SDF fields, managing active fields, dirty flags, and GLSL compilation cache.

Extracted from FldSceneVoxelRenderer (RS-011).
"""
import time
import FreeCAD
from freecad.fields.core import fld_logger


class SdfFieldRegistry:
    def __init__(self):
        self._fields = {}           # label -> (field, visible)
        self._compiled_fields = {}  # label -> (expr, ctx, bmin, bmax)
        self._dirty_fields = set()  # set of labels whose fields changed

    @property
    def fields(self):
        return self._fields

    @property
    def compiled_fields(self):
        return self._compiled_fields

    @property
    def dirty_fields(self):
        return self._dirty_fields

    def __len__(self):
        return len(self._fields)

    def __contains__(self, label):
        return label in self._fields

    def __iter__(self):
        return iter(self._fields)

    @staticmethod
    def _stamp(label, field):
        """Stamp the owning object's surface id onto a field entering the registry.

        This is the door, and it is the reason the stamp lives here rather than
        only in `get_sdf_field`: `FldObjectProxy.execute` and every tool's drag
        preview build a field and push it at `update_field` directly, never
        through `get_sdf_field`. A boolean recomposed by `execute` arrived here
        with surface_id 65535 and rendered in the global base colour with no
        FieldMeta row -- no body colour, no hatch, no selection outline.

        The label is "<DocName>.<ObjName>", so the object is right there. A label
        that names no object stamps nothing: there is no id to stamp, and the
        field is registered exactly as it was handed over.
        """
        from freecad.fields.core.render.field_appearance import FieldAppearance
        from freecad.fields.core.objects.fld_surface_id import stamp_surface_id
        stamp_surface_id(FieldAppearance._object_for(label), field)

    def register(self, label, field):
        fld_logger.render_debug(f"SdfFieldRegistry: Registering field '{label}'")
        self._stamp(label, field)
        self._fields[label] = (field, True)
        self._dirty_fields.add(label)

    def unregister(self, label):
        existed = label in self._fields
        if existed:
            fld_logger.render_debug(f"SdfFieldRegistry: Unregistering field '{label}'")
            self._fields.pop(label)
        self._compiled_fields.pop(label, None)
        self._dirty_fields.discard(label)
        return existed

    def set_visible(self, label, visible):
        if label in self._fields:
            field, _ = self._fields[label]
            self._fields[label] = (field, visible)

    def update_field(self, label, field):
        self._stamp(label, field)
        visible = self._fields.get(label, (None, True))[1]
        self._fields[label] = (field, visible)
        self._dirty_fields.add(label)

    def get(self, label, default=None):
        return self._fields.get(label, default)

    def mark_dirty(self, label):
        if label in self._fields:
            self._dirty_fields.add(label)

    def mark_all_dirty(self):
        if self._fields:
            self._dirty_fields.update(self._fields.keys())

    def gc_orphans(self):
        """Remove labels whose FreeCAD document object no longer exists. Returns removed labels."""
        if not self._fields:
            return []
        to_remove = []
        for label in list(self._fields.keys()):
            try:
                # maxsplit=1, matching FieldAppearance._object_for. Both halves
                # are `.Name`, which FreeCAD sanitizes to an identifier, so neither
                # can contain a dot -- but a bare split() would silently `continue`
                # here and unregister nothing if one ever did, and the two copies
                # disagreeing is what makes the next reader stop and check.
                parts = label.split(".", 1)
                if len(parts) != 2:
                    continue
                doc_name, obj_name = parts
                doc = FreeCAD.getDocument(doc_name)
                if not doc or not doc.getObject(obj_name):
                    to_remove.append(label)
            except Exception as e:
                fld_logger.render_debug(f"SdfFieldRegistry.gc_orphans check failed for '{label}': {e}")
        if to_remove:
            fld_logger.render_debug(f"SdfFieldRegistry: GC-ing orphaned fields: {to_remove}")
            for label in to_remove:
                self.unregister(label)
        return to_remove

    def compile_visible(self, appearance, bbox_checker=None):
        """Compile visible & dirty fields to GLSL.

        Returns (visible_compiled, analytical_data, compile_total_seconds).
        `visible_compiled` is `[(label, field), ...]`.
        """
        from freecad.fields.core.sdf.glsl_compiler import compile_field_to_glsl

        visible = [(label, f) for label, (f, vis) in self._fields.items() if vis and f is not None]
        if not visible:
            self._dirty_fields.clear()
            return [], [], 0.0

        analytical_data = []
        visible_compiled = []
        t_compile_total = 0.0

        for label, f in visible:
            if label in self._compiled_fields and label not in self._dirty_fields:
                expr, ctx, bmin, bmax = self._compiled_fields[label]
            else:
                t_comp_start = time.perf_counter()
                try:
                    expr, ctx = compile_field_to_glsl(f, prefix=label)
                    t_compile_total += (time.perf_counter() - t_comp_start)
                    try:
                        mn, mx = f.bounding_box()
                        bmin = FreeCAD.Vector(mn)
                        bmax = FreeCAD.Vector(mx)
                        from freecad.fields.core.objects.fld_object import get_max_sdf_render_size
                        limit = get_max_sdf_render_size()
                        for attr in ['x', 'y', 'z']:
                            mn_val = getattr(bmin, attr)
                            mx_val = getattr(bmax, attr)
                            size = mx_val - mn_val
                            if size > limit:
                                mid = (mn_val + mx_val) * 0.5
                                setattr(bmin, attr, mid - limit * 0.5)
                                setattr(bmax, attr, mid + limit * 0.5)

                        if bbox_checker is not None:
                            try:
                                from freecad.fields.core.fld_settings import get_render_debug_mode
                                if get_render_debug_mode():
                                    bbox_checker(f, label, bmin, bmax)
                            except Exception as e:
                                fld_logger.debug(
                                    f"compile_visible: render-debug bbox checker "
                                    f"failed for '{label}' ({e}); compilation "
                                    f"continues unaffected."
                                )
                    except Exception:
                        bmin, bmax = FreeCAD.Vector(-10, -10, -10), FreeCAD.Vector(10, 10, 10)
                    self._compiled_fields[label] = (expr, ctx, bmin, bmax)
                except NotImplementedError:
                    fld_logger.warn(
                        f"SceneVoxel: '{label}' ({type(f).__name__}) has no to_glsl() — skipped."
                    )
                    continue
                except Exception as e:
                    fld_logger.error(f"SceneVoxel: GLSL compile failed for '{label}': {e}")
                    continue

            analytical_data.append({
                "expr": expr,
                "ctx": ctx,
                "bbox_min": bmin,
                "bbox_max": bmax,
                "is_subtractive": appearance.is_subtractive(label),
                "vis_alpha": appearance.vis_alpha(label),
                "shape_color": appearance.shape_color(label),
                "selection_color": appearance.selection_color(label),
                "specular": appearance.specular_shininess(label),
                "field": f,
                "label": label,
            })
            visible_compiled.append((label, f))

        self._dirty_fields.clear()
        return visible_compiled, analytical_data, t_compile_total
