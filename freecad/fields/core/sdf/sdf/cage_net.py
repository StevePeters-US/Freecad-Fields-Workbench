# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""CageNet value object: single source of truth for cage control net geometry and topology."""
from dataclasses import dataclass
from typing import List, Tuple, Optional
import numpy as np


@dataclass
class CageNet:
    vertices: np.ndarray        # (Nv, 3) float64
    handles: np.ndarray         # (Nh, 3) float64
    face_verts: List[List[int]] # per-face vertex index lists
    edges: List[Tuple[int, int]]# (Ne, 2) vertex index pairs
    handle_types: List[int]     # (Nh,) int HandleType
    edge_straight: List[bool]   # (Ne,) bool
    rest_vertices: Optional[np.ndarray] = None # (Nv, 3) float64
    rest_handles: Optional[np.ndarray] = None  # (Nh, 3) float64
    displacements: Optional[np.ndarray] = None # (Nv+Nh, 3) float64
    source: Optional[object] = None
    placement: Optional[object] = None

    @property
    def face_verts_flat(self) -> List[int]:
        return [v for f in self.face_verts for v in f]

    def __getitem__(self, key: str):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def __contains__(self, key) -> bool:
        # Required, not optional. Defining __getitem__ without this makes
        # `"vertices" in net` fall back to the old iteration protocol, which
        # calls __getitem__(0) and raises TypeError instead of returning a
        # bool -- from a line that looks nothing like the cause.
        return isinstance(key, str) and hasattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    @property
    def face_sizes(self) -> List[int]:
        return [len(f) for f in self.face_verts]

    @property
    def control_points(self) -> np.ndarray:
        """Vertices then handles -- the order obj.Points is stored in."""
        if self.handles is not None and len(self.handles) > 0:
            return np.vstack([self.vertices, self.handles])
        return self.vertices.copy()

    def to_properties(self, obj) -> None:
        """Write this net onto a FreeCAD DocumentObject's properties."""
        import FreeCAD
        proxy = getattr(obj, "Proxy", None)
        suspend = hasattr(proxy, "_suspend_rebuild")
        if suspend:
            proxy._suspend_rebuild = True
        try:
            if hasattr(obj, "Vertices") and self.rest_vertices is not None:
                obj.Vertices = [FreeCAD.Vector(*v) for v in self.rest_vertices]
            if hasattr(obj, "Handles") and self.rest_handles is not None:
                obj.Handles = [FreeCAD.Vector(*h) for h in self.rest_handles]
            obj.FaceVertices = self.face_verts_flat
            obj.FaceSizes = self.face_sizes
            if hasattr(obj, "EdgeVertices"):
                obj.EdgeVertices = [v for edge in self.edges for v in edge]
            obj.HandleTypes = list(self.handle_types)
            if hasattr(obj, "EdgeStraight"):
                obj.EdgeStraight = [1 if es else 0 for es in self.edge_straight]
            if hasattr(obj, "Displacements") and self.displacements is not None:
                obj.Displacements = [FreeCAD.Vector(*d) for d in self.displacements]
            obj.Points = [FreeCAD.Vector(*p) for p in self.control_points]
        finally:
            if suspend:
                proxy._suspend_rebuild = False

    @classmethod
    def from_properties(cls, obj) -> "CageNet":
        """Reconstruct a CageNet from a FreeCAD DocumentObject's properties."""
        def vec_to_np(vecs, default_shape=(0, 3)):
            if vecs is None or len(vecs) == 0:
                return np.zeros(default_shape, dtype=np.float64)
            return np.array([[v.x, v.y, v.z] if hasattr(v, "x") else [v[0], v[1], v[2]]
                             for v in vecs], dtype=np.float64)

        face_verts_raw = list(getattr(obj, "FaceVertices", []))
        face_sizes_raw = list(getattr(obj, "FaceSizes", []))
        if face_sizes_raw:
            face_verts = []
            offset = 0
            for sz in face_sizes_raw:
                face_verts.append(face_verts_raw[offset:offset + sz])
                offset += sz
        elif len(face_verts_raw) > 0 and isinstance(face_verts_raw[0], (list, tuple)):
            face_verts = face_verts_raw
        else:
            face_verts = []

        edge_verts_raw = list(getattr(obj, "EdgeVertices", []))
        edges = [(edge_verts_raw[i], edge_verts_raw[i + 1])
                 for i in range(0, len(edge_verts_raw), 2)]

        pts_np = vec_to_np(getattr(obj, "Points", []))
        rest_v = vec_to_np(getattr(obj, "Vertices", None))
        rest_h = vec_to_np(getattr(obj, "Handles", None))
        disp = vec_to_np(getattr(obj, "Displacements", None))

        nv = len(rest_v) if len(rest_v) else len(pts_np) - 2 * len(edges)
        verts = pts_np[:nv] if len(pts_np) >= nv else rest_v
        handles = pts_np[nv:] if len(pts_np) >= nv else rest_h

        ht = [int(h) for h in getattr(obj, "HandleTypes", [])]
        es = [bool(e) for e in getattr(obj, "EdgeStraight", [])]

        return cls(
            vertices=verts,
            handles=handles,
            face_verts=face_verts,
            edges=edges,
            handle_types=ht,
            edge_straight=es,
            rest_vertices=rest_v if len(rest_v) else verts,
            rest_handles=rest_h if len(rest_h) else handles,
            displacements=disp,
        )

    @classmethod
    def from_field(cls, field) -> "CageNet":
        """Extract a CageNet from an SdfCageField or SdfCageDeformField."""
        is_deform = hasattr(field, "rest_vertices")
        return cls(
            vertices=field.vertices.copy(),
            handles=field.handles.copy(),
            face_verts=[list(f) for f in field._face_verts],
            edges=list(field._edges),
            handle_types=list(getattr(field, "_handle_types", [])),
            edge_straight=list(getattr(field, "_edge_straight", [])),
            rest_vertices=field.rest_vertices.copy() if is_deform else field.vertices.copy(),
            rest_handles=field.rest_handles.copy() if is_deform else field.handles.copy(),
            displacements=getattr(field, "displacements", None),
            source=getattr(field, "source", field),
            placement=getattr(field, "placement", None),
        )

    def to_field(self, source=None, placement=None, mvc_rest_verts=None, mvc_tris=None):
        """Construct an SdfCageDeformField or SdfCageField from this net."""
        if source is not None:
            from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField
            return SdfCageDeformField(
                source=source,
                vertices=self.vertices,
                handles=self.handles,
                face_verts=self.face_verts,
                edges=self.edges,
                handle_types=self.handle_types,
                edge_straight=self.edge_straight,
                rest_vertices=self.rest_vertices,
                rest_handles=self.rest_handles,
                displacements=self.displacements,
                placement=placement,
                mvc_rest_verts=mvc_rest_verts,
                mvc_tris=mvc_tris,
            )
        else:
            from freecad.fields.core.sdf.sdf.cage import SdfCageField
            return SdfCageField(
                vertices=self.vertices,
                handles=self.handles,
                face_verts=self.face_verts_flat,
                face_sizes=self.face_sizes,
                edges=self.edges,
                placement=placement,
                handle_types=self.handle_types,
            )


