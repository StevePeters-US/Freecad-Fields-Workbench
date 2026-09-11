# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import numpy as np


def to_xyz_list(v):
    """(x, y, z) as a plain list, from a FreeCAD.Vector or any 3-element sequence.

    Shared by every `[v.x, v.y, v.z] if hasattr(v, "x") else [v[0], v[1], v[2]]`
    site that builds an (N,3) numpy array from a mixed Vector/sequence list
    (CR-032) -- `CageNetView.__init__`, `carry_ring_handles_to_cap` (twice),
    `build_extrusion_cage`, and `fld_deform_objects._extrusion_records.to_np`.
    """
    return [v.x, v.y, v.z] if hasattr(v, "x") else [v[0], v[1], v[2]]


class CageNetView:
    """Read-only cage topology view backed by an object's properties.

    Exposes the same attribute names a cage SdfField does (`vertices`,
    `handles`, `_edges`, `_face_verts`, `_edge_lookup`, `_handle_types`) so
    every existing topology reader works unchanged.
    """

    def __init__(self, obj):
        self.vertices = np.array([to_xyz_list(v) for v in (getattr(obj, "Vertices", None) or [])], dtype=np.float64)
        if len(self.vertices) == 0:
            self.vertices = np.zeros((0, 3), dtype=np.float64)
        self.handles = np.array([to_xyz_list(v) for v in (getattr(obj, "Handles", None) or [])], dtype=np.float64)
        if len(self.handles) == 0:
            self.handles = np.zeros((0, 3), dtype=np.float64)
        ev = list(getattr(obj, "EdgeVertices", None) or [])
        self._edges = [(ev[2 * i], ev[2 * i + 1]) for i in range(len(ev) // 2)]
        self._edge_lookup = {frozenset(e): i for i, e in enumerate(self._edges)}
        fv, fs = list(getattr(obj, "FaceVertices", None) or []), list(getattr(obj, "FaceSizes", None) or [])
        self._face_verts, at = [], 0
        for n in fs:
            self._face_verts.append(fv[at:at + n])
            at += n
        self._handle_types = list(getattr(obj, "HandleTypes", None) or []) or [1] * len(self.handles)


def cage_net_of(obj):
    """The cage topology for `obj`: its SdfField when that IS a cage, else its
    properties. Returns None when the object carries no cage net."""
    if obj is None:
        return None
    field = getattr(getattr(obj, "Proxy", None), "SdfField", None)
    if field is not None and hasattr(field, "vertices") and hasattr(field, "_edges"):
        return field
    if list(getattr(obj, "EdgeVertices", [])) and list(getattr(obj, "Vertices", [])):
        return CageNetView(obj)
    return None


def _set_edge_handles(handles_np, edges, ei, va, vb, h_near_a, h_near_b):
    """Write one edge's two handles in the orientation the edge is stored in."""
    if edges[ei][0] == va:
        handles_np[2 * ei] = h_near_a
        handles_np[2 * ei + 1] = h_near_b
    else:
        handles_np[2 * ei] = h_near_b
        handles_np[2 * ei + 1] = h_near_a


def carry_ring_handles_to_cap(handles_np, edges, base_handles, base_pts, top_pts,
                              top_offset=None, edge_straight=None):
    """Copy a ring's Bezier handles onto the swept cap ring.

    base_handles is 2 per ring edge j, ordered (near ring[j], near ring[j+1]). The
    handles sit at t = 1/3 and 2/3, so they take the sweep offset lerped at those
    params -- which collapses to a rigid translation whenever the sweep is uniform.
    top_offset is the cap ring's vertex-index base, so this serves both a freshly
    built prism (k) and a grown cage (n0).
    """
    k = len(base_pts)
    if top_offset is None:
        top_offset = k
    BH = np.array([to_xyz_list(v) for v in base_handles], dtype=np.float64).reshape((-1, 3))
    if len(BH) < 2 * k:
        return
    base_pts_np = np.array([to_xyz_list(v) for v in base_pts], dtype=np.float64).reshape((-1, 3))
    top_pts_np = np.array([to_xyz_list(v) for v in top_pts], dtype=np.float64).reshape((-1, 3))
    offs = top_pts_np - base_pts_np
    edge_lookup = {frozenset(e): i for i, e in enumerate(edges)}
    for j in range(k):
        a, b = j, (j + 1) % k
        ei = edge_lookup.get(frozenset((top_offset + a, top_offset + b)))
        if ei is None:
            continue
        off_a, off_b = offs[a], offs[b]
        _set_edge_handles(handles_np, edges, ei, top_offset + a, top_offset + b,
                          BH[2 * j] + off_a + (off_b - off_a) / 3.0,
                          BH[2 * j + 1] + off_a + 2.0 * (off_b - off_a) / 3.0)
        if edge_straight is not None and ei < len(edge_straight):
            edge_straight[ei] = False


def build_extrusion_cage(base_ring, top_ring, base_edge_handles=None):
    """Cage net for one extruded face: bottom k-gon, k side quads, top k-gon.

    base_edge_handles is the source face's own boundary handles, 2 per ring edge
    j, ordered (near base_ring[j], near base_ring[j+1]). Given them, the new
    cage's bottom AND top rings inherit that curve profile instead of being
    straight-chorded; the vertical edges stay straight.
    """
    from freecad.fields.core.sdf.sdf.cage import CageTopology

    base_pts = np.array([[v.x, v.y, v.z] for v in base_ring], dtype=np.float64)
    top_pts = np.array([[v.x, v.y, v.z] for v in top_ring], dtype=np.float64)
    k = len(base_pts)

    face_verts_flat = list(range(k))
    face_sizes = [k]
    topo = CageTopology(face_verts_flat, face_sizes, base_pts)

    # Extrude single face
    topo.extrude_face(0, distance=1.0)

    # Overwrite top ring positions
    for i in range(k):
        topo.vertices[k + i].pos = top_pts[i].copy()

    vertices_np = np.array([v.pos for v in topo.vertices], dtype=np.float64)
    edges = topo.edges
    n_edges = len(edges)

    handles_np = np.zeros((2 * n_edges, 3), dtype=np.float64)
    edge_straight = [False] * n_edges

    for ei, (vi, vj) in enumerate(edges):
        p0 = vertices_np[vi]
        p1 = vertices_np[vj]
        is_vertical = (vi < k and vj >= k) or (vj < k and vi >= k)
        if is_vertical:
            edge_straight[ei] = True
        handles_np[2 * ei] = p0 + (p1 - p0) / 3.0
        handles_np[2 * ei + 1] = p0 + 2.0 * (p1 - p0) / 3.0

    handle_types = [1] * (2 * n_edges)

    if base_edge_handles is not None and len(base_edge_handles) >= 2 * k:
        BH = np.array([to_xyz_list(v) for v in base_edge_handles],
                      dtype=np.float64).reshape((-1, 3))
        edge_lookup = {frozenset(e): i for i, e in enumerate(edges)}
        for j in range(k):
            a, b = j, (j + 1) % k
            h_a, h_b = BH[2 * j], BH[2 * j + 1]

            ei = edge_lookup.get(frozenset((a, b)))
            if ei is not None:
                _set_edge_handles(handles_np, edges, ei, a, b, h_a, h_b)
                edge_straight[ei] = False

        carry_ring_handles_to_cap(handles_np, edges, base_edge_handles, base_pts,
                                  top_pts, top_offset=k, edge_straight=edge_straight)

    face_verts_list = []
    face_sizes_list = []
    for f in topo.faces:
        he_start = f.half_edge
        he = he_start
        f_verts = []
        while True:
            f_verts.append(he.prev.vertex.idx)
            he = he.next
            if he == he_start:
                break
        face_verts_list.extend(f_verts)
        face_sizes_list.append(len(f_verts))

    return (vertices_np, handles_np, face_verts_list, face_sizes_list, edges, handle_types, edge_straight)




def _source_face_handles(source_cage, ring_indices):
    """(links, points) for the boundary handles of one source cage face.

    Ordered 2 per ring edge j as (near ring[j], near ring[j+1]) -- the cage
    stores each edge once in an arbitrary direction, so the pair is swapped when
    the stored edge runs against the ring. links index into the source object's
    Points (vertices first, then handles), so the handles can be re-read live.
    """
    n_verts = len(source_cage.vertices)
    handles = source_cage.handles
    k = len(ring_indices)
    links, pts = [], []
    for j in range(k):
        a, b = ring_indices[j], ring_indices[(j + 1) % k]
        ei = source_cage._edge_lookup.get(frozenset((a, b)))
        if ei is None:
            # No such edge (non-manifold face): fall back to a straight chord.
            va = np.asarray(source_cage.vertices[a])
            vb = np.asarray(source_cage.vertices[b])
            pts.extend([FreeCAD.Vector(*(va + (vb - va) / 3.0)),
                        FreeCAD.Vector(*(va + 2.0 * (vb - va) / 3.0))])
            links.extend([-1, -1])
            continue
        h0, h1 = 2 * ei, 2 * ei + 1
        if source_cage._edges[ei][0] != a:
            h0, h1 = h1, h0
        links.extend([n_verts + h0, n_verts + h1])
        pts.extend([FreeCAD.Vector(*handles[h0]), FreeCAD.Vector(*handles[h1])])
    return links, pts


def ring_handles(field, ring_indices, frame="rest"):
    """(2k,3) boundary handles of one cage face, in `frame` ("rest" or "current").

    Ordered 2 per ring edge j as (near ring[j], near ring[j+1]). The cage stores each
    edge once in an arbitrary direction, so the pair is swapped when the stored edge
    runs against the ring.

    The frame matters and the two are NOT interchangeable: geometry that is built or
    stored (an extrusion record) belongs in the rest frame, while anything drawn in the
    viewport belongs in the current frame. Mixing them -- current-frame vertices with
    rest-frame handles -- draws Bezier edges from the right endpoints through the wrong
    control points, which is visible as curves swooping off the surface.
    """
    if frame not in ("rest", "current"):
        raise ValueError(f"ring_handles: frame must be 'rest' or 'current', got {frame!r}")
    k = len(ring_indices)
    out = np.zeros((2 * k, 3), dtype=np.float64)
    if frame == "rest":
        verts = getattr(field, "rest_vertices", getattr(field, "vertices", None))
        handles = getattr(field, "rest_handles", getattr(field, "handles", None))
    else:
        verts = getattr(field, "vertices", None)
        handles = getattr(field, "handles", None)
    for j in range(k):
        a, b = ring_indices[j], ring_indices[(j + 1) % k]
        ei = field._edge_lookup.get(frozenset((a, b)))
        if ei is None:
            # Non-manifold face: fall back to a straight chord.
            va, vb = verts[a], verts[b]
            out[2 * j] = va + (vb - va) / 3.0
            out[2 * j + 1] = va + 2.0 * (vb - va) / 3.0
            continue
        h0, h1 = 2 * ei, 2 * ei + 1
        if field._edges[ei][0] != a:
            h0, h1 = h1, h0
        out[2 * j] = handles[h0]
        out[2 * j + 1] = handles[h1]
    return out


def ring_rest_handles(field, ring_indices):
    """(2k,3) rest-frame boundary handles of one cage face. See `ring_handles`."""
    return ring_handles(field, ring_indices, frame="rest")
