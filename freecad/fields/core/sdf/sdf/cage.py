# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField, _GLSL_APPLY_INV_MAT
import math
import numpy as np
from collections import defaultdict, deque

# Re-exported so every existing `from freecad.fields.core.sdf.sdf.cage import X` keeps working
# after the ST-006 split. Do not prune this list without grepping first --
# several entries are private names imported by tools and tests.
from freecad.fields.core.sdf.sdf.cage_topology import (  # noqa: F401
    MAX_CAGE_FACES, HandleType, Vertex, Face, HalfEdge, CageTopology,
    _BOX_EDGES, _BOX_FACES, _BOX_EDGE_LOOKUP,
    _OCT_EDGES, _OCT_FACES, _OCT_EDGE_LOOKUP,
)
from freecad.fields.core.sdf.sdf.cage_patch_math import (  # noqa: F401
    _bez3, _bez3d, _coons_eval, _coons_grad, _hermite,
    _bez3_b, _bez3d_b, _coons_grad_b, _hermite_b,
    _bicubic_coons_grad, _bicubic_coons_grad_b,
    _gregory_grad, _gregory_grad_b,
    _bez_tri3, _bez_tri3_b, _bez_tri3_homo_grads, _bez_tri3_homo_grads_b,
    _eval_patch, _eval_patch_grad, _eval_patch_grad_b,
    _closest_on_face, _closest_on_tri_face,
    _closest_on_bilinear_batch, _closest_on_face_batch, _closest_on_tri_face_batch,
)

class BVHNode:
    def __init__(self, face_indices, bmin, bmax):
        self.face_indices = face_indices
        self.bmin = bmin
        self.bmax = bmax
        self.left = None
        self.right = None
        self.face_count = 0