def read_net(obj) -> Optional[CageNet]:
    """Read a CageNet from a FreeCAD DocumentObject's properties, or None if no cage."""
    if obj is None:
        return None
    ev = list(getattr(obj, "EdgeVertices", []))
    v = list(getattr(obj, "Vertices", []))
    if not ev or not v:
        return None
    return CageNet.from_properties(obj)


def write_net(obj, net: CageNet, points=None, displacements=None) -> None:
    """Write a CageNet to a FreeCAD DocumentObject's properties."""
    if net is None:
        return
    if points is not None:
        pts_arr = np.asarray([[p.x, p.y, p.z] if hasattr(p, "x") else [p[0], p[1], p[2]]
                              for p in points], dtype=np.float64)
        nv = len(net.vertices)
        net = CageNet(
            vertices=pts_arr[:nv] if len(pts_arr) >= nv else net.vertices,
            handles=pts_arr[nv:] if len(pts_arr) >= nv else net.handles,
            face_verts=net.face_verts,
            edges=net.edges,
            handle_types=net.handle_types,
            edge_straight=net.edge_straight,
            rest_vertices=net.rest_vertices,
            rest_handles=net.rest_handles,
            displacements=np.asarray([[d.x, d.y, d.z] if hasattr(d, "x") else [d[0], d[1], d[2]]
                                      for d in displacements], dtype=np.float64) if displacements is not None else net.displacements,
            source=net.source,
            placement=net.placement,
        )
    elif displacements is not None:
        net.displacements = np.asarray([[d.x, d.y, d.z] if hasattr(d, "x") else [d[0], d[1], d[2]]
                                        for d in displacements], dtype=np.float64)
    net.to_properties(obj)
