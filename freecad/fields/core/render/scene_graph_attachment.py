# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Coin3D scene-graph attachment for FldSceneVoxelRenderer.

Extracted from FldSceneVoxelRenderer unchanged (SV-001, SV-002). Every function takes the
renderer instance as `r` and reads/writes its attributes directly -- this is what
lets test_renderer_decomposition.py keep monkeypatching
`r._active_scene_graph` per-instance after the logic moves here.
"""
import ctypes
import FreeCAD
import FreeCADGui

from freecad.fields.core import fld_logger
from freecad.fields.core.render.render_state import _RS_QUALITY
from freecad.fields.core.render.renderer_observers import (
    _FldNavFilter, _FldSelectionObserver, _FldDocumentObserver
)
from freecad.fields.core.render.scene_shader_builder import CompiledScene

try:
    from PySide.QtWidgets import QApplication
except ImportError:
    from PySide.QtWidgets import QApplication


def install_doc_observer(r):
    """Hook App's deletion slots. Once per singleton, not per view.

    This deliberately does not live in `_attach`: field lifetime is a document
    concern, not a view one, so it must not be tied to whether a view happens to
    have the switch in it.
    """
    if r._doc_observer is not None:
        return
    add = getattr(FreeCAD, "addDocumentObserver", None)
    if add is None:
        return
    try:
        r._doc_observer = _FldDocumentObserver(r)
        add(r._doc_observer)
    except Exception as e:
        r._doc_observer = None
        fld_logger.render_debug(f"FldSceneVoxelRenderer: document observer install failed: {e}")


def active_scene_graph():
    """The active view's Coin scene graph, or None when there is no view."""
    try:
        view = getattr(getattr(FreeCADGui, "ActiveDocument", None), "ActiveView", None)
        return view.getSceneGraph() if view else None
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer: active scene graph unavailable: {e}")
        return None


def live_scene_graphs():
    """Every open 3D view's scene graph. Pivy wrappers compare by node with `==`,
    not by identity -- two `getSceneGraph()` calls on one view give `is` False and
    `==` True (verified live), so membership must be tested with `==`.
    """
    graphs = []
    try:
        for name in FreeCAD.listDocuments():
            gdoc = FreeCADGui.getDocument(name)
            for view in (gdoc.mdiViewsOfType("Gui::View3DInventor") or []):
                sg = view.getSceneGraph()
                if sg is not None:
                    graphs.append(sg)
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer._live_scene_graphs failed: {e}")
    return graphs


def prune_attached_graphs(r):
    """Forget graphs no open view owns any more.

    `_attached_graphs` is a strong reference, and a Coin graph is ref-counted -- so
    without this, closing a document would leave its whole scene graph, and every
    geometry node under it, alive for the rest of the session just because the
    renderer's switch had once been added to it.
    """
    live = r._live_scene_graphs()
    if not live:
        return
    r._attached_graphs = [g for g in r._attached_graphs
                          if any(g == l for l in live)]


def switch_in(r, sg):
    """True when the switch is already a child of sg. Ask, do not remember."""
    try:
        return sg.findChild(r._switch) >= 0
    except Exception as e:
        fld_logger.render_debug(f"FldSceneVoxelRenderer._switch_in failed: {e}")
        return False


def attach(r):
    """Put the switch into the *active* view's scene graph if it is not there.

    RA-013. This used to early-out on a single `self._attached` bool. FreeCAD is
    multi-document and multi-view, and the switch lives in a view's scene graph --
    so when the document it had attached to was closed, the graph went with it,
    the flag stayed True, and no later document ever got the switch. Fields geometry
    then stopped drawing in every document, with no error and nothing in the log.

    Measured 2026-08-26: `_attached` True, the switch absent from the active
    view's graph, and `_volume_dirty` never clearing across repeated `redraw()`
    calls because the render callback was not in the tree to run; forcing a
    re-attach made the very next redraw bake. It is also the likeliest reason
    CW-012's 2026-08-25 sweep read `Bakes in tick = 0` at every N -- the bench
    opens its own document.

    A bool cannot answer "is my switch in THIS graph", so the graph is asked
    directly and the answer is not cached. Calling this repeatedly is cheap and
    idempotent, which is why `update_field` now calls it unconditionally.
    """
    r._prune_attached_graphs()
    sg = r._active_scene_graph()
    if sg is not None and not r._switch_in(sg):
        try:
            sg.addChild(r._switch)
            r._attached_graphs.append(sg)
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._attach addChild failed: {e}")
    if r._nav_filter is None and getattr(QApplication, "instance", None) and QApplication.instance():
        r._nav_filter = _FldNavFilter(r)
        QApplication.instance().installEventFilter(r._nav_filter)
    if r._sel_observer is None and getattr(FreeCADGui, "Selection", None):
        try:
            r._sel_observer = _FldSelectionObserver(r)
            FreeCADGui.Selection.addObserver(r._sel_observer)
            r._selected_labels = {f"{obj.Document.Name}.{obj.Name}" for obj in FreeCADGui.Selection.getSelection()}
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._attach selection observer failed: {e}")


