# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Fields user preferences / parameter storage accessors.

Split out of core/objects/fld_object.py (ST-007). Centralizes all ParamGet
calls under `User parameter:BaseApp/Preferences/Mod/Fields`.
"""
import FreeCAD
import FreeCADGui
from freecad.fields.core import fld_logger

_PARAM_PATH = "User parameter:BaseApp/Preferences/Mod/Fields"
def _param_get():
    return FreeCAD.ParamGet(_PARAM_PATH)


def get_show_wireframe():
    """Return whether to show wireframe for NURBS objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ShowWireframe", False)

def set_show_wireframe(show):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ShowWireframe", bool(show))

def get_line_width():
    """Return the line width for Fields objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("LineWidth", 3.0)

def set_line_width(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("LineWidth", float(val))

def get_point_size():
    """Return the point size for Fields objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("PointSize", 6.0)

def set_point_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("PointSize", float(val))

def get_picking_radius():
    """Return the picking radius in mm."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("PickingRadius", 5.0)

def set_picking_radius(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("PickingRadius", float(val))

def get_max_bounds():
    """Return the maximum field bounds in mm."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("MaxBounds", 10000.0)

def set_max_bounds(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("MaxBounds", float(val))

def get_perf_profiler_enabled():
    """Return whether the performance profiler is enabled."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnablePerfProfiler", False)

def set_perf_profiler_enabled(val):
    try: # ParamGet might fail in some contexts
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnablePerfProfiler", bool(val))
    except Exception as e:
        fld_logger.debug(f"set_perf_profiler_enabled failed: {e}")

def get_render_debug_mode():
    """Return whether render debug mode is enabled."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("RenderDebugMode", False)

def set_render_debug_mode(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("RenderDebugMode", bool(val))


def get_near_clip_distance():
    """Return the near clip distance override in mm (0 = auto)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("NearClipDistance", 0.0)

def set_near_clip_distance(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("NearClipDistance", float(val))

def get_ray_march_cell_size():
    """Return the SDF baking cell size for GPU ray march renderer in mm (default 2.0)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("RayMarchCellSize", 2.0)

def set_ray_march_cell_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("RayMarchCellSize", float(val))

def get_model_tolerance():
    """Return the project accuracy target in mm (default 0.1).

    This is the global "how close is close enough" number: the maximum surface
    deviation the slicer and the CAM toolpath generator are allowed to leave
    behind. It seeds the Tolerance field of the SDF Slice and SDF CAM panels,
    which may still be overridden per run.

    Cost is superlinear -- halving it multiplies the control points a contour
    needs (and the refinement iterations that find them), so it is the single
    knob that decides how long a slice takes.
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("ModelTolerance", 0.1)

def set_model_tolerance(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("ModelTolerance", float(val))

def get_voxel_grid_resolution():
    """Return the scene volume resolution along its longest axis (default 256).

    Memory is res^3 * 4 bytes for the distance volume: 128 -> 8 MB, 256 -> 67 MB,
    384 -> 227 MB. Values above 384 are refused by SceneVolume.ensure().
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("VoxelGridResolution", 256)

def set_voxel_grid_resolution(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("VoxelGridResolution", int(val))

def get_voxel_band_voxels():
    """Return the narrow-band half-width in voxels (default 8).

    Distances are clamped to +/- band and empty space is cleared to +band. Raising
    this lets rays take longer steps in empty space; lowering it below 2 makes the
    trilinear gradient unusable for normals.
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("VoxelBandVoxels", 8)

def set_voxel_band_voxels(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("VoxelBandVoxels", int(val))

def get_show_cage_curves():
    """Return whether to draw curves representing the edges of the patches for cage objects."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ShowCageCurves", True)

def set_show_cage_curves(show):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ShowCageCurves", bool(show))


def get_max_sdf_render_size():
    """Return the maximum SDF bounding box dimension in mm for rendering (default 2000)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("MaxSdfRenderSize", 2000.0)

def set_max_sdf_render_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("MaxSdfRenderSize", float(val))

def get_heightmap_resolution():
    """Return the heightmap baking resolution (default 256)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("HeightmapResolution", 256)

def set_heightmap_resolution(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("HeightmapResolution", int(val))

def apply_near_clip_override():
    """Apply near clip distance override to the active camera, if set."""
    dist = get_near_clip_distance()
    if dist <= 0.0:
        return  # auto mode
    try:
        view = FreeCADGui.ActiveDocument.ActiveView
        if view:
            cam = view.getCameraNode()
            if cam:
                cam.nearDistance.setValue(dist)
    except Exception as e:
        fld_logger.debug(f"apply_near_clip_override failed: {e}")

def get_interactive_throttle_interval():
    """Return the interactive remeshing/update throttle interval in seconds."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("InteractiveThrottleInterval", 0.025)

def set_interactive_throttle_interval(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("InteractiveThrottleInterval", float(val))

def get_drag_tick_rate():
    """Return the drag tick rate in Hz (default 30)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("DragTickRate", 30)

def set_drag_tick_rate(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("DragTickRate", int(val))

def get_sdf_selection_outline_size():
    """Return the selection outline border size in pixels (default 2, 0 to disable)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SdfSelectionOutlineSize", 2)

def set_sdf_selection_outline_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SdfSelectionOutlineSize", int(val))

def get_hatch_size():
    """Crosshatch period in screen pixels for subtractive SDFs (0 disables it)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("HatchSize", 8)

def set_hatch_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("HatchSize", int(val))

def get_hatch_color():
    """Crosshatch line colour (r, g, b in 0-1).

    Defaults to field_appearance.DEFAULT_HATCH_COLOR -- the blue that used to be
    the subtractive body colour before CH-002 made every object orange.
    """
    p = FreeCAD.ParamGet(_PARAM_PATH)
    return (p.GetFloat("HatchColorR", 0.3),
            p.GetFloat("HatchColorG", 0.5),
            p.GetFloat("HatchColorB", 1.0))

def set_hatch_color(rgb):
    p = FreeCAD.ParamGet(_PARAM_PATH)
    p.SetFloat("HatchColorR", float(rgb[0]))
    p.SetFloat("HatchColorG", float(rgb[1]))
    p.SetFloat("HatchColorB", float(rgb[2]))

def get_hatch_strength():
    """How far to mix the hatch colour over the body colour (0-1, 1 = opaque lines)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("HatchStrength", 1.0)

def set_hatch_strength(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("HatchStrength", max(0.0, min(1.0, float(val))))

# Overall rendering quality presets.
#   interactive_scale: viewport downscale factor during navigation/drag (1 = native res)
#   interactive_steps: ray march u_max_steps during navigation/drag
#   quality_steps:     ray march u_max_steps for the final quality frame
# `interactive_voxel_scale` divides the scene volume resolution while a drag is
# in progress, the same way `interactive_scale` divides the screen resolution.
# It is a separate number because the two cost curves differ: the screen scale
# is quadratic, the volume scale is CUBIC, so a factor of 2 here is 8x the bake.
# See fld_scene_voxel_renderer's Pass 1 -- an edit that grows the scene bbox
# remaps the volume and forces a full bake on every tick, so the drag pays this
# in full every frame while the idle view never pays it at all.
RENDER_QUALITY_PRESETS = {
    0: {"name": "Draft",    "interactive_scale": 4, "interactive_voxel_scale": 4, "interactive_steps": 48,  "quality_steps": 256},
    1: {"name": "Balanced", "interactive_scale": 2, "interactive_voxel_scale": 2, "interactive_steps": 96,  "quality_steps": 512},
    2: {"name": "High",     "interactive_scale": 2, "interactive_voxel_scale": 2, "interactive_steps": 256, "quality_steps": 768},
    3: {"name": "Ultra",    "interactive_scale": 1, "interactive_voxel_scale": 1, "interactive_steps": 256, "quality_steps": 1024},
}

def get_render_quality():
    """Return the overall rendering quality preset index (0=Draft, 1=Balanced, 2=High, 3=Ultra)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("RenderQuality", 1)

def set_render_quality(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("RenderQuality", int(val))

def get_render_quality_preset():
    """Return the active quality preset dict (falls back to Balanced for unknown indices)."""
    return RENDER_QUALITY_PRESETS.get(get_render_quality(), RENDER_QUALITY_PRESETS[1])

def get_interactive_resolution_scaling():
    """Return the interactive resolution downscaling factor derived from the quality preset."""
    return get_render_quality_preset()["interactive_scale"]

def get_interactive_voxel_scaling():
    """Return the scene-volume resolution divisor to use during a drag.

    Presets written before this key existed still work: they fall back to 1,
    which is the previous behaviour (bake at full resolution while dragging).
    """
    return get_render_quality_preset().get("interactive_voxel_scale", 1)

def get_cage_patch_type():
    """Return the cage patch type (0=Bilinear Coons, 1=Bicubic Coons, 2=Gregory-Coons)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("CagePatchType", 0)

def set_cage_patch_type(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("CagePatchType", int(val))

def get_simplify_cage_drag():
    """Return whether to simplify the cage model during interactive drag (default True)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SimplifyCageDrag", True)

def set_simplify_cage_drag(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("SimplifyCageDrag", bool(val))

def get_sdf_warp_resolution():
    """Deform-cage warp volume resolution (points along the longest axis, default 32).

    The warp is smooth and low-frequency, so this needs far less resolution than a
    surface SDF would. Bake cost is cubic: 16≈4k pts, 32≈33k, 64≈262k.
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SdfWarpResolution", 32)

def set_sdf_warp_resolution(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SdfWarpResolution", int(val))

def get_gpu_field_eval():
    """Return whether GPU field evaluation (compute shader batching) is enabled."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("GpuFieldEval", True)

def set_gpu_field_eval(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("GpuFieldEval", bool(val))


def get_snap_enabled():
    """Whether snapping is on by default (Ctrl inverts it during a drag)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SnapEnabled", True)

def set_snap_enabled(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("SnapEnabled", bool(val))

def get_snap_grid_step():
    """Translation quantum in mm. 0 disables grid snap."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("SnapGridStep", 1.0)

def set_snap_grid_step(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("SnapGridStep", max(0.0, float(val)))

def get_snap_angle_step():
    """Rotation quantum in degrees. 0 disables angle snap."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("SnapAngleStep", 15.0)

def set_snap_angle_step(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("SnapAngleStep", max(0.0, float(val)))

def get_snap_vertex_enabled():
    """Whether drags snap to nearby control points and primitive origins."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SnapVertex", True)

def set_snap_vertex_enabled(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("SnapVertex", bool(val))

def get_snap_pixel_radius():
    """How close in screen pixels a candidate must be to win a vertex snap."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("SnapPixelRadius", 12.0)

def set_snap_pixel_radius(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("SnapPixelRadius", float(val))


def get_snap_guide_enabled():
    """Whether the axis line / plane grid is drawn during a snapped drag."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SnapGuideEnabled", True)


def set_snap_guide_enabled(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("SnapGuideEnabled", bool(val))


def get_snap_guide_line_color():
    """Axis guide line colour (r, g, b in 0-1)."""
    p = FreeCAD.ParamGet(_PARAM_PATH)
    return (p.GetFloat("SnapGuideLineR", 0.55),
            p.GetFloat("SnapGuideLineG", 0.75),
            p.GetFloat("SnapGuideLineB", 1.00))


def set_snap_guide_line_color(rgb):
    p = FreeCAD.ParamGet(_PARAM_PATH)
    p.SetFloat("SnapGuideLineR", float(rgb[0]))
    p.SetFloat("SnapGuideLineG", float(rgb[1]))
    p.SetFloat("SnapGuideLineB", float(rgb[2]))


def get_snap_guide_line_width():
    """Axis guide line width in pixels."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("SnapGuideLineWidth", 1.5)


def set_snap_guide_line_width(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("SnapGuideLineWidth", max(0.5, float(val)))


def get_snap_detent_color():
    """Detent dot colour (r, g, b in 0-1)."""
    p = FreeCAD.ParamGet(_PARAM_PATH)
    return (p.GetFloat("SnapDetentR", 1.0),
            p.GetFloat("SnapDetentG", 1.0),
            p.GetFloat("SnapDetentB", 1.0))


def set_snap_detent_color(rgb):
    p = FreeCAD.ParamGet(_PARAM_PATH)
    p.SetFloat("SnapDetentR", float(rgb[0]))
    p.SetFloat("SnapDetentG", float(rgb[1]))
    p.SetFloat("SnapDetentB", float(rgb[2]))


def get_snap_detent_size():
    """Detent dot diameter in screen pixels. 0 hides the dots, keeping the line."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SnapDetentSize", 5)


def set_snap_detent_size(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SnapDetentSize", max(0, int(val)))


def get_snap_detent_interval():
    """Grid steps between guide detents. 10 draws a dot every tenth snap position.

    Detents are landmarks for reading distance, not the snap positions themselves --
    snapping still quantizes to every step. 1 means a dot per step, which is what the
    owner rejected as unreadable on 2026-09-09; it stays reachable but is not the default.
    """
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SnapDetentInterval", 10)


def set_snap_detent_interval(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SnapDetentInterval", max(1, min(1000, int(val))))


def get_snap_grid_color():
    """Plane guide grid colour (r, g, b in 0-1)."""
    p = FreeCAD.ParamGet(_PARAM_PATH)
    return (p.GetFloat("SnapGridR", 0.45),
            p.GetFloat("SnapGridG", 0.45),
            p.GetFloat("SnapGridB", 0.50))


def set_snap_grid_color(rgb):
    p = FreeCAD.ParamGet(_PARAM_PATH)
    p.SetFloat("SnapGridR", float(rgb[0]))
    p.SetFloat("SnapGridG", float(rgb[1]))
    p.SetFloat("SnapGridB", float(rgb[2]))


def get_snap_grid_line_width():
    """Plane guide grid line width in pixels."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetFloat("SnapGridLineWidth", 1.0)


def set_snap_grid_line_width(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetFloat("SnapGridLineWidth", max(0.5, float(val)))


def get_snap_guide_extent_px():
    """How far the axis guide reaches from the anchor, in screen pixels, each way."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SnapGuideExtentPx", 600)


def set_snap_guide_extent_px(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SnapGuideExtentPx", max(50, int(val)))


def get_snap_grid_extent_steps():
    """Half-width of the plane grid, in grid steps. Caps the line count at 2N+1 per axis."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SnapGridExtentSteps", 20)


def set_snap_grid_extent_steps(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SnapGridExtentSteps", max(1, min(200, int(val))))


def get_snap_marker_size_px():
    """FldSnapIndicator cross arm length in screen pixels (was a fixed 8 mm)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetInt("SnapMarkerSizePx", 10)


def set_snap_marker_size_px(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetInt("SnapMarkerSizePx", max(2, int(val)))


# --- Control scheme -------------------------------------------------------
# One of: "hybrid" (default -- direct drag and modal G/R/S both available),
# "modal" (drag orbits; transforms require G/R/S), "direct" (drag-first;
# modal keys inert). Tools must not branch on this themselves -- FldBase does.
CONTROL_SCHEMES = ("hybrid", "modal", "direct")

def get_control_scheme():
    """Which input idiom is in force. See CONTROL_SCHEMES."""
    val = FreeCAD.ParamGet(_PARAM_PATH).GetString("ControlScheme", "hybrid")
    return val if val in CONTROL_SCHEMES else "hybrid"

def set_control_scheme(val):
    if val not in CONTROL_SCHEMES:
        raise ValueError(f"unknown control scheme: {val!r}")
    FreeCAD.ParamGet(_PARAM_PATH).SetString("ControlScheme", val)

def get_snap_during_direct_drag():
    """Whether a plain LMB drag snaps, or only a modal G/R/S transform does."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("SnapDuringDirectDrag", True)

def set_snap_during_direct_drag(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("SnapDuringDirectDrag", bool(val))

def get_snap_invert_modifier():
    """Modifier that inverts the snap default while held: "ctrl", "shift" or "alt"."""
    val = FreeCAD.ParamGet(_PARAM_PATH).GetString("SnapInvertModifier", "ctrl")
    return val if val in ("ctrl", "shift", "alt") else "ctrl"

def set_snap_invert_modifier(val):
    if val not in ("ctrl", "shift", "alt"):
        raise ValueError(f"unknown snap modifier: {val!r}")
    FreeCAD.ParamGet(_PARAM_PATH).SetString("SnapInvertModifier", val)

def get_modal_requires_selection():
    """Whether GRAB refuses to start with nothing selected (True) or grabs the
    whole object (False)."""
    return FreeCAD.ParamGet(_PARAM_PATH).GetBool("ModalRequiresSelection", False)

def set_modal_requires_selection(val):
    FreeCAD.ParamGet(_PARAM_PATH).SetBool("ModalRequiresSelection", bool(val))

