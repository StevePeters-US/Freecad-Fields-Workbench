# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""The lattice editor's universal gizmo: `LatticeGizmoTarget` and `LatticeGizmoMixin` (INP-181)."""
import FreeCAD
import numpy as np

from freecad.fields.core.input.gizmo.transform import array_map
from freecad.fields.core.render.field_appearance import DEFAULT_ADDITIVE_COLOR
from freecad.fields.tools.selection_gizmo import SelectionGizmoMixin, SelectionGizmoTarget

REST_COLOR = (1.0, 1.0, 1.0)


class LatticeGizmoTarget(SelectionGizmoTarget):
    """Every handle acts on the selected control points about their centroid, or on every point
    when none is selected. A write sets the displacements that put each moved point where the
    delta takes it, clamped toward the snapshot's so no cell folds
    (`SdfLatticeField.clamp_displacements`). Every apply is a total from the snapshot."""

    def live(self):
        return self.tool._lattice_field() is not None

    def rotation(self):
        pl = getattr(self.tool._lattice_field(), "placement", None)
        return FreeCAD.Rotation(pl.Rotation) if pl is not None else FreeCAD.Rotation()

    def geometry(self):
        f = self.tool._lattice_field()
        return {"displacements": np.array(f.displacements, dtype=np.float64)}

    def gizmo_apply(self, snap, delta):
        t = self.tool
        f = t._lattice_field()
        if f is None:
            return
        old = snap["displacements"]
        moved = list(snap["selection"]) or list(range(len(old)))
        world = t._world_points(f, old)
        world[moved] = array_map(delta)(world[moved])
        proposed = t._local_points(f, world) - np.asarray(f.grid_local, dtype=np.float64)
        dtype = np.asarray(f.displacements).dtype
        t._write_displacements(f, f.clamp_displacements(proposed.astype(dtype), old.astype(dtype)))

    def gizmo_restore(self, snap):
        t = self.tool
        f = t._lattice_field()
        if f is None:
            return
        t._write_displacements(f, snap["displacements"].astype(np.asarray(f.displacements).dtype))
        t._commit_changes(force=True)

    def gizmo_commit(self):
        self.tool._commit_changes(force=True)


class LatticeGizmoMixin(SelectionGizmoMixin):
    """The gizmo half of `LatticeTool`. Comes before `FldSdfModifierToolBase` in the MRO."""

    GIZMO_TARGET = LatticeGizmoTarget
    MARQUEE = True

    def _lattice_field(self):
        """The target's lattice field, or None. `get_sdf_field`, not `.SdfField`: the proxy
        sets `SdfField = None` on every Displacements write (`lattice_tool.py:178-180`)."""
        obj = getattr(self, "_target_obj", None)
        proxy = getattr(obj, "Proxy", None) if obj is not None else None
        return proxy.get_sdf_field(obj) if proxy is not None else None

    @staticmethod
    def _world_points(field, displacements):
        """(N, 3) world positions of `grid_local + displacements`."""
        local = np.asarray(field.grid_local, dtype=np.float64) + np.asarray(displacements, dtype=np.float64)
        pl = field.placement
        if pl is None:
            return local
        return np.array([tuple(pl.multVec(FreeCAD.Vector(*p))) for p in local], dtype=np.float64)

    @staticmethod
    def _local_points(field, world):
        """(N, 3) lattice-local positions of `world`."""
        pl = field.placement
        if pl is None:
            return np.array(world, dtype=np.float64)
        inv = pl.inverse()
        return np.array([tuple(inv.multVec(FreeCAD.Vector(*p))) for p in world], dtype=np.float64)

    def _write_displacements(self, field, disps):
        """The old drag's write (`lattice_tool.py:301-312`): field, points, spheres, edges,
        the object's Displacements, then the throttled commit."""
        field.displacements = disps
        self._update_points_from_field(field)
        for i, p in enumerate(self.points):
            if i < len(self.fld_points):
                self.fld_points[i].position = p
                self.fld_points[i].update_draw()
        self._target_obj.Displacements = [
            FreeCAD.Vector(float(d[0]), float(d[1]), float(d[2])) for d in disps]
        self._update_lines()
        self._commit_changes()

    # --- SelectionGizmoMixin hooks -----------------------------------------------------

    def _gizmo_editable(self):
        return self._lattice_field() is not None

    def _pick_point(self, ray_p, ray_d):
        """The control point nearest the ray within a handle radius (the old press's test,
        `lattice_tool.py:232-240`), or None."""
        r = self._compute_handle_radius()
        best, best_dist = None, float("inf")
        for i, pt in enumerate(self.points):
            idx, dist = self._hit_test_perp(ray_p, ray_d, [pt], tolerance=r)
            if idx is not None and dist < best_dist:
                best, best_dist = i, dist
        return best

    def _point_world(self, key):
        return self.points[key] if isinstance(key, int) and 0 <= key < len(self.points) else None

    def _selectable_keys(self):
        return list(range(len(self.points)))

    def _selection_changed(self):
        """Selected spheres in the additive colour (the old drag's), the rest white."""
        sel = set(self._selection)
        for i, fp in enumerate(self.fld_points):
            fp.set_color(DEFAULT_ADDITIVE_COLOR if i in sel else REST_COLOR)

    def _gizmo_unselected_centre(self):
        if not self.points:
            return None
        return sum(self.points, FreeCAD.Vector()) * (1.0 / len(self.points))

    def _gizmo_exclude(self):
        """The lattice's own points never snap."""
        return [self._target_obj] if getattr(self, "_target_obj", None) else []
