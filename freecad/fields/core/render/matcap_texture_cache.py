# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""GL_TEXTURE_2D_ARRAY holding the preset matcaps, one layer per preset.

One sampler2DArray and a per-field layer index replace the eight `u_matcap_N`
samplers the removed build used. The same layer is read either as a 2D matcap
(uv from the view-space normal) or as a 1D colour ramp (uv from curvature).

Three things here are load-bearing, each one a cause of the removed build never
rendering correctly:

* `GL_SRGB8_ALPHA8`: the composite pass ends in `pow(x, 1/2.2)`, so a PNG sampled
  as linear is gamma-corrected twice and comes out milky.
* `GL_CLAMP_TO_EDGE`: at the silhouette the lookup UV reaches the border, and
  `GL_REPEAT` would wrap the opposite edge in as a halo ring.
* No mipmaps, no `textureLod`: nothing here ever generated a mip chain.

The upload is lazy and happens inside a GL callback (`upload_pending`) -- there is
no current context at import time.
"""
import ctypes
import os

import numpy as np

from freecad.fields.core import fld_logger
from freecad.fields.core.render import matcap_bake
from freecad.fields.core.render.matcap_bake import LIGHTING_NAMES, MATERIAL_NAMES, DATA_NAMES

GL_TEXTURE_2D_ARRAY = 0x8C1A
GL_SRGB8_ALPHA8     = 0x8C43

GL_RGBA               = 0x1908
GL_UNSIGNED_BYTE      = 0x1401
GL_LINEAR             = 0x2601
GL_CLAMP_TO_EDGE      = 0x812F
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_TEXTURE_WRAP_S     = 0x2802
GL_TEXTURE_WRAP_T     = 0x2803
GL_UNPACK_ALIGNMENT   = 0x0CF5
GL_MAX_ARRAY_TEXTURE_LAYERS = 0x88FF

#: Every layer is exactly this square size; a preset that is not is rejected
#: rather than rescaled silently.
MATCAP_SIZE = 256

#: Layer order: lighting, then materials, then data layers, in `matcap_bake` table
#: order. `Resources/matcaps/generate_presets.py` writes `<name>.png` for each.
MATCAP_NAMES = [*LIGHTING_NAMES, *MATERIAL_NAMES, *DATA_NAMES]
MATCAP_NAMES_WITH_NONE = ["None"] + MATCAP_NAMES

#: Layers that are readouts, not lighting. The packer flags them in
#: `FieldMeta.matcap.y` and forces AO bypass; the hatch skips flagged fields.
DATA_LAYERS = ("normals", "zebra", "curvature")
#: Layers read as a 1D ramp by curvature (FieldMeta.matcap.z = 1), not by normal
#: (z = 0, the 2D lookup by view-space normal). The source is a function of the layer.
RAMP_LAYERS = ("curvature",)


def matcap_dir():
    from freecad.fields import RESOURCES_DIR
    return os.path.join(RESOURCES_DIR, "matcaps")


def load_layers(directory=None, names=MATCAP_NAMES):
    """Read the preset PNGs into one (n, SIZE, SIZE, 4) uint8 array.

    Returns (array, ok): a missing or wrongly sized preset leaves its layer
    transparent black and is reported once, so the layer indices stay stable.
    Rows are flipped so image-top lands at v = 1 and +Y reads up in the lookup.
    """
    from PySide import QtGui

    directory = directory or matcap_dir()
    layers = np.zeros((len(names), MATCAP_SIZE, MATCAP_SIZE, 4), dtype=np.uint8)
    ok = True
    for i, name in enumerate(names):
        path = os.path.join(directory, name + ".png")
        img = QtGui.QImage(path)
        if img.isNull():
            fld_logger.error(f"MatcapTextureCache: cannot read {path}")
            ok = False
            continue
        if img.width() != MATCAP_SIZE or img.height() != MATCAP_SIZE:
            fld_logger.error(
                f"MatcapTextureCache: {path} is {img.width()}x{img.height()}, "
                f"expected {MATCAP_SIZE}x{MATCAP_SIZE}; layer left empty")
            ok = False
            continue
        img = img.convertToFormat(QtGui.QImage.Format_RGBA8888)
        bpl = img.bytesPerLine()
        raw = np.frombuffer(img.constBits(), dtype=np.uint8,
                            count=bpl * MATCAP_SIZE).reshape(MATCAP_SIZE, bpl)
        layers[i] = raw[:, :MATCAP_SIZE * 4].reshape(MATCAP_SIZE, MATCAP_SIZE, 4)[::-1]
    return layers, ok


class MatcapTextureCache:
    """The matcap array: the built-ins plus every open document's material variants.

    Layer order: built-ins in `MATCAP_NAMES` order, then documents in insertion
    order, then each document's variants in recipe order. The array is re-specified
    on the same texture id when that set changes, and a document's variants go when
    the document closes.
    """

    def __init__(self, names=MATCAP_NAMES):
        self._names = list(names)
        self._tex_id = 0
        self._uploaded = False
        self._failed = False
        self._dirty = True
        self._builtin = None             # (n, SIZE, SIZE, 4) uint8, kept in CPU memory
        self._docs = {}                  # doc name -> [(variant name, flipped array)]
        self._recipes = {}               # doc name -> tuple of (name, base, colour tuple, metallic, roughness)
        self._baked = {}                 # (base, colour tuple) -> flipped array
        self._max_layers = None
        self._dropped = set()            # (doc name, variant name) that did not fit
        self.depth = 0                   # layers in the uploaded array
        #: Raw Meta string each document's recipes were last loaded from
        #: (matcap_variants.ensure_loaded compares against it).
        self.meta_raw = {}

    @property
    def tex_id(self):
        return self._tex_id

    # ---- variants -----------------------------------------------------------

    def set_document_variants(self, doc_name, recipes):
        """Bake and register a document's recipes. Idempotent: identical recipes
        leave the array clean."""
        key = tuple((r["name"], r["base"], tuple(r["color"]), r["metallic"], r["roughness"])
                    for r in recipes)
        if self._recipes.get(doc_name, ()) == key:
            return
        if not key:
            self.drop_document(doc_name)
            return
        layers = []
        for name, base, colour, metallic, roughness in key:
            bk = (base, colour, metallic, roughness)
            if bk not in self._baked:
                mat = matcap_bake.variant_of(matcap_bake.MATERIALS[base], colour, metallic,
                                             roughness)
                self._baked[bk] = np.ascontiguousarray(matcap_bake.render_material(mat)[::-1])
            layers.append((name, self._baked[bk]))
        self._docs[doc_name] = layers
        self._recipes[doc_name] = key
        self._dirty = True

    def drop_document(self, doc_name):
        """Forget a closed document's variants."""
        self.meta_raw.pop(doc_name, None)
        if self._docs.pop(doc_name, None) is not None:
            self._recipes.pop(doc_name, None)
            self._dirty = True

    def layer_of(self, doc_name, name):
        """Name -> layer index: a built-in first (a variant name can never equal one),
        then `(doc_name, name)`; -1 for an unknown name, "None" or a variant that
        did not fit the driver's array limit."""
        if name in self._names:
            return self._names.index(name)
        if (doc_name, name) in self._dropped:
            return -1
        i = len(self._names)
        for d, layers in self._docs.items():
            for vname, _arr in layers:
                if d == doc_name and vname == name:
                    return i
                i += 1
        return -1

    def variant_images(self, doc_name):
        """[(name, (SIZE, SIZE, 4) uint8 in image orientation)] for the dialog."""
        return [(n, np.ascontiguousarray(a[::-1])) for n, a in self._docs.get(doc_name, [])]

    def _stack(self):
        """The (n, SIZE, SIZE, 4) array to upload, truncated to the driver's limit."""
        if self._builtin is None:
            self._builtin, _ok = load_layers(names=self._names)
        flat = [(d, n, a) for d, layers in self._docs.items() for n, a in layers]
        room = max(0, (self._max_layers or 10 ** 9) - len(self._names))
        dropped = flat[room:]
        self._dropped = {(d, n) for d, n, _a in dropped}
        if dropped:
            fld_logger.error(
                f"MatcapTextureCache: array limit {self._max_layers} reached; "
                f"dropped variants: {', '.join(n for _d, n, _a in dropped)}")
        kept = [a for _d, _n, a in flat[:room]]
        return np.concatenate([self._builtin] + [a[None] for a in kept]) if kept else self._builtin

    # ---- GL -----------------------------------------------------------------

    def upload_pending(self):
        """Create the array, or re-specify it when the layer set changed.

        Must run inside a GL context. The texture id is kept across re-specification.
        """
        if (self._uploaded and not self._dirty) or self._failed:
            return
        from freecad.fields.core.gl.gl_texture3d import _loader

        try:
            glGenTextures = _loader.get("glGenTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            glBindTexture = _loader.get("glBindTexture", [ctypes.c_uint, ctypes.c_uint], None)
            glTexImage3D = _loader.get("glTexImage3D", [
                ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p], None)
            glTexSubImage3D = _loader.get("glTexSubImage3D", [
                ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                ctypes.c_void_p], None)
            glTexParameteri = _loader.get("glTexParameteri",
                [ctypes.c_uint, ctypes.c_uint, ctypes.c_int], None)
            glPixelStorei = _loader.get("glPixelStorei", [ctypes.c_uint, ctypes.c_int], None)

            if self._max_layers is None:
                glGetIntegerv = _loader.get("glGetIntegerv",
                    [ctypes.c_uint, ctypes.POINTER(ctypes.c_int)], None)
                v = ctypes.c_int(0)
                glGetIntegerv(GL_MAX_ARRAY_TEXTURE_LAYERS, ctypes.byref(v))
                self._max_layers = v.value or 256
            stack = self._stack()

            if not self._tex_id:
                tex = ctypes.c_uint(0)
                glGenTextures(1, ctypes.byref(tex))
                self._tex_id = tex.value
            glBindTexture(GL_TEXTURE_2D_ARRAY, self._tex_id)
            glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
            n = len(stack)
            glTexImage3D(GL_TEXTURE_2D_ARRAY, 0, GL_SRGB8_ALPHA8, MATCAP_SIZE, MATCAP_SIZE, n,
                         0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            for i in range(n):
                data = np.ascontiguousarray(stack[i])
                glTexSubImage3D(GL_TEXTURE_2D_ARRAY, 0, 0, 0, i, MATCAP_SIZE, MATCAP_SIZE, 1,
                                GL_RGBA, GL_UNSIGNED_BYTE, data.ctypes.data)
            glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glBindTexture(GL_TEXTURE_2D_ARRAY, 0)
            self.depth = n
            self._uploaded = True
            self._dirty = False
        except Exception as e:
            self._failed = True
            fld_logger.error(f"MatcapTextureCache: upload failed: {e}")

    def forget_gl(self):
        """Drop the texture id without deleting it (the GL context was replaced).
        The CPU-side layers are kept, so the next upload rebuilds the array."""
        self._tex_id = 0
        self._uploaded = False
        self._failed = False
        self._dirty = True
        self._max_layers = None
        self.depth = 0

    def destroy_all(self):
        if self._tex_id:
            from freecad.fields.core.gl.gl_texture3d import _loader
            try:
                glDeleteTextures = _loader.get("glDeleteTextures",
                    [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
                glDeleteTextures(1, (ctypes.c_uint * 1)(self._tex_id))
            except Exception as e:
                fld_logger.warn(f"MatcapTextureCache.destroy_all failed: {e}")
        self.forget_gl()
