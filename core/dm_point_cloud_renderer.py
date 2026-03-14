"""
core/dm_point_cloud_renderer.py

Lightweight point-cloud renderer for F-Rep SDF objects.
Samples zero-crossings on a uniform grid and renders them via SoPointSet.
No triangulation, no shaders — pure fixed-function Coin3D.
"""
import numpy as np

try:
    from pivy import coin
except ImportError:
    coin = None


def sample_surface_points(field, cell_size: float):
    """
    Find SDF zero-crossings on a uniform grid and return surface samples.

    For each grid edge (along x, y, z axes) where the SDF changes sign,
    linearly interpolates the crossing position. Computes the SDF gradient
    at each crossing for use as a surface normal.

    Args:
        field:     Any FRepField subclass.
        cell_size: Grid spacing in mm. Smaller → more points, slower to sample.

    Returns:
        pts     (np.ndarray): (N, 3) float32 — surface crossing positions.
        normals (np.ndarray): (N, 3) float32 — unit surface normals.
        Returns two empty (0, 3) arrays if the surface has no crossings.
    """
    mn, mx = field.bounding_box()

    # Identical padding logic to MarchingCubesMesher (core/dm_mesher.py:117)
    def _pad(lo, hi):
        if hi - lo < 1e-4:
            mid = (lo + hi) / 2
            return mid - 1.0, mid + 1.0
        return lo - cell_size, hi + cell_size

    x0, x1 = _pad(mn.x, mx.x)
    y0, y1 = _pad(mn.y, mx.y)
    z0, z1 = _pad(mn.z, mx.z)

    nx = max(1, int(np.ceil((x1 - x0) / cell_size)))
    ny = max(1, int(np.ceil((y1 - y0) / cell_size)))
    nz = max(1, int(np.ceil((z1 - z0) / cell_size)))

    x = np.linspace(x0, x0 + nx * cell_size, nx + 1)
    y = np.linspace(y0, y0 + ny * cell_size, ny + 1)
    z = np.linspace(z0, z0 + nz * cell_size, nz + 1)

    X, Y, Z = np.meshgrid(x, y, z, indexing='ij')
    grid_pts = np.column_stack([X.ravel(), Y.ravel(), Z.ravel()]).astype(np.float32)
    vals = field.evaluate_grid(grid_pts).reshape(nx + 1, ny + 1, nz + 1)

    # For each axis, find edges with a sign change and interpolate the crossing.
    # Axis 0 (X-edges): neighbour pairs along the x dimension, etc.
    axis_slices = [
        (slice(None, -1), slice(None), slice(None),
         slice(1, None),  slice(None), slice(None)),   # axis 0
        (slice(None), slice(None, -1), slice(None),
         slice(None), slice(1, None),  slice(None)),   # axis 1
        (slice(None), slice(None), slice(None, -1),
         slice(None), slice(None), slice(1, None)),    # axis 2
    ]

    crossing_pts = []
    for (s0x, s0y, s0z, s1x, s1y, s1z) in axis_slices:
        v0 = vals[s0x, s0y, s0z]
        v1 = vals[s1x, s1y, s1z]
        p0 = np.stack([X[s0x, s0y, s0z],
                       Y[s0x, s0y, s0z],
                       Z[s0x, s0y, s0z]], axis=-1)
        p1 = np.stack([X[s1x, s1y, s1z],
                       Y[s1x, s1y, s1z],
                       Z[s1x, s1y, s1z]], axis=-1)

        mask = (v0 * v1) < 0          # sign change → crossing exists
        v0f  = v0[mask].astype(np.float64)
        v1f  = v1[mask].astype(np.float64)
        p0f  = p0[mask].astype(np.float64)
        p1f  = p1[mask].astype(np.float64)

        denom = v0f - v1f
        denom = np.where(np.abs(denom) < 1e-12, 1e-12, denom)
        t = (v0f / denom).reshape(-1, 1)
        crossing_pts.append((p0f + t * (p1f - p0f)).astype(np.float32))

    if not crossing_pts:
        empty = np.zeros((0, 3), dtype=np.float32)
        return empty, empty.copy()

    pts = np.vstack(crossing_pts)

    # Surface normals = normalised SDF gradient at each crossing
    grads = field.gradient_grid(pts).astype(np.float32)
    norms = np.linalg.norm(grads, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-10)
    normals = grads / norms

    return pts, normals


class DMPointCloudRenderer:
    """Coin3D point-cloud renderer backed by SDF zero-crossing samples."""

    def __init__(self, vobj):
        self.vobj = vobj
        self._sep     = None
        self._switch  = None
        self._coords  = None   # SoCoordinate3
        self._normals = None   # SoNormal
        if coin:
            self._setup_nodes(vobj)

    def _setup_nodes(self, vobj):
        self._switch = coin.SoSwitch()
        self._switch.whichChild = 0 if vobj.Visibility else -1

        sep = coin.SoSeparator()
        self._sep = sep

        mat = coin.SoMaterial()
        mat.diffuseColor.setValue(coin.SbColor(1.0, 0.5, 0.0))
        mat.specularColor.setValue(coin.SbColor(0.3, 0.3, 0.3))
        mat.shininess.setValue(0.3)
        sep.addChild(mat)

        binding = coin.SoNormalBinding()
        binding.value.setValue(coin.SoNormalBinding.PER_VERTEX)
        sep.addChild(binding)

        normals_node = coin.SoNormal()
        sep.addChild(normals_node)

        draw_style = coin.SoDrawStyle()
        draw_style.pointSize.setValue(4.0)
        sep.addChild(draw_style)

        coords = coin.SoCoordinate3()
        sep.addChild(coords)

        sep.addChild(coin.SoPointSet())

        self._coords  = coords
        self._normals = normals_node

        self._switch.addChild(sep)
        vobj.RootNode.addChild(self._switch)

    def set_visible(self, visible):
        """Toggle visibility of the point cloud render."""
        if self._switch:
            self._switch.whichChild = 0 if visible else -1

    def update(self, field, cell_size: float):
        """
        Re-sample the SDF zero-level set and push new points/normals to Coin3D.

        Args:
            field:     Any FRepField subclass.
            cell_size: Grid spacing in mm (from get_meshing_cell_size()).
        """
        if not coin or self._coords is None:
            return

        pts, normals = sample_surface_points(field, cell_size)

        self._coords.point.setNum(0)
        self._normals.vector.setNum(0)

        if len(pts) == 0:
            return

        self._coords.point.setValues(0, len(pts), pts.tolist())
        self._normals.vector.setValues(0, len(normals), normals.tolist())
    def detach(self):
        """Remove nodes from the scene graph."""
        if self._switch and self.vobj and self.vobj.RootNode:
            try:
                self.vobj.RootNode.removeChild(self._switch)
            except Exception:
                pass
        self._sep = None
        self._switch = None
        self._coords = None
        self._normals = None
