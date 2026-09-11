# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/objects/fld_object.py

Core Fields document object. Uses Part::FeaturePython for native
NURBS/BRep rendering.

Factory
-------
    obj = create_fld_object("Box", shape_type="box", params={"length": 10, ...})

The Proxy.execute() reads parametric properties and calls build_shape() to 
generate the native BRep geometry.
"""

import FreeCAD
import FreeCADGui
import Part
try:
    from pivy import coin
except ImportError:
    coin = None

from freecad.fields.core import fld_logger

# ─────────────────────────────────────────────────────────────────────────────
# Fields Settings helpers (re-exported from freecad.fields.core.fld_settings for backwards compat)
# ─────────────────────────────────────────────────────────────────────────────

from freecad.fields.core.fld_settings import (  # noqa: F401
    _PARAM_PATH, get_show_wireframe, set_show_wireframe,
    get_line_width, set_line_width, get_point_size, set_point_size,
    get_picking_radius, set_picking_radius, get_max_bounds, set_max_bounds,
    get_perf_profiler_enabled, set_perf_profiler_enabled,
    get_render_debug_mode, set_render_debug_mode,
    get_near_clip_distance, set_near_clip_distance,
    get_ray_march_cell_size, set_ray_march_cell_size,
    get_show_cage_curves, set_show_cage_curves,
    get_max_sdf_render_size, set_max_sdf_render_size,
    get_heightmap_resolution, set_heightmap_resolution,
    apply_near_clip_override, get_interactive_throttle_interval,
    set_interactive_throttle_interval, get_drag_tick_rate, set_drag_tick_rate,
    get_sdf_selection_outline_size, set_sdf_selection_outline_size,
    get_hatch_size, set_hatch_size,
    get_hatch_color, set_hatch_color,
    get_hatch_strength, set_hatch_strength,
    RENDER_QUALITY_PRESETS, get_render_quality, set_render_quality,
    get_render_quality_preset, get_interactive_resolution_scaling,
    get_cage_patch_type, set_cage_patch_type,
    get_simplify_cage_drag, set_simplify_cage_drag,
    get_sdf_warp_resolution, set_sdf_warp_resolution,
    get_voxel_grid_resolution, set_voxel_grid_resolution,
    get_voxel_band_voxels, set_voxel_band_voxels,
    get_gpu_field_eval, set_gpu_field_eval,
    get_model_tolerance, set_model_tolerance,
)





# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_fld_object(name, shape_type, params=None, placement=None):
    """
    Create a Fields object (Part::FeaturePython).
    """
    from freecad.fields.core import fld_logger
    from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy
    from freecad.fields.core.objects.fld_view_provider import FldViewProvider

    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument()

    try:
        # fld_logger.debug(f"create_fld_object: name={name}, type={shape_type}, has_placement={placement is not None}")
        obj = doc.addObject("Part::FeaturePython", name)
        FldObjectProxy(obj, shape_type, params, placement=placement)

        if FreeCAD.GuiUp:
            FldViewProvider(obj.ViewObject)
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                try:
                    obj.ViewObject.Visibility = True
                except Exception as e:
                    fld_logger.debug(f"create_fld_object: Failed to set visibility for {name}: {e}")
        

        # Marked dirty, not recomputed. The recompute belongs to the caller --
        # see finalize_new_object() for why (IF-016).
        obj.touch()

        # Appearance that does not need a computed Shape.
        if hasattr(obj, "ViewObject") and obj.ViewObject:
            # Orange for every Fields object regardless of Group. Subtractive state is
            # shown by the crosshatch (see voxel_march_shader.py), not by hue, so
            # this is a default the user is free to overwrite and nothing writes
            # ShapeColor again after creation.
            from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
            obj.ViewObject.ShapeColor = DEFAULT_ADDITIVE_COLOR
            obj.ViewObject.LineWidth = get_line_width()
            obj.ViewObject.PointSize = get_point_size()

        return obj
        
    except Exception:
        from freecad.fields.core import fld_logger
        fld_logger.exception(f"create_fld_object FAILED for {name}")
        return None


def apply_display_tolerance(obj, max_sag_mm):
    """Draw `obj` to an absolute chord sag in millimetres instead of a size fraction.

    FreeCAD tessellates a shape for display using `ViewObject.Deviation`, which is a
    PERCENTAGE of the shape's bounding-box diagonal and defaults to 0.5. On a 120 mm
    slice that is 0.6 mm of allowed chord sag -- an order of magnitude coarser than
    anything this workbench fits, and coarse enough that a curve fitted to 0.05 mm is
    drawn as a polygon. The faceting is not merely ugly: the display breaks the
    polyline at every control point, because `FldCurve._build_from_points` knots the
    spline with multiplicity 3 there, so the drawn corners land exactly where a real
    tangent break would and there is no way to tell the two apart by looking.

    `AngularDeflection` (default 28.5 degrees) is the other half of the same budget
    and governs the tight-radius stretches the linear term is loose on, so both move
    together or the fix only works on the flat spans.

    Requires a computed Shape -- the bounding box is empty before the recompute --
    and does nothing without a GUI.

    Args:
        obj:         a Fields document object.
        max_sag_mm:  the largest chord error the drawn curve may have, in mm.
    """
    from freecad.fields.core import fld_logger
    vobj = getattr(obj, "ViewObject", None) if obj else None
    if not vobj or not FreeCAD.GuiUp:
        return
    try:
        diag = obj.Shape.BoundBox.DiagonalLength
    except Exception as e:
        fld_logger.debug(f"apply_display_tolerance: no bounding box for "
                        f"{getattr(obj, 'Label', '?')}: {e}")
        return
    if not (diag > 1e-6):
        return
    # Floors, not a plain conversion. Deviation is clamped at 0.0001 by FreeCAD
    # itself, and a contour with sub-millimetre features would otherwise ask for a
    # tessellation fine enough to cost more than the fit did.
    pct = max(100.0 * float(max_sag_mm) / diag, 0.005)
    try:
        vobj.Deviation = pct
        vobj.AngularDeflection = 5.0
    except Exception as e:
        fld_logger.debug(f"apply_display_tolerance: {getattr(obj, 'Label', '?')}: {e}")


def apply_shaded_display_mode(obj):
    """Put an sdf/surface object into "Shaded" display mode.

    Only works *after* a recompute. The enumeration comes from Part's view
    provider and stays empty while Shape is empty, which is why FldViewProvider
    does not attempt it either (see the timing note in fld_view_provider.py).
    """
    from freecad.fields.core import fld_logger
    vobj = getattr(obj, "ViewObject", None) if obj else None
    if not vobj or getattr(obj, "ShapeType", None) not in ("sdf", "surface"):
        return
    try:
        vobj.DisplayMode = "Shaded"
    except Exception as e:
        fld_logger.debug(f"apply_shaded_display_mode: {getattr(obj, 'Label', '?')}: {e}")


def finalize_new_object(obj):
    """Recompute, select, frame and shade a newly created object.

    create_fld_object() deliberately does none of this. It is reached from tool
    drag paths as well as from commands, and recomputing -- let alone calling
    updateGui() and zooming the camera -- inside an event callback is the
    re-entrancy hazard U4 exists to prevent (IF-016). Commands that create an
    object in Activated() call this straight afterwards; tools recompute on
    their own drag tick and call apply_shaded_display_mode() at commit.

    Synchronous on purpose: callers may hold an undo transaction, and deferring
    would let it close before the recompute ran.
    """
    from freecad.fields.core import fld_logger
    if obj is None or not getattr(obj, "Document", None):
        return
    obj.Document.recompute()

    if FreeCAD.GuiUp:
        try:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(obj)

            active_document = FreeCADGui.ActiveDocument
            active_view = getattr(active_document, "ActiveView", None) if active_document else None
            if not active_view:
                active_view = FreeCADGui.activeView()

            if active_view and hasattr(active_view, "viewSelection"):
                active_view.viewSelection()

            FreeCADGui.updateGui()
        except Exception as e:
            fld_logger.debug(
                f"finalize_new_object: Selection/View setup failed for "
                f"{getattr(obj, 'Label', '?')} ({type(e).__name__}): {e}")

    apply_shaded_display_mode(obj)

def refresh_all_fld_objects():
    """Update LineWidth and PointSize of all Fields objects in the active document."""
    doc = FreeCAD.activeDocument()
    if not doc:
        return
    
    lw = get_line_width()
    ps = get_point_size()
    show_wire = get_show_wireframe()
    
    for obj in doc.Objects:
        if hasattr(obj, "ShapeType") and obj.ViewObject:
            # Native FreeCAD properties
            obj.ViewObject.LineWidth = lw
            # Fields Proxy properties
            if hasattr(obj, "ShowWireframe"):
                obj.ShowWireframe = bool(show_wire)
                
            # Invalidate SdfField cache (e.g. for octree cache during patch type preference changes)
            doc_proxy = getattr(obj, "Proxy", None)
            if doc_proxy and hasattr(doc_proxy, "SdfField") and doc_proxy.SdfField:
                doc_proxy.SdfField.invalidate_cache()
                
            if hasattr(obj, "ShapeType") and obj.ShapeType == "sdf":
                obj.touch()

            proxy = getattr(obj.ViewObject, "Proxy", None)
            if proxy and hasattr(proxy, "on_prefs_changed"):
                proxy.on_prefs_changed()
            obj.ViewObject.PointSize = ps
    
    def _deferred_recompute(d=doc):
        if d:
            try:
                d.recompute()
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.debug(f"refresh_all_fld_objects recompute failed: {e}")
        if FreeCAD.GuiUp:
            FreeCADGui.updateGui()
    from PySide import QtCore
    QtCore.QTimer.singleShot(0, _deferred_recompute)



# ─────────────────────────────────────────────────────────────────────────────
# Backward-compat: FreeCAD's Part::FeaturePython persistence stores each
# Proxy's class by module path (e.g. "freecad.fields.core.objects.fld_object.FldObjectProxy")
# at save time, then does getattr(import_module(module), class_name) to
# restore it. FldObjectProxy/FldViewProvider used to live in this module; they
# now live in fld_object_proxy.py/fld_view_provider.py. This lazily resolves
# the old path so previously-saved documents and already-open sessions can
# still restore their proxies.
# ─────────────────────────────────────────────────────────────────────────────

def __getattr__(name):
    if name == "FldObjectProxy":
        from freecad.fields.core.objects.fld_object_proxy import FldObjectProxy
        return FldObjectProxy
    if name == "FldViewProvider":
        from freecad.fields.core.objects.fld_view_provider import FldViewProvider
        return FldViewProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
