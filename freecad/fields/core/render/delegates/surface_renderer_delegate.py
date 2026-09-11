# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/delegates/surface_renderer_delegate.py

Rendering delegate for the semi-transparent "infinite extension" preview
overlay shown on parametric surface patches with ShowExtension enabled.
"""
from freecad.fields.core.render.delegates import add_to_scene
try:
    from pivy import coin
except ImportError:
    coin = None


class SurfaceRendererDelegate:
    def __init__(self):
        self._extension_sep = None
        self._extension_mat = None
        self._extension_coords = None
        self._extension_face_set = None
        self._extension_draw_style = None

    def setup_extension_overlay(self, renderer):
        if not coin: return
        if self._extension_sep:
            return

        self._extension_sep = coin.SoSeparator()

        # Premium semi-transparent purple material for infinite extension
        self._extension_mat = coin.SoMaterial()
        self._extension_mat.diffuseColor.setValue(0.5, 0.2, 0.9)
        self._extension_mat.transparency.setValue(0.6)
        self._extension_sep.addChild(self._extension_mat)

        self._extension_coords = coin.SoCoordinate3()
        self._extension_sep.addChild(self._extension_coords)

        self._extension_face_set = coin.SoIndexedFaceSet()
        self._extension_sep.addChild(self._extension_face_set)

        self._extension_draw_style = coin.SoDrawStyle()
        self._extension_draw_style.style = coin.SoDrawStyle.FILLED
        self._extension_sep.addChild(self._extension_draw_style)

        add_to_scene(renderer, self._extension_sep)

    def update_extension_overlay(self, renderer, fp):
        if not coin: return
        if not self._extension_coords:
            self.setup_extension_overlay(renderer)
        if not self._extension_coords:
            return

        show_ext = getattr(fp, "ShowExtension", False)
        proxy = getattr(fp, "Proxy", None)
        untrimmed = getattr(proxy, "untrimmed_shape", None) if proxy else None

        if not show_ext or not untrimmed or untrimmed.isNull():
            self._extension_coords.point.setNum(0)
            self._extension_face_set.coordIndex.setNum(0)
            return

        try:
            # Tessellate surface with tolerance 0.5 for preview smoothness/speed
            verts, faces = untrimmed.tessellate(0.5)

            coin_verts = [coin.SbVec3f(v.x, v.y, v.z) for v in verts]
            self._extension_coords.point.setNum(len(coin_verts))
            if coin_verts:
                self._extension_coords.point.setValues(0, coin_verts)

            flat_faces = []
            for f in faces:
                flat_faces.extend(f)
                flat_faces.append(-1)

            self._extension_face_set.coordIndex.setNum(len(flat_faces))
            if flat_faces:
                self._extension_face_set.coordIndex.setValues(0, flat_faces)
        except Exception as e:
            from freecad.fields.core import fld_logger
            fld_logger.warn(f"SurfaceRendererDelegate: failed to tessellate untrimmed surface: {e}")
            self._extension_coords.point.setNum(0)
            self._extension_face_set.coordIndex.setNum(0)
