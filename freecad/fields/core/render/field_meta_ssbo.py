# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/field_meta_ssbo.py

The FieldMeta table: one row per surface id carrying that surface's box,
appearance and selection state, uploaded as an SSBO at binding point 3.

Both the march (voxel_march_shader) and the composite pass (_FRAG_COMP) read
these rows, and they read the same upload -- SSBO binding points are context
state, not program state, so the composite never re-uploads. That is also why
the outline colour is per object and there is no palette uniform or lookup
texture beside this buffer: a runtime-indexed uniform ARRAY is the one thing
that must not come back (CP-014, NVIDIA register spill).

Row layout, 20 floats:

    [ 0: 3]  bmin.xyz, pad
    [ 4: 7]  bmax.xyz, vis_alpha
    [ 8:11]  diffuse.rgb, is_subtractive
    [12:15]  specular.rgba
    [16:19]  selected, selection_colour.rgb
"""
import ctypes

import numpy as np

from freecad.fields.core import fld_logger

GL_SHADER_STORAGE_BUFFER = 0x90D2
GL_DYNAMIC_DRAW          = 0x88E8

#: Floats per row. Must match the FieldMeta struct in both shaders.
ROW_FLOATS = 20

#: The sentinel a field's root carries when it has no surface id of its own.
NO_SURFACE_ID = 65535

#: SSBO binding point, shared by the march and the composite pass.
BINDING_POINT = 3


def collect_surface_ids(field_obj):
    """Every surface id one field owns, as a set.

    A field may report ids explicitly through `collect_surface_ids()`, carry a
    base id plus a count, or both -- a boolean tree does the first, a primitive
    the second, and a cage with children does both.
    """
    sids = field_obj.collect_surface_ids() if hasattr(field_obj, "collect_surface_ids") else set()
    sid = getattr(field_obj, "surface_id", NO_SURFACE_ID)
    if sid < NO_SURFACE_ID:
        sids.update(range(sid, sid + field_obj.surface_count()))
    return sids


class FieldMetaBuffer:
    """Owns the GL buffer holding the FieldMeta table and decides when to repack.

    `dirty` is set by anything that changes a row's contents -- a selection
    change, an appearance refresh, a rebuild. The table is also repacked
    whenever it has outgrown the allocation, because a grown table cannot be
    written with glBufferSubData.
    """

    def __init__(self):
        self.buf_id = 0
        self.capacity = 0
        self.dirty = True
        #: Row count uploaded most recently. The composite pass bounds-checks
        #: g-buffer ids against it, so it is refreshed every frame -- delete a
        #: field and the table shrinks while a clean volume keeps its ids, and
        #: a stale id must then read nothing rather than a stale row.
        self.count = 0
        #: Labels already reported as having no id, so the report view gets one
        #: line per label rather than one per frame.
        self._no_id_reported = set()

    def table_size(self, fields):
        """One past the highest surface id in `fields`, and a report for any
        field that has no id at all.

        The table is keyed by surface id and by nothing else. A field whose root
        carries no id has no row and is not drawn with its own appearance --
        there is no second key to fall back to, because a fallback here is what
        put two different numbers into one table and made a solid render with
        another field's colour.
        """
        max_sid = -1
        for cf in fields:
            sids = collect_surface_ids(cf.field_obj)
            if not sids:
                if cf.label not in self._no_id_reported:
                    self._no_id_reported.add(cf.label)
                    fld_logger.error(
                        f"FieldMeta: {cf.label} has no surface id; "
                        f"its appearance cannot be uploaded")
                continue
            self._no_id_reported.discard(cf.label)
            max_sid = max(max_sid, max(sids))
        return max_sid + 1

    @staticmethod
    def pack(fields, selected_labels, table_size):
        """The (table_size, ROW_FLOATS) float32 array to upload.

        Rows for ids no field claims stay zero, which reads as a black,
        fully transparent, unselected surface -- nothing marches into them.
        """
        meta_arr = np.zeros((table_size, ROW_FLOATS), dtype=np.float32)
        for cf in fields:
            row = [
                cf.bbox_min.x, cf.bbox_min.y, cf.bbox_min.z, 0.0,
                cf.bbox_max.x, cf.bbox_max.y, cf.bbox_max.z, float(cf.vis_alpha),
                cf.shape_color[0], cf.shape_color[1], cf.shape_color[2], 1.0 if cf.is_subtractive else 0.0,
                cf.specular[0], cf.specular[1], cf.specular[2], cf.specular[3],
                # flags: .x selected, .yzw this object's own outline colour,
                # read by the composite pass.
                1.0 if cf.label in selected_labels else 0.0,
                cf.selection_color[0], cf.selection_color[1], cf.selection_color[2],
            ]
            for s_i in collect_surface_ids(cf.field_obj):
                if 0 <= s_i < table_size:
                    meta_arr[s_i] = row
        return meta_arr

    def upload_and_bind(self, gl, fields, selected_labels):
        """Repack if needed, then bind the buffer at BINDING_POINT.

        `gl` supplies glGenBuffers / glBindBuffer / glBufferData /
        glBindBufferBase as attributes, and glBufferSubData is resolved here.
        Returns the selection snapshot that was baked into this upload, or None
        when nothing was repacked -- Pass 4's `u_any_selected` gate must reflect
        the state the march saw, not the state as of the composite frame, and
        the two can differ by one selection event (VR-030).
        """
        from freecad.fields.core.gl.gl_texture3d import _loader

        if not self.buf_id:
            buf_id_val = ctypes.c_uint(0)
            gl.glGenBuffers(1, ctypes.byref(buf_id_val))
            self.buf_id = buf_id_val.value
            self.capacity = 0

        table_size = self.table_size(fields)
        self.count = table_size

        snapshot = None
        if table_size > 0 and (self.dirty or table_size > self.capacity):
            meta_arr = self.pack(fields, selected_labels, table_size)

            gl.glBindBuffer(GL_SHADER_STORAGE_BUFFER, self.buf_id)
            glBufferSubData = _loader.get("glBufferSubData",
                [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_void_p], None)
            if table_size <= self.capacity and glBufferSubData:
                glBufferSubData(GL_SHADER_STORAGE_BUFFER, 0, meta_arr.nbytes, meta_arr.ctypes.data)
            else:
                gl.glBufferData(GL_SHADER_STORAGE_BUFFER, meta_arr.nbytes,
                                meta_arr.ctypes.data, GL_DYNAMIC_DRAW)
                self.capacity = table_size
            self.dirty = False
            snapshot = frozenset(selected_labels)

        gl.glBindBufferBase(GL_SHADER_STORAGE_BUFFER, BINDING_POINT, self.buf_id)
        return snapshot

    def forget_gl(self):
        """Drop the buffer id without deleting it.

        Called when the GL context is re-initialised, where the id no longer
        names anything this process can delete. It does leak the old buffer on
        the driver side if the context is in fact still alive -- that is the
        behaviour this replaced, kept deliberately rather than changed as a
        side effect of moving the code.
        """
        self.buf_id = 0
        self.capacity = 0
