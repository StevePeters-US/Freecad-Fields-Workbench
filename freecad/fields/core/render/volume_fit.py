# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/volume_fit.py

Pure CPU sizing math for the scene volume box, extracted from `scene_volume.py`
because it touches only its own arguments and `FreeCAD.Vector` -- no GL, no
`self`. Unit-tested directly (`test_scene_volume.py` imports `_fit_box` and
`VOLUME_SLACK_FRAC` from `scene_volume`, which re-exports these names).
"""
import FreeCAD

MAX_RESOLUTION = 384

# How much empty margin to leave around the scene when the volume box is
# (re)fitted, as a fraction of the scene's own extent.
#
# The box used to be refitted to the scene bbox exactly, which meant an extrude
# drag -- whose whole job is to grow the bbox -- moved the world->voxel mapping
# on nearly every tick, set `dirty`, and forced a full bake. Growing in slack
# steps instead lets the mapping hold still across most of a drag, which is the
# precondition for a region bake being legal at all.
#
# The cost is resolution: the same voxel budget now spans a box this much larger,
# so voxels are ~SLACK bigger. That is a linear quality loss bought against a
# cubic bake saving.
VOLUME_SLACK_FRAC = 0.25

# Refit when the scene has shrunk to less than this fraction of the box, so a
# deleted or collapsed feature gives its resolution back. It has to sit well
# below 1/(1 + SLACK) or a fresh refit would immediately qualify as too loose.
VOLUME_SHRINK_FRAC = 0.5


def _fit_box(bbox_min, bbox_max, resolution, band_voxels, slack_frac=VOLUME_SLACK_FRAC):
    """Pure sizing function returning (vmin, vmax, nx, ny, nz, step)."""
    resolution = max(32, min(int(resolution), MAX_RESOLUTION))
    extent = FreeCAD.Vector(bbox_max.x - bbox_min.x,
                            bbox_max.y - bbox_min.y,
                            bbox_max.z - bbox_min.z)
    longest = max(extent.x, extent.y, extent.z, 1e-6)
    cell = longest / resolution
    pad = cell * (band_voxels + 1)
    req_min = FreeCAD.Vector(bbox_min.x - pad, bbox_min.y - pad, bbox_min.z - pad)
    req_max = FreeCAD.Vector(bbox_max.x + pad, bbox_max.y + pad, bbox_max.z + pad)

    slack = slack_frac * max(req_max.x - req_min.x,
                             req_max.y - req_min.y,
                             req_max.z - req_min.z)
    vmin = FreeCAD.Vector(req_min.x - slack, req_min.y - slack, req_min.z - slack)
    vmax = FreeCAD.Vector(req_max.x + slack, req_max.y + slack, req_max.z + slack)

    cell = max(vmax.x - vmin.x, vmax.y - vmin.y, vmax.z - vmin.z, 1e-6) / resolution

    nx = max(1, min(resolution, int((vmax.x - vmin.x) / cell) + 1))
    ny = max(1, min(resolution, int((vmax.y - vmin.y) / cell) + 1))
    nz = max(1, min(resolution, int((vmax.z - vmin.z) / cell) + 1))

    step = FreeCAD.Vector((vmax.x - vmin.x) / nx,
                          (vmax.y - vmin.y) / ny,
                          (vmax.z - vmin.z) / nz)
    return vmin, vmax, nx, ny, nz, step
