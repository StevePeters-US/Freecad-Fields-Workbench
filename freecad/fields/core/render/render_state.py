# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/render_state.py

The renderer's two quality states, in a module of their own so that both the
renderer and the navigation filter that drives it can name them without either
importing the other.

`fld_scene_voxel_renderer` re-exports both names; callers outside this package
import them from there, which is the spelling the rest of the tree already uses.
"""

_RS_QUALITY     = 0   # full analytic shader + SSAO
_RS_INTERACTIVE = 1   # downscaled analytic shader, fewer march steps