def detach(r):
    # Every graph the switch was added to, not just the active one -- the whole
    # point of RA-013 is that "the active view" is not where it necessarily is.
    # No early-out on an attachment flag: the GL resources torn down below can
    # exist whether or not the switch is in a tree.
    for sg in r._attached_graphs:
        try:
            sg.removeChild(r._switch)
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeChild failed: {e}")
    r._attached_graphs = []

    # Destroy multi-pass GL resources (best-effort; called outside GL context)
    for prog in (r._prog_ssao, r._prog_blur, r._prog_comp):
        if prog is not None:
            try:
                prog.destroy()
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach prog.destroy failed: {e}")
    r._prog_ssao = r._prog_blur = r._prog_comp = None

    # Every compute program lives in the cache, including the active one, so
    # destroying the cache covers _prog_compute too — destroying it separately
    # as well would be a double free.
    if getattr(r, "_prog_cache", None) is not None:
        r._prog_cache.destroy_all()

    if getattr(r, "_cage_ssbo_cache", None) and getattr(r._gl_buf, "glDeleteBuffers", None) is not None:
        try:
            for entry in r._cage_ssbo_cache.values():
                if entry and entry.get("buf_id"):
                    bid = ctypes.c_uint(entry["buf_id"])
                    r._gl_buf.glDeleteBuffers(1, ctypes.byref(bid))
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._detach delete SSBOs failed: {e}")
        r._cage_ssbo_cache = {}

    if getattr(r, "_tex3d_cache", None):
        for entry in r._tex3d_cache.values():
            if entry and entry.get("tex"):
                try:
                    entry["tex"].destroy()
                except Exception as e:
                    fld_logger.render_debug(f"FldSceneVoxelRenderer._detach: failed to destroy 3D texture: {e}")
        r._tex3d_cache = {}

    if getattr(r, "_scene_volume", None) is not None:
        try:
            r._scene_volume.destroy()
        except Exception as e:
            fld_logger.error(f"SceneVoxel: scene volume destroy failed: {e}")
        r._scene_volume = None

    for fbo in (r._gbuf_fbo, r._ssao_fbo, r._blur_fbo):
        if fbo is not None:
            try:
                fbo.destroy()
            except Exception as e:
                fld_logger.render_debug(f"FldSceneVoxelRenderer._detach fbo.destroy failed: {e}")
    r._gbuf_fbo = r._ssao_fbo = r._blur_fbo = None

    if r._noise_tex_id or r._depth_img_tex:
        try:
            from freecad.fields.core.gl.gl_texture3d import _loader
            glDeleteTextures = _loader.get("glDeleteTextures",
                [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)], None)
            if r._noise_tex_id and glDeleteTextures:
                arr = (ctypes.c_uint * 1)(r._noise_tex_id)
                glDeleteTextures(1, arr)
                r._noise_tex_id = 0
            if r._depth_img_tex and glDeleteTextures:
                arr = (ctypes.c_uint * 1)(r._depth_img_tex)
                glDeleteTextures(1, arr)
                r._depth_img_tex = 0
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._detach glDeleteTextures failed: {e}")

    r._scene = CompiledScene()
    r._active_uniforms = {}
    r._selected_labels.clear()
    r._vp_size = (0, 0)

    if r._sel_observer is not None and getattr(FreeCADGui, "Selection", None):
        try:
            FreeCADGui.Selection.removeObserver(r._sel_observer)
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeObserver failed: {e}")
        r._sel_observer = None

    if r._nav_filter is not None:
        try:
            QApplication.instance().removeEventFilter(r._nav_filter)
        except Exception as e:
            fld_logger.render_debug(f"FldSceneVoxelRenderer._detach removeEventFilter failed: {e}")
        r._nav_filter = None
    r._render_state = _RS_QUALITY
