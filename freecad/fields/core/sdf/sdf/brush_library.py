# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/sdf/sdf/brush_library.py

Per-user sculpt brush library: <UserAppData>/Fields/brushes/<name>.npz.
Shared by every FreeCAD document -- brushes are assets, not document content.
"""
import os
import FreeCAD
import numpy as np
from freecad.fields.core import fld_logger
from freecad.fields.core.sdf.sdf.voxel_field import SdfVoxelField
from freecad.fields.core.sdf.sdf.sculpt_brush import SculptBrush
from freecad.fields.core.sdf.sdf.sphere import SdfSphereField
from freecad.fields.core.sdf.sdf.box import SdfBoxField
from freecad.fields.core.sdf.sdf.cylinder import SdfCylinderField
from freecad.fields.core.sdf.sdf.torus import SdfTorusField

# The brush a stroke uses when nothing else has been chosen. A sphere is the only
# shape whose stamp is orientation-free, so a dab is the same whichever way the
# surface faces and the tool needs no brush rotation to be usable on day one.
DEFAULT_BRUSH_NAME = "Sphere"


def brush_dir():
    base = FreeCAD.ConfigGet("UserAppData") or os.path.expanduser("~")
    return os.path.join(base, "Fields", "brushes")


def list_brushes():
    d = brush_dir()
    if not os.path.isdir(d) or not os.listdir(d):
        ensure_default_brushes()
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".npz"))


def save_brush(brush):
    d = brush_dir()
    try:
        os.makedirs(d, exist_ok=True)
        np.savez_compressed(
            os.path.join(d, brush.name + ".npz"),
            data=brush.grid.data,
            resolution=np.array(brush.grid.resolution, dtype=np.int32),
            params=np.array([brush.strength, brush.falloff, brush.spacing], dtype=np.float64))
        return True
    except Exception as e:
        fld_logger.error(f"brush_library: failed to save {brush.name}: {e}")
        return False


def load_brush(name):
    path = os.path.join(brush_dir(), name + ".npz")
    if not os.path.exists(path):
        fld_logger.error(f"brush_library: no brush named {name} at {path}")
        return None
    try:
        with np.load(path) as z:
            grid = SdfVoxelField(size=(1.0, 1.0, 1.0),
                                 resolution=tuple(int(r) for r in z["resolution"]),
                                 data=z["data"].astype(np.float32))
            s, f, sp = z["params"]
        return SculptBrush(name, grid, strength=s, falloff=f, spacing=sp)
    except Exception as e:
        fld_logger.error(f"brush_library: failed to load {path}: {e}")
        return None


def ensure_default_brushes():
    """Seed the library with built-in primitive brushes if not already present."""
    d = brush_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        fld_logger.debug(f"brush_library: could not create brush dir {d}: {e}")
        return

    builtins = [
        ("Sphere", SdfSphereField(FreeCAD.Vector(0, 0, 0), 0.5)),
        ("Cube", SdfBoxField(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1.0, 1.0, 1.0))),
        ("Cylinder", SdfCylinderField(FreeCAD.Vector(0, 0, -0.5), FreeCAD.Vector(0, 0, 1), 0.5, 1.0)),
        ("Torus", SdfTorusField(FreeCAD.Vector(0, 0, 0), 0.35, 0.15)),
    ]

    created = []
    for name, field in builtins:
        path = os.path.join(d, name + ".npz")
        if os.path.exists(path):
            continue
        brush = SculptBrush.from_field(field, name, resolution=32)
        if save_brush(brush):
            created.append(name)

    if created:
        fld_logger.info(f"brush_library: created default brushes: {', '.join(created)}")


def default_brush():
    """The default brush, seeding the library first if it is empty."""
    ensure_default_brushes()
    brush = load_brush(DEFAULT_BRUSH_NAME)
    if brush is None:
        # The user deleted or corrupted Sphere.npz. Rebuild it in memory rather than
        # returning None -- a tool with no brush is a tool that cannot be activated,
        # and this is recoverable without one.
        fld_logger.warn(f"brush_library: {DEFAULT_BRUSH_NAME} missing; rebuilding in memory")
        brush = SculptBrush.from_field(
            SdfSphereField(FreeCAD.Vector(0, 0, 0), 0.5), DEFAULT_BRUSH_NAME, resolution=32)
    return brush
