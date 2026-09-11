# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf_constants.py

Shared constant arrays used by both the octree builder (sdf_octree.py) and
the isosurface mesher (fld_mesher.py). Keeping a single definition avoids the
two staying in sync by convention only.
"""
import numpy as np

# Sentinel value indicating no surface ID assigned
SURFACE_ID_UNSET = 65535

# Degenerate-axis check threshold (near-zero axis or normal length)
DEGENERATE_AXIS_EPS = 1e-8

# Project default model tolerance target (0.1 mm)
DEFAULT_MODEL_TOLERANCE_MM = 0.1

# Corner (dx, dy, dz) offsets in Lorensen-Cline order, in unit-cell coords.
_OFF_X = np.array([0, 1, 1, 0, 0, 1, 1, 0], dtype=np.float64)
_OFF_Y = np.array([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.float64)
_OFF_Z = np.array([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.float64)
CELL_OFFSETS = np.stack([_OFF_X, _OFF_Y, _OFF_Z], axis=1)  # (8, 3)

# Shared ExtrudeGizmo visual colors
EXTRUDE_GIZMO_COLOR = (1.0, 0.5, 0.0)      # Orange for active extrusion ring and handle spheres
EXTRUDE_CONNECTOR_COLOR = (0.8, 0.8, 0.8)  # Light gray for vertical connector lines