class SdfCageField(SdfField):
    """Closed patch-surface SDF.

    Supports both quad faces (cubic Bezier Coons patches) and triangular faces
    (degree-3 Bezier triangles).

    Args:
        vertices:    (N, 3) float64 array of vertex positions.
        handles:     (2*E, 3) float64 array; handles[2*i] and handles[2*i+1] are
                     the two inner Bezier control points for edge i in canonical
                     direction (vi→vj per the edges list).
        face_verts:  flat list of vertex indices per face.
        face_sizes:  list of vertex counts per face (3 = tri, 4 = quad).
        edges:       list of (vi, vj) pairs defining canonical edge directions.
                     Defaults to _BOX_EDGES if None.
        placement:   FreeCAD.Placement or None.
    """

    def __init__(self, vertices, handles, face_verts, face_sizes,
                 edges=None, placement=None, handle_types=None, edge_straight=None):
        super().__init__()
        v_arr = np.array(vertices, dtype=np.float64)
        h_arr = np.array(handles,  dtype=np.float64)
        self.vertices = v_arr.reshape((-1, 3))
        self.handles  = h_arr.reshape((-1, 3))
        self.placement = placement
        self.sign_mode = "closest"

        self.topology = CageTopology(face_verts, face_sizes, self.vertices)
        self._edges = list(edges) if edges is not None else self.topology.edges
        self._edge_lookup = {frozenset(e): i for i, e in enumerate(self._edges)}

        # Handle coupling data model
        n_handles = len(self.handles)
        if handle_types is None:
            self._handle_types = [HandleType.LINKED] * n_handles
        else:
            self._handle_types = [int(ht) for ht in handle_types]
            if len(self._handle_types) < n_handles:
                self._handle_types += [HandleType.LINKED] * (n_handles - len(self._handle_types))

        # Edge straight data model
        n_edges = len(self._edges)
        if edge_straight is None:
            self._edge_straight = []
            for ei, (vi, vj) in enumerate(self._edges):
                if vi < len(self.vertices) and vj < len(self.vertices):
                    va = self.vertices[vi]
                    vb = self.vertices[vj]
                    h0 = self.handles[2 * ei]
                    h1 = self.handles[2 * ei + 1]
                    target_h0 = va + (vb - va) / 3.0
                    target_h1 = va + 2.0 * (vb - va) / 3.0
                    is_s0 = np.allclose(h0, target_h0, atol=1e-5)
                    is_s1 = np.allclose(h1, target_h1, atol=1e-5)
                    self._edge_straight.append(is_s0 and is_s1)
                else:
                    self._edge_straight.append(False)
        else:
            self._edge_straight = [bool(es) for es in edge_straight]
            if len(self._edge_straight) < n_edges:
                self._edge_straight += [False] * (n_edges - len(self._edge_straight))

        # Enforce exact chord positions for straight edges
        for ei, is_straight in enumerate(self._edge_straight):
            if is_straight:
                va, vb = self._edges[ei]
                self.handles[2 * ei] = self.vertices[va] + (self.vertices[vb] - self.vertices[va]) / 3.0
                self.handles[2 * ei + 1] = self.vertices[va] + 2.0 * (self.vertices[vb] - self.vertices[va]) / 3.0

        self._vert_handles = {vi: [] for vi in range(len(self.vertices))}
        self._handle_offsets = np.zeros_like(self.handles)

        for ei, (vi, vj) in enumerate(self._edges):
            # Near handle belongs to vi
            h0_idx = 2 * ei
            if vi < len(self.vertices):
                self._vert_handles[vi].append(h0_idx)
                self._handle_offsets[h0_idx] = self.handles[h0_idx] - self.vertices[vi]
            # Far handle belongs to vj
            h1_idx = 2 * ei + 1
            if vj < len(self.vertices):
                self._vert_handles[vj].append(h1_idx)
                self._handle_offsets[h1_idx] = self.handles[h1_idx] - self.vertices[vj]

        idx = 0
        self._face_verts = []
        for sz in face_sizes:
            self._face_verts.append(face_verts[idx:idx+sz])
            idx += sz

        self.inv_matrix = self._compute_inv_matrix(placement)
        self.recompute_face_aabbs()
        self.geometry_version = 0
        self.hot_faces = frozenset()

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def from_patches(cls, patch_specs, weld_tol=1e-3, sign_mode="winding", placement=None):
        """Weld independent patches into a single cage field.

        A cage face is a cubic Coons / Bézier-triangle patch (4 corners + 2 cubic handles per edge + one interior term).
        Cage faces should be authored from curve loops, not converted from arbitrary high-order NURBS FldSurface grids —
        a general 8x8 NURBS surface is not a single cubic patch and will only be approximated.

        Args:
            patch_specs: List of dicts, each with:
                         - "corners": (K, 3) list or array of corner points (K is 3 or 4)
                         - "handles": optional (2*K, 3) list or array of cubic handles for the K edges.
                                      For edge i (from corner i to corner (i+1)%K), handles are at
                                      index 2*i (near corner i) and 2*i+1 (near corner (i+1)%K).
            weld_tol:    Vertex welding tolerance (mm).
            sign_mode:   "winding" or "closest".
            placement:   Optional FreeCAD.Placement.
        """
        import numpy as np

        # 1. Weld corner vertices
        vertices = []
        def get_or_create_vertex(pt):
            pt_np = np.asarray(pt, dtype=np.float64)
            for idx, v in enumerate(vertices):
                if np.allclose(v, pt_np, atol=weld_tol):
                    return idx
            vertices.append(pt_np)
            return len(vertices) - 1

        face_verts = []
        face_sizes = []

        global_edges = []
        global_edge_lookup = {}  # frozenset((vi, vj)) -> global_edge_index
        global_handles = {}      # global_edge_index -> (h0, h1)
        global_edge_straight = {} # global_edge_index -> bool

        for spec in patch_specs:
            corners = np.asarray(spec["corners"], dtype=np.float64)
            K = len(corners)
            if K not in (3, 4):
                raise ValueError("Only triangular (3) or quad (4) patches are supported.")

            # Weld corners
            mapped_indices = [get_or_create_vertex(c) for c in corners]
            face_verts.extend(mapped_indices)
            face_sizes.append(K)

            spec_handles = spec.get("handles")
            if spec_handles is not None:
                spec_handles = np.asarray(spec_handles, dtype=np.float64)
                if len(spec_handles) != 2 * K:
                    raise ValueError(f"Expected {2*K} handles for patch with {K} corners.")

            for i in range(K):
                vi = mapped_indices[i]
                vj = mapped_indices[(i + 1) % K]
                edge_key = frozenset((vi, vj))

                if edge_key not in global_edge_lookup:
                    ei = len(global_edges)
                    global_edges.append((vi, vj))
                    global_edge_lookup[edge_key] = ei

                    if spec_handles is not None:
                        h_near_vi = spec_handles[2 * i]
                        h_near_vj = spec_handles[2 * i + 1]
                        global_handles[ei] = (h_near_vi, h_near_vj)
                        global_edge_straight[ei] = False
                    else:
                        p_vi = vertices[vi]
                        p_vj = vertices[vj]
                        h_near_vi = p_vi + (p_vj - p_vi) / 3.0
                        h_near_vj = p_vi + 2.0 * (p_vj - p_vi) / 3.0
                        global_handles[ei] = (h_near_vi, h_near_vj)
                        global_edge_straight[ei] = True
                else:
                    # Edge already exists, if this spec provides handles we can use them
                    ei = global_edge_lookup[edge_key]
                    if spec_handles is not None:
                        va, vb = global_edges[ei]
                        h_near_vi = spec_handles[2 * i]
                        h_near_vj = spec_handles[2 * i + 1]
                        if va == vi:
                            global_handles[ei] = (h_near_vi, h_near_vj)
                        else:
                            global_handles[ei] = (h_near_vj, h_near_vi)
                        global_edge_straight[ei] = False

        vertices_arr = np.array(vertices, dtype=np.float64)
        n_edges = len(global_edges)

        handles_list = []
        edge_straight_list = []
        for ei in range(n_edges):
            h0, h1 = global_handles[ei]
            handles_list.append(h0)
            handles_list.append(h1)
            edge_straight_list.append(global_edge_straight[ei])

        handles_arr = np.array(handles_list, dtype=np.float64)

        field = cls(
            vertices=vertices_arr,
            handles=handles_arr,
            face_verts=face_verts,
            face_sizes=face_sizes,
            edges=global_edges,
            placement=placement,
            edge_straight=edge_straight_list
        )
        field.sign_mode = sign_mode
        return field

    @classmethod
    def from_aabb(cls, min_pt, max_pt, placement=None):
        x0, y0, z0 = (min_pt.x, min_pt.y, min_pt.z) if hasattr(min_pt, "x") else min_pt
        x1, y1, z1 = (max_pt.x, max_pt.y, max_pt.z) if hasattr(max_pt, "x") else max_pt
        verts = np.array([
            [x0,y0,z0],[x1,y0,z0],[x1,y1,z0],[x0,y1,z0],
            [x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1],
        ], dtype=np.float64)
        handles = np.zeros((24, 3), dtype=np.float64)
        for i, (vi, vj) in enumerate(_BOX_EDGES):
            handles[2*i]   = verts[vi] + (verts[vj]-verts[vi]) / 3.0
            handles[2*i+1] = verts[vi] + (verts[vj]-verts[vi]) * 2.0/3.0
        fv = [v for face in _BOX_FACES for v in face]
        fs = [len(f) for f in _BOX_FACES]
        return cls(verts, handles, fv, fs, edges=_BOX_EDGES, placement=placement)

    @classmethod
    def from_box(cls, box_field):
        c = box_field.center; h = box_field.half_size
        return cls.from_aabb(
            [c.x-h.x, c.y-h.y, c.z-h.z],
            [c.x+h.x, c.y+h.y, c.z+h.z],
            placement=box_field.placement,
        )

    @classmethod
    def from_sphere(cls, sphere_field):
        """8 curved triangular patches (octahedron topology) approximating the sphere.

        Each edge is a 90° great-circle arc; handles use the cubic Bezier arc
        constant k = (4/3)·tan(π/8).  Interior B111 of each triangular patch is
        the average of its six surrounding edge handles, which pulls the patch
        surface toward the sphere.
        """
        c = sphere_field.center
        r = sphere_field.radius
        cx, cy, cz = c.x, c.y, c.z

        # Octahedron vertices on sphere surface
        verts = np.array([
            [cx + r, cy,     cz    ],  # v0: +x
            [cx - r, cy,     cz    ],  # v1: -x
            [cx,     cy + r, cz    ],  # v2: +y
            [cx,     cy - r, cz    ],  # v3: -y
            [cx,     cy,     cz + r],  # v4: +z
            [cx,     cy,     cz - r],  # v5: -z
        ], dtype=np.float64)

        center  = np.array([cx, cy, cz], dtype=np.float64)
        normals = (verts - center) / r   # unit normals per vertex

        # Each edge subtends 90°; cubic Bezier arc constant
        k = (4.0 / 3.0) * math.tan(math.pi / 8.0)

        handles = np.zeros((24, 3), dtype=np.float64)
        for ei, (vi, vj) in enumerate(_OCT_EDGES):
            n0, n3 = normals[vi], normals[vj]
            t0 = n3 - np.dot(n3, n0) * n0;  t0 /= np.linalg.norm(t0)
            t3 = n0 - np.dot(n0, n3) * n3;  t3 /= np.linalg.norm(t3)
            handles[2*ei]     = verts[vi] + r * k * t0
            handles[2*ei + 1] = verts[vj] + r * k * t3

        fv = [v for face in _OCT_FACES for v in face]
        fs = [len(f) for f in _OCT_FACES]

        handle_types = [HandleType.ALIGNED] * 24

        return cls(verts, handles, fv, fs, edges=_OCT_EDGES,
                   placement=sphere_field.placement, handle_types=handle_types)

    @classmethod
    def from_cylinder(cls, cyl_field):
        """6-patch cage approximating the cylinder (4 curved sides + 2 flat caps)."""
        bc = cyl_field.base_center
        ax_fc = cyl_field.axis
        r = cyl_field.radius
        h = cyl_field.height

        ax = np.array([ax_fc.x, ax_fc.y, ax_fc.z], dtype=np.float64)
        ref = np.array([1.0, 0.0, 0.0]) if abs(ax[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        u = np.cross(ax, ref); u /= np.linalg.norm(u)
        v = np.cross(ax, u)

        ang = [math.radians(a) for a in (225.0, 315.0, 45.0, 135.0)]
        bc_arr = np.array([bc.x, bc.y, bc.z], dtype=np.float64)
        h_vec  = h * ax

        verts = np.zeros((8, 3), dtype=np.float64)
        for i, rad in enumerate(ang):
            off = r * (math.cos(rad)*u + math.sin(rad)*v)
            verts[i]     = bc_arr + off
            verts[i + 4] = bc_arr + off + h_vec

        k_ring = (4.0 / 3.0) * math.tan(math.pi / 8.0)

        handles = np.zeros((24, 3), dtype=np.float64)
        for ei, (vi, vj) in enumerate(_BOX_EDGES):
            if ei < 8:
                bi = vi if vi < 4 else vi - 4
                bj = vj if vj < 4 else vj - 4
                ri, rj = ang[bi], ang[bj]
                t_i = -math.sin(ri)*u + math.cos(ri)*v
                t_j =  math.sin(rj)*u - math.cos(rj)*v
                handles[2*ei]     = verts[vi] + r * k_ring * t_i
                handles[2*ei + 1] = verts[vj] + r * k_ring * t_j
            else:
                handles[2*ei]     = verts[vi] + (verts[vj]-verts[vi]) / 3.0
                handles[2*ei + 1] = verts[vi] + (verts[vj]-verts[vi]) * 2.0/3.0

        fv = [v for face in _BOX_FACES for v in face]
        fs = [len(f) for f in _BOX_FACES]

        handle_types = [HandleType.LINKED] * 24
        # Set top/bottom ring handles (indices 0..15) to ALIGNED
        for idx in range(16):
            handle_types[idx] = HandleType.ALIGNED

        return cls(verts, handles, fv, fs, edges=_BOX_EDGES,
                   placement=cyl_field.placement, handle_types=handle_types)

    @classmethod
    def from_torus(cls, torus_field):
        """16 curved quad patches wrapping the torus (4 major × 4 tube segments).

        Genus-1 net: the major ring is sampled at 4 angles (u) and the tube at 4
        angles (v), a 4×4 grid of quad patches wrapping in BOTH directions. Every
        edge is a 90° circular arc, so handles use the cubic-Bezier arc constant
        k = (4/3)·tan(π/8) (as from_sphere / from_cylinder), scaled by the radius
        of the circle the edge lives on: ρ_j = R + r·cos v_j for major edges, r
        for tube edges. Ring in XY, Z up — matches SdfTorusField.evaluate.
        """
        c = torus_field.center
        R = torus_field.major_radius
        r = torus_field.tube_radius
        cx, cy, cz = c.x, c.y, c.z

        NU = NV = 4
        us = [i * (2.0 * math.pi / NU) for i in range(NU)]  # major angles
        vs = [j * (2.0 * math.pi / NV) for j in range(NV)]  # tube angles

        def vid(i, j):
            return (i % NU) * NV + (j % NV)

        # Vertices on the surface -------------------------------------------
        verts = np.zeros((NU * NV, 3), dtype=np.float64)
        for i in range(NU):
            cu, su = math.cos(us[i]), math.sin(us[i])
            for j in range(NV):
                rho = R + r * math.cos(vs[j])
                verts[vid(i, j)] = [cx + rho * cu, cy + rho * su,
                                    cz + r * math.sin(vs[j])]

        # Faces: quads, CCW from outside ------------------------------------
        # s: v0→v1 = +u (major), t: v0→v3 = +v (tube); (v1-v0)×(v3-v0) outward.
        faces = []
        for i in range(NU):
            for j in range(NV):
                faces.append([vid(i, j), vid(i + 1, j),
                              vid(i + 1, j + 1), vid(i, j + 1)])

        # Edges: all major (along u) first, then all tube (along v) ----------
        edges = []
        for i in range(NU):
            for j in range(NV):
                edges.append((vid(i, j), vid(i + 1, j)))
        for i in range(NU):
            for j in range(NV):
                edges.append((vid(i, j), vid(i, j + 1)))

        k = (4.0 / 3.0) * math.tan(math.pi / 8.0)

        def tan_u(i):  # unit ∂/∂u tangent at major angle i
            return np.array([-math.sin(us[i % NU]), math.cos(us[i % NU]), 0.0])

        def tan_v(i, j):  # unit ∂/∂v tangent at (u_i, v_j)
            cu, su = math.cos(us[i % NU]), math.sin(us[i % NU])
            sv, cv = math.sin(vs[j % NV]), math.cos(vs[j % NV])
            return np.array([-sv * cu, -sv * su, cv])

        handles = np.zeros((2 * len(edges), 3), dtype=np.float64)
        ei = 0
        for i in range(NU):          # major edges (radius ρ_j)
            for j in range(NV):
                rho = R + r * math.cos(vs[j])
                vi, vj = vid(i, j), vid(i + 1, j)
                handles[2 * ei]     = verts[vi] + rho * k * tan_u(i)
                handles[2 * ei + 1] = verts[vj] - rho * k * tan_u(i + 1)
                ei += 1
        for i in range(NU):          # tube edges (radius r)
            for j in range(NV):
                vi, vj = vid(i, j), vid(i, j + 1)
                handles[2 * ei]     = verts[vi] + r * k * tan_v(i, j)
                handles[2 * ei + 1] = verts[vj] - r * k * tan_v(i, j + 1)
                ei += 1

        fv = [v for face in faces for v in face]
        fs = [len(f) for f in faces]
        handle_types = [HandleType.ALIGNED] * (2 * len(edges))

        return cls(verts, handles, fv, fs, edges=edges,
                   placement=torus_field.placement, handle_types=handle_types)

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def bounding_box(self):
        all_pts = np.vstack([self.vertices, self.handles])
        if self.placement is not None:
            pl = self.placement
            world_pts = [pl.multVec(FreeCAD.Vector(float(p[0]),float(p[1]),float(p[2]))) for p in all_pts]
        else:
            world_pts = [FreeCAD.Vector(float(p[0]),float(p[1]),float(p[2])) for p in all_pts]
        
        xs = [p.x for p in world_pts]
        ys = [p.y for p in world_pts]
        zs = [p.z for p in world_pts]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dz = max(zs) - min(zs)
        diagonal = math.sqrt(dx*dx + dy*dy + dz*dz)
        pad = self.compute_bounding_box_margin(diagonal)
        
        return (
            FreeCAD.Vector(min(xs)-pad, min(ys)-pad, min(zs)-pad),
            FreeCAD.Vector(max(xs)+pad, max(ys)+pad, max(zs)+pad),
        )

    def _cage_triangles(self):
        """Flat triangle soup for the winding-number sign test, made edge-coherent and
        globally outward-facing.

        The solid-angle sum in :meth:`_compute_winding_number` is a winding number only on
        a closed, consistently oriented mesh; on an incoherent one the per-triangle
        contributions cancel and every interior point reads ~0, i.e. "outside". Patch specs
        arrive one source face at a time, each wound however its own decomposer happened to
        emit it, so coherence is imposed here -- once, on the mesh that actually needs it --
        rather than asked of every surface-type decomposer separately.

        Orientation is propagated across shared edges by index, so it is exact rather than
        tolerance-dependent. The final global flip keys off total signed volume, which fixes
        the dominant shell; a mesh whose components enclose voids is not resolved by that
        alone, since telling a void from a separate solid needs a containment test.
        """
        cached = getattr(self, "_winding_tris", None)
        if cached is not None:
            return cached

        tris = []
        for f in self._face_verts:
            if len(f) == 3:
                tris.append([f[0], f[1], f[2]])
            elif len(f) == 4:
                tris.append([f[0], f[1], f[2]])
                tris.append([f[0], f[2], f[3]])

        if not tris:
            self._winding_tris = np.zeros((0, 3, 3), dtype=np.float64)
            return self._winding_tris

        edge_tris = defaultdict(list)
        for ti, t in enumerate(tris):
            for k in range(3):
                edge_tris[frozenset((t[k], t[(k + 1) % 3]))].append(ti)

        seen = [False] * len(tris)
        for start in range(len(tris)):
            if seen[start]:
                continue
            seen[start] = True
            queue = deque([start])
            while queue:
                ti = queue.popleft()
                t = tris[ti]
                for k in range(3):
                    a, b = t[k], t[(k + 1) % 3]
                    shared = edge_tris[frozenset((a, b))]
                    if len(shared) != 2:
                        # Non-manifold or boundary edge: orientation cannot be propagated
                        # across it. Hole triangulation bridges the outer loop to each inner
                        # loop, so a well-formed capped face legitimately carries edges with
                        # four incident triangles; walking through one flips a coherent
                        # neighbourhood into an incoherent one.
                        continue
                    for tj in shared:
                        if tj == ti or seen[tj]:
                            continue
                        u = tris[tj]
                        # Coherent neighbours walk their shared edge in opposite directions.
                        if not any(u[m] == b and u[(m + 1) % 3] == a for m in range(3)):
                            tris[tj] = [u[0], u[2], u[1]]
                        seen[tj] = True
                        queue.append(tj)

        T = self.vertices[np.asarray(tris, dtype=np.int64)]
        vol = float(np.einsum("ij,ij->i", np.cross(T[:, 0], T[:, 1]), T[:, 2]).sum())
        if vol < 0.0:
            T = T[:, ::-1, :]
        self._winding_tris = np.ascontiguousarray(T, dtype=np.float64)  # (T, 3, 3)
        return self._winding_tris

    def _compute_winding_number(self, q):
        T_pts = self._cage_triangles()
        if len(T_pts) == 0:
            return np.zeros(len(q), dtype=np.float64)
        
        N = len(q)
        a = T_pts[None, :, 0, :] - q[:, None, :]
        b = T_pts[None, :, 1, :] - q[:, None, :]
        c = T_pts[None, :, 2, :] - q[:, None, :]
        
        al = np.linalg.norm(a, axis=-1)
        bl = np.linalg.norm(b, axis=-1)
        cl = np.linalg.norm(c, axis=-1)
        
        det = np.sum(np.cross(a, b) * c, axis=-1)
        denom = al * bl * cl + np.sum(a * b, axis=-1) * cl + np.sum(b * c, axis=-1) * al + np.sum(c * a, axis=-1) * bl
        
        omega = 2.0 * np.arctan2(det, denom)
        omega_sum = np.sum(omega, axis=1)
        return omega_sum / (4.0 * np.pi)

    def recompute_face_aabbs(self):
        self._winding_tris = None
        self._face_aabbs = []
        self._face_cached_ctrl_pts = []
        for fi in range(len(self._face_verts)):
            sz = len(self._face_verts[fi])
            if sz == 4:
                C0, C1, D0, D1 = self._face_curve_handles(fi)
                pts = np.vstack([C0, C1, D0, D1])
                self._face_cached_ctrl_pts.append((C0, C1, D0, D1))
            elif sz == 3:
                ctrl = self._face_tri_ctrl_pts(fi)
                pts = np.vstack(ctrl)
                self._face_cached_ctrl_pts.append(ctrl)
            else:
                self._face_cached_ctrl_pts.append(None)
                pts = self.vertices[self._face_verts[fi]]
            bmin = np.min(pts, axis=0)
            bmax = np.max(pts, axis=0)
            self._face_aabbs.append((bmin, bmax))

        # CP-019: Cache whole-cage AABB
        if len(self.vertices) > 0:
            all_pts = np.vstack([self.vertices, self.handles])
            self._cage_aabb = (np.min(all_pts, axis=0), np.max(all_pts, axis=0))
        else:
            self._cage_aabb = None


    def _edge_bezier_for_pair(self, vi, vj):
        """Return (P0, P1, P2, P3) cubic Bezier from vertices[vi] to vertices[vj]."""
        key = frozenset((vi, vj))
        ei = self._edge_lookup[key]
        ea, _eb = self._edges[ei]
        h0 = self.handles[2*ei];  h1 = self.handles[2*ei+1]
        P0 = self.vertices[vi];   P3 = self.vertices[vj]
        if ea == vi:
            return P0, h0, h1, P3   # forward
        else:
            return P0, h1, h0, P3   # reversed

    def _face_curve_handles(self, fi):
        """Coons curve handles for a quad face [v0,v1,v2,v3].

        Returns (C0, C1, D0, D1) each as (P0,P1,P2,P3).
          C0: v0→v1  (s-direction, t=0)
          C1: v3→v2  (s-direction, t=1)
          D0: v0→v3  (t-direction, s=0)
          D1: v1→v2  (t-direction, s=1)
        """
        v = self._face_verts[fi]
        v0, v1, v2, v3 = v[0], v[1], v[2], v[3]
        C0 = self._edge_bezier_for_pair(v0, v1)
        C1 = self._edge_bezier_for_pair(v3, v2)
        D0 = self._edge_bezier_for_pair(v0, v3)
        D1 = self._edge_bezier_for_pair(v1, v2)
        return C0, C1, D0, D1

    def _face_tri_ctrl_pts(self, fi):
        """Degree-3 Bezier triangle control points for a triangular face [v0,v1,v2].

        Returns: (B300, B030, B003, B210, B120, B021, B012, B201, B102, B111)
        B111 is auto-computed as a center-calibrated affine combination of the six edge handles and three corners.
        """
        v = self._face_verts[fi]
        v0, v1, v2 = v[0], v[1], v[2]

        B300 = self.vertices[v0]
        B030 = self.vertices[v1]
        B003 = self.vertices[v2]

        # Edge v0→v1: handles at 1/3 (B210) and 2/3 (B120)
        _, B210, B120, _ = self._edge_bezier_for_pair(v0, v1)
        # Edge v1→v2: handles at 1/3 (B021) and 2/3 (B012)
        _, B021, B012, _ = self._edge_bezier_for_pair(v1, v2)
        # Edge v2→v0: first handle from v2 = B102, second = B201
        _, B102, B201, _ = self._edge_bezier_for_pair(v2, v0)

        # Interior: affine combination of edge handles and corners. The
        # coefficients satisfy 6*alpha - 3*beta = 1 (affine invariance) and are
        # calibrated so a from_sphere() octant patch stays within 0.4% of the
        # true sphere radius (the previous 0.375 / 5/12 pair sagged patch
        # centers by 5.1%).
        _B111_ALPHA = 0.494121
        _B111_BETA = 0.654910
        B111 = _B111_ALPHA * (B210 + B120 + B021 + B012 + B201 + B102) - _B111_BETA * (B300 + B030 + B003)

        return B300, B030, B003, B210, B120, B021, B012, B201, B102, B111

    # ── SDF evaluation ────────────────────────────────────────────────────────

    def evaluate(self, point, patch_type=None, simplify=None):
        if isinstance(point, np.ndarray):
            q = point
        else:
            lp = self._to_local_point(point)
            q = np.array([lp.x, lp.y, lp.z], dtype=np.float64)
        
        if q.ndim == 1:
            q_batch = q[None, :]
        else:
            q_batch = q
            
        res = self._evaluate_local_grid(q_batch, patch_type=patch_type, simplify=simplify)
        return float(res[0])

    def _evaluate_local_grid(self, q, patch_type=None, simplify=None):
        if patch_type is None:
            from freecad.fields.core.objects.fld_object import get_cage_patch_type
            patch_type = get_cage_patch_type()
        if simplify is None:
            simplify = getattr(self, "simplify", False)

        N = len(q)
        if N == 0:
            return np.zeros(0, dtype=np.float32)

        best_d = np.full(N, 1e30, dtype=np.float64)
        best_signed = np.full(N, 1e30, dtype=np.float64)

        # CP-019: Far-field AABB shortcut
        FAR_MARGIN = 5.0
        if getattr(self, "_cage_aabb", None) is not None:
            bmin, bmax = self._cage_aabb
            diff_min = bmin[None, :] - q
            diff_max = q - bmax[None, :]
            d_outer = np.sqrt(np.sum(np.maximum(0.0, np.maximum(diff_min, diff_max))**2, axis=-1))
            far = d_outer > FAR_MARGIN
            if far.any():
                best_d[far] = d_outer[far]
                best_signed[far] = d_outer[far]
                if far.all():
                    return best_signed.astype(np.float32)

        if not self._face_aabbs:
            return np.zeros(N, dtype=np.float32)

        bmins = np.array([aabb[0] for aabb in self._face_aabbs])
        bmaxs = np.array([aabb[1] for aabb in self._face_aabbs])

        diff_min = bmins[None, :, :] - q[:, None, :]
        diff_max = q[:, None, :] - bmaxs[None, :, :]
        d_box = np.sqrt(np.sum(np.maximum(0.0, np.maximum(diff_min, diff_max))**2, axis=-1))

        face_order = np.argsort(d_box.min(axis=0))

        _missing_edges = 0
        for fi in face_order:
            mask = d_box[:, fi] < best_d
            if not mask.any():
                continue

            active_q = q[mask]
            sz = len(self._face_verts[fi])
            if sz == 4:
                is_face_straight = False
                if getattr(self, "_edge_straight", None) is not None:
                    v0, v1, v2, v3 = self._face_verts[fi]
                    try:
                        e0 = self._edge_lookup[frozenset((v0, v1))]
                        e1 = self._edge_lookup[frozenset((v3, v2))]
                        e2 = self._edge_lookup[frozenset((v0, v3))]
                        e3 = self._edge_lookup[frozenset((v1, v2))]
                        if self.is_edge_straight(e0) and self.is_edge_straight(e1) and self.is_edge_straight(e2) and self.is_edge_straight(e3):
                            is_face_straight = True
                    except KeyError:
                        _missing_edges += 1
                
                if is_face_straight:
                    v0, v1, v2, v3 = self._face_verts[fi]
                    hit, norm, dist = _closest_on_bilinear_batch(
                        self.vertices[v0], self.vertices[v1], self.vertices[v2], self.vertices[v3],
                        active_q, simplify=simplify
                    )
                else:
                    C0, C1, D0, D1 = self._face_cached_ctrl_pts[fi]
                    hit, norm, dist = _closest_on_face_batch(C0, C1, D0, D1, active_q, patch_type=patch_type, simplify=simplify)
            elif sz == 3:
                ctrl = self._face_cached_ctrl_pts[fi]
                hit, norm, dist = _closest_on_tri_face_batch(ctrl, active_q, simplify=simplify)
            else:
                continue

            better = dist < best_d[mask]
            if better.any():
                better_q = active_q[better]
                better_hit = hit[better]
                better_norm = norm[better]

                if getattr(self, "sign_mode", "closest") == "winding":
                    w = self._compute_winding_number(better_q)
                    winding_sign = np.where(w >= 0.5, -1.0, 1.0)
                    closest_sign = np.where(((better_q - better_hit) * better_norm).sum(-1) >= 0.0, 1.0, -1.0)
                    blend_mask = np.abs(w - 0.5) < 0.1
                    sign = np.where(blend_mask, closest_sign, winding_sign)
                else:
                    sign = np.where(((better_q - better_hit) * better_norm).sum(-1) >= 0.0, 1.0, -1.0)
                better_signed = dist[better] * sign

                idx = np.flatnonzero(mask)[better]
                best_d[idx] = dist[better]
                best_signed[idx] = better_signed

        if _missing_edges:
            from freecad.fields.core import fld_logger
            fld_logger.debug(
                f"SdfCageField._evaluate_local_grid: {_missing_edges} face(s) had no "
                f"edge-lookup entry; treated as curved."
            )

        return best_signed.astype(np.float32)

    def evaluate_grid(self, points):
        local_pts = self._to_local_grid(points).astype(np.float64)
        return self._evaluate_local_grid(local_pts)

    def subset_copy(self, exclude_faces):
        """Returns a new SdfCageField with the specified faces dropped."""
        new_face_verts = []
        new_face_sizes = []
        for fi, f in enumerate(self._face_verts):
            if fi in exclude_faces:
                continue
            new_face_verts.extend(f)
            new_face_sizes.append(len(f))
        
        field_copy = SdfCageField(
            vertices=self.vertices.copy(),
            handles=self.handles.copy(),
            face_verts=new_face_verts,
            face_sizes=new_face_sizes,
            edges=list(self._edges),
            placement=self.placement,
            handle_types=list(self._handle_types),
            edge_straight=list(self._edge_straight) if hasattr(self, "_edge_straight") else None
        )
        field_copy.simplify = getattr(self, "simplify", False)
        return field_copy

    def handle_owner(self, hi: int) -> int:
        """Vertex index that handle hi hangs off."""
        return self._edges[hi // 2][hi % 2]

    def clone_bumped(self):
        """Copy of this field with geometry_version incremented."""
        from freecad.fields.core.sdf.sdf.cage_net import CageNet
        net = CageNet.from_field(self)
        clone = SdfCageField(
            self.vertices.copy(), self.handles.copy(), net.face_verts_flat, net.face_sizes,
            edges=list(self._edges), placement=self.placement,
            handle_types=list(self._handle_types),
            edge_straight=list(self._edge_straight) if hasattr(self, "_edge_straight") else None)
        clone.sign_mode = getattr(self, "sign_mode", "closest")
        clone.simplify = False
        clone.hot_faces = frozenset()
        clone.geometry_version = getattr(self, "geometry_version", 0) + 1
        return clone

    def is_edge_straight(self, ei):
        """Returns True if the edge is straight. Checks cached status and validates
        against current handle coordinates to support direct handle mutation."""
        if not hasattr(self, "_edge_straight") or self._edge_straight is None:
            return False
        if not self._edge_straight[ei]:
            return False
        # Double check if handles have been mutated out of sync
        va, vb = self._edges[ei]
        h0 = self.handles[2 * ei]
        h1 = self.handles[2 * ei + 1]
        target_h0 = self.vertices[va] + (self.vertices[vb] - self.vertices[va]) / 3.0
        target_h1 = self.vertices[va] + 2.0 * (self.vertices[vb] - self.vertices[va]) / 3.0
        if np.allclose(h0, target_h0, atol=1e-5) and np.allclose(h1, target_h1, atol=1e-5):
            return True
        self._edge_straight[ei] = False
        return False





    def move_vertex(self, vi, delta):
        """Move vertex vi by delta, propagating to adjacent non-FREE handles."""
        delta_arr = np.array([delta.x, delta.y, delta.z], dtype=np.float64)
        self.vertices[vi] += delta_arr
        for hi in self._vert_handles.get(vi, []):
            ei = hi // 2
            if getattr(self, "_edge_straight", None) and self._edge_straight[ei]:
                continue
            if self._handle_types[hi] != HandleType.FREE:
                self.handles[hi] += delta_arr
            else:
                self._handle_offsets[hi] = self.handles[hi] - self.vertices[vi]

        # Recompute derived handles for straight edges
        if getattr(self, "_edge_straight", None):
            for ei, is_straight in enumerate(self._edge_straight):
                if is_straight:
                    va, vb = self._edges[ei]
                    self.handles[2 * ei] = self.vertices[va] + (self.vertices[vb] - self.vertices[va]) / 3.0
                    self.handles[2 * ei + 1] = self.vertices[va] + 2.0 * (self.vertices[vb] - self.vertices[va]) / 3.0
                    owner_vi = self._edges[ei][0] if (2 * ei % 2 == 0) else self._edges[ei][1]
                    self._handle_offsets[2 * ei] = self.handles[2 * ei] - self.vertices[owner_vi]
                    owner_vj = self._edges[ei][0] if ((2 * ei + 1) % 2 == 0) else self._edges[ei][1]
                    self._handle_offsets[2 * ei + 1] = self.handles[2 * ei + 1] - self.vertices[owner_vj]

        self.topology.rebuild_from_geometry(self.vertices)
        self.recompute_face_aabbs()

    def project_aligned_handles(self, vi):
        """Project all ALIGNED handles at vertex vi onto the tangent plane."""
        aligned_hi = [hi for hi in self._vert_handles.get(vi, []) if self._handle_types[hi] == HandleType.ALIGNED]
        if len(aligned_hi) < 2:
            return

        v_pos = self.vertices[vi]
        T_vectors = []
        for hi in aligned_hi:
            T_vectors.append(self.handles[hi] - v_pos)

        T_mat = np.array(T_vectors)
        try:
            _, _, Vh = np.linalg.svd(T_mat)
            normal = Vh[2]
            norm_len = np.linalg.norm(normal)
            if norm_len > 1e-8:
                normal = normal / norm_len
            else:
                return
        except Exception:
            return

        for hi in aligned_hi:
            T = self.handles[hi] - v_pos
            T_proj = T - np.dot(T, normal) * normal
            self.handles[hi] = v_pos + T_proj
            self._handle_offsets[hi] = T_proj

        # Recompute derived handles for straight edges
        if getattr(self, "_edge_straight", None):
            for ei, is_straight in enumerate(self._edge_straight):
                if is_straight:
                    va, vb = self._edges[ei]
                    self.handles[2 * ei] = self.vertices[va] + (self.vertices[vb] - self.vertices[va]) / 3.0
                    self.handles[2 * ei + 1] = self.vertices[va] + 2.0 * (self.vertices[vb] - self.vertices[va]) / 3.0
                    owner_vi = self._edges[ei][0] if (2 * ei % 2 == 0) else self._edges[ei][1]
                    self._handle_offsets[2 * ei] = self.handles[2 * ei] - self.vertices[owner_vi]
                    owner_vj = self._edges[ei][0] if ((2 * ei + 1) % 2 == 0) else self._edges[ei][1]
                    self._handle_offsets[2 * ei + 1] = self.handles[2 * ei + 1] - self.vertices[owner_vj]

        self.recompute_face_aabbs()

    def to_glsl(self, ctx, point_var="p"):
        raise NotImplementedError("SdfCageField analytical rendering is retired in favor of FFD solid deformation.")




def rebuild_handles_and_types(old_field, new_topo, vertex_map=None):
    """Rebuilds handles and handle_types arrays after a topology change.
    Matches new edges to old edges to preserve existing handle positions.
    """
    if vertex_map is None:
        vertex_map = lambda v: v
        
    new_edges = new_topo.edges
    n_edges = len(new_edges)
    new_handles = np.zeros((2*n_edges, 3), dtype=np.float64)
    new_types = [1] * (2*n_edges)  # Default LINKED = 1
    
    # Build a map from frozenset of old endpoints to old edge index
    old_edges_map = {}
    for old_ei, (vi, vj) in enumerate(old_field._edges):
        old_edges_map[frozenset((vi, vj))] = (old_ei, vi, vj)
        
    for new_ei, (vi, vj) in enumerate(new_edges):
        key = frozenset((vertex_map(vi), vertex_map(vj)))
        if key in old_edges_map:
            old_ei, old_vi, old_vj = old_edges_map[key]
            h0 = old_field.handles[2*old_ei]
            h1 = old_field.handles[2*old_ei+1]
            ht0 = old_field._handle_types[2*old_ei]
            ht1 = old_field._handle_types[2*old_ei+1]
            
            # Check orientation
            if vertex_map(vi) == old_vi:
                # Same direction
                h0_shifted = h0 + (new_topo.vertices[vi].pos - old_field.vertices[old_vi])
                h1_shifted = h1 + (new_topo.vertices[vj].pos - old_field.vertices[old_vj])
                new_handles[2*new_ei] = h0_shifted
                new_handles[2*new_ei+1] = h1_shifted
                new_types[2*new_ei] = ht0
                new_types[2*new_ei+1] = ht1
            else:
                # Reversed direction
                h0_shifted = h0 + (new_topo.vertices[vi].pos - old_field.vertices[old_vj])
                h1_shifted = h1 + (new_topo.vertices[vj].pos - old_field.vertices[old_vi])
                new_handles[2*new_ei] = h1_shifted
                new_handles[2*new_ei+1] = h0_shifted
                new_types[2*new_ei] = ht1
                new_types[2*new_ei+1] = ht0
        else:
            # New edge: initialize handles at 1/3 and 2/3
            pos_i = new_topo.vertices[vi].pos
            pos_j = new_topo.vertices[vj].pos
            new_handles[2*new_ei] = pos_i + (pos_j - pos_i) / 3.0
            new_handles[2*new_ei+1] = pos_i + (pos_j - pos_i) * 2.0 / 3.0
            new_types[2*new_ei] = 1      # LINKED
            new_types[2*new_ei+1] = 1    # LINKED

    return new_handles, new_types


def remap_handle_displacements(old_edges, old_handle_disp, new_edges, vertex_map=None):
    """Carry per-edge handle DISPLACEMENTS across a topology change.

    For a deform cage the topology (and thus new_edges' vertex positions) lives
    in the REST frame while the field's handles live in the CURRENT, deformed
    frame. rebuild_handles_and_types reconstructs absolute handle positions by
    shifting the old handle by (new_rest_pos - old_current_pos), which is correct
    only for the single-frame patch cage. For the deform cage that mixes frames
    and leaks the vertex displacement into the handle displacement
    (disp_h -> old_disp_h - old_disp_v), detonating the cage into spikes on the
    second edit (the first is harmless because an identity cage has disp_v == 0).

    Preserve the frame-independent handle displacement instead: matched edges
    keep their old displacement (orientation-aware), brand-new edges get zero
    (their handle sits at the rest 1/3, 2/3 position). Caller reconstructs
    curr_handles = rest_handles + this.
    """
    if vertex_map is None:
        vertex_map = lambda v: v
    old_map = {}
    for oe, (a, b) in enumerate(old_edges):
        old_map[frozenset((a, b))] = (oe, a, b)
    disp = np.zeros((2 * len(new_edges), 3), dtype=np.float64)
    for ne, (vi, vj) in enumerate(new_edges):
        a, b = vertex_map(vi), vertex_map(vj)
        hit = old_map.get(frozenset((a, b)))
        if hit is None:
            continue  # new edge -> zero handle displacement
        oe, oa, ob = hit
        if a == oa:
            disp[2 * ne]     = old_handle_disp[2 * oe]
            disp[2 * ne + 1] = old_handle_disp[2 * oe + 1]
        else:  # edge stored in reversed orientation -> swap the two handles
            disp[2 * ne]     = old_handle_disp[2 * oe + 1]
            disp[2 * ne + 1] = old_handle_disp[2 * oe]
    return disp
