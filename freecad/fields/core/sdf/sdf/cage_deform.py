# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
from freecad.fields.core.sdf.sdf_field import SdfField
from freecad.fields.core.sdf.sdf.cage import HandleType, _bez_tri3, _coons_eval, CageTopology
import numpy as np

# Subdivision level per cage face when building the Mean-Value-Coordinates
# deformation mesh. The raw octahedral/box net (corner vertices only) is far too
# coarse: a single corner drags the whole interior (MVC is global) and the curved
# arc handles are ignored entirely. Tessellating each Bézier face — driven by its
# corner verts AND its edge handles — into a denser triangle mesh makes the
# handles actually shape the surface and localises each control point's influence.
# S=2 adds the edge-midpoints (which depend on the handles); higher = more local
# but O(faces·S²) more MVC work per sample on the GPU.
#
# With discrete 3D warp baking (bake_warp_volume), _mvc_reconstruct runs at grid
# points during initial/dirty bake. S=1 keeps the bake fast while trilinear texture
# filtering smooths the deformation space.
_MVC_SUBDIV = 1


class SdfCageDeformField(SdfField):
    """
    Deform cage SDF field.
    Wraps a preserved source SDF field (e.g. SdfSphereField) with a closed
    triangular/quad control net derived from SdfCageField.from_sphere/box/cylinder.

    Phase 2: Mean Value Coordinates (3D MVC) cage deformation.
    Moving cage control points smoothly deforms the source SDF on both CPU and GPU GLSL.
    """

    def __init__(self, source: SdfField, vertices, handles, face_verts, edges,
                 handle_types=None, edge_straight=None, rest_vertices=None,
                 rest_handles=None, displacements=None, placement=None,
                 mvc_rest_verts=None, mvc_tris=None):
        super().__init__()
        self.source = source
        v_arr = np.array(vertices, dtype=np.float64)
        h_arr = np.array(handles, dtype=np.float64)
        self.vertices = v_arr.reshape((-1, 3))
        self.handles = h_arr.reshape((-1, 3))

        if rest_vertices is not None:
            rv_arr = np.array(rest_vertices, dtype=np.float64)
            self.rest_vertices = rv_arr.reshape((-1, 3))
        else:
            self.rest_vertices = self.vertices.copy()

        if rest_handles is not None:
            rh_arr = np.array(rest_handles, dtype=np.float64)
            self.rest_handles = rh_arr.reshape((-1, 3))
        else:
            self.rest_handles = self.handles.copy()

        self.placement = placement

        # Face vertices MUST be a list-of-lists (this class has no face_sizes
        # param, unlike SdfCageField, so a flat index list cannot be split back
        # into faces). Reject a flat list loudly instead of silently building a
        # hollow/garbage cage — a flat list would leave self._face_verts as bare
        # ints and blow up downstream (e.g. face_verts_flat below).
        if len(face_verts) > 0 and not isinstance(face_verts[0], (list, tuple, np.ndarray)):
            raise TypeError(
                "SdfCageDeformField.face_verts must be a list of per-face vertex "
                "lists (e.g. [[0,1,2], [3,4,5]]), not a flat index list — this "
                "class has no face_sizes parameter.")
        self._face_verts = [list(f) for f in face_verts]

        face_verts_flat = [v for f in self._face_verts for v in f]
        face_sizes = [len(f) for f in self._face_verts]
        self.topology = CageTopology(face_verts_flat, face_sizes, self.rest_vertices)

        self._edges = list(edges)
        self._edge_lookup = {frozenset(e): i for i, e in enumerate(self._edges)}

        # Fan-triangulate the raw corner net (kept for face-AABB / legacy callers).
        tris = []
        for face in self._face_verts:
            for i in range(1, len(face) - 1):
                tris.append((face[0], face[i], face[i + 1]))
        self._triangles = np.array(tris, dtype=np.int32)

        n_handles = len(self.handles)
        if handle_types is None:
            self._handle_types = [HandleType.LINKED] * n_handles
        else:
            self._handle_types = [int(ht) for ht in handle_types]
            if len(self._handle_types) < n_handles:
                self._handle_types += [HandleType.LINKED] * (n_handles - len(self._handle_types))

        n_edges = len(self._edges)
        if edge_straight is None:
            self._edge_straight = [False] * n_edges
        else:
            self._edge_straight = [bool(es) for es in edge_straight]
            if len(self._edge_straight) < n_edges:
                self._edge_straight += [False] * (n_edges - len(self._edge_straight))

        if displacements is not None:
            disp_arr = np.asarray(displacements, dtype=np.float64)
            if disp_arr.size > 0:
                disp_arr = disp_arr.reshape((-1, 3))
                nv = len(self.vertices)
                nh = len(self.handles)
                if len(disp_arr) >= nv + nh:
                    if np.allclose(self.vertices, self.rest_vertices) and np.allclose(self.handles, self.rest_handles):
                        self.vertices = self.rest_vertices + disp_arr[:nv]
                        self.handles = self.rest_handles + disp_arr[nv:nv + nh]

        # Handle indexing setup for CageEditTool compatibility
        self._vert_handles = {vi: [] for vi in range(len(self.vertices))}
        self._handle_offsets = np.zeros_like(self.handles)

        for ei, (vi, vj) in enumerate(self._edges):
            h0_idx = 2 * ei
            if vi < len(self.vertices):
                self._vert_handles[vi].append(h0_idx)
                self._handle_offsets[h0_idx] = self.handles[h0_idx] - self.vertices[vi]
            h1_idx = 2 * ei + 1
            if vj < len(self.vertices):
                self._vert_handles[vj].append(h1_idx)
                self._handle_offsets[h1_idx] = self.handles[h1_idx] - self.vertices[vj]

        # Tessellated MVC deformation mesh. The REST mesh + triangle topology are
        # fixed (rest net never changes during editing), so build them once here.
        # The DEFORMED mesh is rebuilt live from the current verts/handles in
        # _mvc_reconstruct()/to_glsl() so drags update immediately (the edit tool
        # mutates self.vertices/self.handles in place on the same field object).
        if mvc_rest_verts is not None and mvc_tris is not None:
            self._mvc_rest_verts, self._mvc_tris = mvc_rest_verts, mvc_tris
        else:
            self._mvc_rest_verts, self._mvc_tris = self._build_mvc_mesh(
                self.rest_vertices, self.rest_handles, build_tris=True)

        self._is_subtractive = getattr(self.source, "_is_subtractive", False)
        self.inv_matrix = self._compute_inv_matrix(placement)
        self.geometry_version = 0
        self.hot_faces = frozenset()
        self.recompute_face_aabbs()

    # ── Tessellated MVC cage (verts + handles → dense Bézier-face mesh) ────────

    def _edge_bezier(self, vertices, handles, vi, vj):
        """Cubic Bézier (P0,P1,P2,P3) from vertex vi to vj using its edge handles."""
        ei = self._edge_lookup[frozenset((vi, vj))]
        ea, _eb = self._edges[ei]
        h0 = handles[2 * ei]
        h1 = handles[2 * ei + 1]
        P0 = vertices[vi]
        P3 = vertices[vj]
        if ea == vi:
            return P0, h0, h1, P3      # forward
        return P0, h1, h0, P3          # reversed

    def _quad_ctrl_pts(self, vertices, handles, face):
        """Coons quad curve handles for a face [v0,v1,v2,v3]. Returns (C0, C1, D0, D1)."""
        v0, v1, v2, v3 = face[0], face[1], face[2], face[3]
        C0 = self._edge_bezier(vertices, handles, v0, v1)
        C1 = self._edge_bezier(vertices, handles, v3, v2)
        D0 = self._edge_bezier(vertices, handles, v0, v3)
        D1 = self._edge_bezier(vertices, handles, v1, v2)
        return C0, C1, D0, D1

    def _tri_ctrl_pts(self, vertices, handles, face):
        """10 degree-3 Bézier-triangle control points for a triangular face.
        Mirrors SdfCageField._face_tri_ctrl_pts (same B111 calibration)."""
        v0, v1, v2 = face[0], face[1], face[2]
        B300 = vertices[v0]; B030 = vertices[v1]; B003 = vertices[v2]
        _, B210, B120, _ = self._edge_bezier(vertices, handles, v0, v1)
        _, B021, B012, _ = self._edge_bezier(vertices, handles, v1, v2)
        _, B102, B201, _ = self._edge_bezier(vertices, handles, v2, v0)
        B111 = (0.494121 * (B210 + B120 + B021 + B012 + B201 + B102)
                - 0.654910 * (B300 + B030 + B003))
        return (B300, B030, B003, B210, B120, B021, B012, B201, B102, B111)

    def _build_mvc_mesh(self, vertices, handles, build_tris):
        """Tessellate every cage face (Bézier triangle / Coons quad) into a dense
        closed triangle mesh. Returns dense verts (M,3); when build_tris, also the
        (T,3) int32 triangle index list. Rest and deformed meshes share topology
        (same S, same face order), so their vertices correspond 1:1. Faces are
        tessellated independently (no welding): coincident edge samples are
        harmless for MVC since duplicates share identical rest positions."""
        S = _MVC_SUBDIV
        verts = []
        tris = [] if build_tris else None

        for face in self._face_verts:
            base = len(verts)
            n = len(face)

            if n == 3:
                ctrl = self._tri_ctrl_pts(vertices, handles, face)
                idx = {}
                for i in range(S + 1):
                    for j in range(S + 1 - i):
                        k = S - i - j
                        idx[(i, j)] = len(verts)
                        verts.append(_bez_tri3(*ctrl, i / S, j / S, k / S))
                if build_tris:
                    for i in range(S):
                        for j in range(S - i):
                            tris.append((idx[(i, j)], idx[(i + 1, j)], idx[(i, j + 1)]))
                            if j < S - 1 - i:
                                tris.append((idx[(i + 1, j)], idx[(i + 1, j + 1)], idx[(i, j + 1)]))

            elif n == 4:
                v0, v1, v2, v3 = face[0], face[1], face[2], face[3]
                C0 = self._edge_bezier(vertices, handles, v0, v1)
                C1 = self._edge_bezier(vertices, handles, v3, v2)
                D0 = self._edge_bezier(vertices, handles, v0, v3)
                D1 = self._edge_bezier(vertices, handles, v1, v2)
                idx = {}
                for a in range(S + 1):
                    for b in range(S + 1):
                        idx[(a, b)] = len(verts)
                        verts.append(_coons_eval(C0, C1, D0, D1, a / S, b / S))
                if build_tris:
                    for a in range(S):
                        for b in range(S):
                            p00 = idx[(a, b)]; p10 = idx[(a + 1, b)]
                            p11 = idx[(a + 1, b + 1)]; p01 = idx[(a, b + 1)]
                            tris.append((p00, p10, p11))
                            tris.append((p00, p11, p01))

            else:
                # Unexpected n-gon: fan-triangulate the corner vertices.
                for vi in face:
                    verts.append(vertices[vi])
                if build_tris:
                    for i in range(1, n - 1):
                        tris.append((base, base + i, base + i + 1))

        verts_arr = np.array(verts, dtype=np.float64).reshape((-1, 3))
        if build_tris:
            return verts_arr, np.array(tris, dtype=np.int32)
        return verts_arr

    @property
    def displacements(self):
        disp_v = self.vertices - self.rest_vertices if len(self.vertices) else np.empty((0, 3))
        disp_h = self.handles - self.rest_handles if len(self.handles) else np.empty((0, 3))
        if len(disp_v) == 0 and len(disp_h) == 0:
            return np.empty((0, 3), dtype=np.float64)
        if len(disp_h) == 0:
            return disp_v
        return np.vstack([disp_v, disp_h])

    @displacements.setter
    def displacements(self, value):
        if value is None:
            return
        disp_arr = np.asarray(value, dtype=np.float64)
        if disp_arr.size == 0:
            return
        disp_arr = disp_arr.reshape((-1, 3))
        nv = len(self.vertices)
        nh = len(self.handles)
        if len(disp_arr) >= nv + nh:
            self.vertices = self.rest_vertices + disp_arr[:nv]
            self.handles = self.rest_handles + disp_arr[nv:nv + nh]
            self.recompute_face_aabbs()
            self.geometry_version = getattr(self, "geometry_version", 0) + 1

    @property
    def _is_identity(self):
        max_diff_v = np.max(np.abs(self.vertices - self.rest_vertices)) if len(self.vertices) > 0 else 0.0
        max_diff_h = np.max(np.abs(self.handles - self.rest_handles)) if len(self.handles) > 0 else 0.0
        return bool((max_diff_v < 1e-5) and (max_diff_h < 1e-5))

    def recompute_face_aabbs(self):
        """Compute bounding boxes and cached control points for each face in the cage net."""
        self._face_aabbs = []
        self._face_cached_ctrl_pts = []
        for face in self._face_verts:
            sz = len(face)
            if sz == 4:
                C0, C1, D0, D1 = self._quad_ctrl_pts(self.vertices, self.handles, face)
                pts = np.vstack([C0, C1, D0, D1])
                self._face_cached_ctrl_pts.append((C0, C1, D0, D1))
            elif sz == 3:
                ctrl = self._tri_ctrl_pts(self.vertices, self.handles, face)
                pts = np.vstack(ctrl)
                self._face_cached_ctrl_pts.append(ctrl)
            else:
                self._face_cached_ctrl_pts.append(None)
                pts = self.vertices[face]
            bmin = pts.min(axis=0)
            bmax = pts.max(axis=0)
            self._face_aabbs.append((bmin, bmax))

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

        self.geometry_version = getattr(self, "geometry_version", 0) + 1
        self.recompute_face_aabbs()

    def project_aligned_handles(self, vi):
        """Project all ALIGNED handles at vertex vi onto the tangent plane."""
        aligned_hi = [hi for hi in self._vert_handles.get(vi, []) if self._handle_types[hi] == HandleType.ALIGNED]
        if len(aligned_hi) < 2:
            return

        v_pos = self.vertices[vi]
        T_vectors = [self.handles[hi] - v_pos for hi in aligned_hi]
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

        self.recompute_face_aabbs()

    # ── 3D Mean Value Coordinates (CPU inverse reconstruction) ───────────────

    # Query points are processed in blocks: the kernel materialises an
    # (block, Nv, 3) direction array, so an unbounded block would allocate
    # hundreds of MB on a fine bake grid.
    def handle_owner(self, hi: int) -> int:
        """Vertex index that handle hi hangs off."""
        return self._edges[hi // 2][hi % 2]

    def clone_bumped(self):
        """Copy of this field with geometry_version incremented."""
        clone = SdfCageDeformField(
            source=self.source,
            vertices=self.vertices.copy(),
            handles=self.handles.copy(),
            face_verts=[list(f) for f in self._face_verts],
            edges=list(self._edges),
            handle_types=list(self._handle_types),
            edge_straight=list(self._edge_straight),
            rest_vertices=self.rest_vertices.copy(),
            rest_handles=self.rest_handles.copy(),
            placement=self.placement,
            mvc_rest_verts=self._mvc_rest_verts,
            mvc_tris=self._mvc_tris,
        )
        clone.geometry_version = getattr(self, "geometry_version", 0) + 1
        return clone

    _MVC_BLOCK = 16384

    def mvc_forward_map(self, points: np.ndarray) -> np.ndarray:
        """
        Maps rest-space points into world (deformed) space via forward 3D Mean Value Coordinates.
        points: (N, 3) float64 ndarray
        returns: (N, 3) float64 ndarray
        """
        pts = np.asarray(points, dtype=np.float64)
        if self._is_identity:
            return pts.copy()

        N = pts.shape[0]
        V_def = self._build_mvc_mesh(self.vertices, self.handles, build_tris=False)
        V_rest = self._mvc_rest_verts
        tris = self._mvc_tris

        x = np.empty((N, 3), dtype=np.float64)
        blk = self._MVC_BLOCK
        for start in range(0, N, blk):
            sl = slice(start, min(start + blk, N))
            x[sl] = self._mvc_map_block(pts[sl], V_rest, V_def, tris)
        return x

    def _mvc_reconstruct(self, points: np.ndarray) -> np.ndarray:
        """
        Reconstructs rest-space points q for world-space query points x via 3D Mean Value Coordinates.
        points: (N, 3) float64 ndarray
        returns: (N, 3) float64 ndarray
        """
        pts = np.asarray(points, dtype=np.float64)
        if self._is_identity:
            return pts.copy()

        N = pts.shape[0]
        V_def = self._build_mvc_mesh(self.vertices, self.handles, build_tris=False)
        V_rest = self._mvc_rest_verts
        tris = self._mvc_tris

        q = np.empty((N, 3), dtype=np.float64)
        blk = self._MVC_BLOCK
        for start in range(0, N, blk):
            sl = slice(start, min(start + blk, N))
            q[sl] = self._mvc_map_block(pts[sl], V_def, V_rest, tris)
        return q

    def _mvc_map_block(self, P, V_src, V_tgt, tris):
        """MVC kernel for one block of query points from V_src to V_tgt."""
        n = P.shape[0]
        Nv = V_src.shape[0]

        U = V_src[None, :, :] - P[:, None, :]        # (n, Nv, 3)
        D = np.linalg.norm(U, axis=2)                # (n, Nv)

        q = P.copy()
        resolved = np.zeros(n, dtype=bool)
        on_vert = D < 1e-8
        hit = on_vert.any(axis=1)
        if hit.any():
            q[hit] = V_tgt[np.argmax(on_vert, axis=1)[hit]]
            resolved |= hit

        with np.errstate(divide='ignore', invalid='ignore'):
            U_hat = U / D[:, :, None]
        U_hat[~np.isfinite(U_hat)] = 0.0

        weights = np.zeros((n, Nv), dtype=np.float64)

        for ia, ib, ic in tris:
            u1 = U_hat[:, ia, :]; u2 = U_hat[:, ib, :]; u3 = U_hat[:, ic, :]
            d1 = D[:, ia]; d2 = D[:, ib]; d3 = D[:, ic]

            l1 = np.linalg.norm(u2 - u3, axis=1)
            l2 = np.linalg.norm(u3 - u1, axis=1)
            l3 = np.linalg.norm(u1 - u2, axis=1)

            theta1 = 2.0 * np.arcsin(np.clip(l1 / 2.0, -1.0, 1.0))
            theta2 = 2.0 * np.arcsin(np.clip(l2 / 2.0, -1.0, 1.0))
            theta3 = 2.0 * np.arcsin(np.clip(l3 / 2.0, -1.0, 1.0))

            h = (theta1 + theta2 + theta3) / 2.0

            # x lies in this triangle's plane: fall back to planar barycentric
            # interpolation (the spherical weights are singular there).
            coplanar = ((np.pi - h) < 1e-5) & ~resolved
            if coplanar.any():
                p1 = V_src[ia]
                v0_ = V_src[ib] - p1
                v1_ = V_src[ic] - p1
                d00 = float(np.dot(v0_, v0_)); d01 = float(np.dot(v0_, v1_))
                d11 = float(np.dot(v1_, v1_))
                denom = d00 * d11 - d01 * d01
                if abs(denom) > 1e-12:
                    v2_ = P[coplanar] - p1
                    d20 = v2_ @ v0_
                    d21 = v2_ @ v1_
                    bv = (d11 * d20 - d01 * d21) / denom
                    bw = (d00 * d21 - d01 * d20) / denom
                    bu = 1.0 - bv - bw
                    q[coplanar] = (bu[:, None] * V_tgt[ia]
                                   + bv[:, None] * V_tgt[ib]
                                   + bw[:, None] * V_tgt[ic])
                    resolved[coplanar] = True

            sin_h = np.sin(h)
            sin_t1 = np.sin(theta1); sin_t2 = np.sin(theta2); sin_t3 = np.sin(theta3)
            denom_c1 = sin_t2 * sin_t3
            denom_c2 = sin_t3 * sin_t1
            denom_c3 = sin_t1 * sin_t2

            ok = (~resolved
                  & (np.abs(denom_c1) >= 1e-12)
                  & (np.abs(denom_c2) >= 1e-12)
                  & (np.abs(denom_c3) >= 1e-12))
            if not ok.any():
                continue

            with np.errstate(divide='ignore', invalid='ignore'):
                c1 = (2.0 * sin_h * np.sin(h - theta1)) / denom_c1 - 1.0
                c2 = (2.0 * sin_h * np.sin(h - theta2)) / denom_c2 - 1.0
                c3 = (2.0 * sin_h * np.sin(h - theta3)) / denom_c3 - 1.0

            # det([u1; u2; u3]) == u1 · (u2 × u3)
            det_u = np.einsum('ij,ij->i', u1, np.cross(u2, u3))
            sign_det = np.where(det_u >= 0.0, 1.0, -1.0)

            s1 = sign_det * np.sqrt(np.maximum(0.0, 1.0 - c1 * c1))
            s2 = sign_det * np.sqrt(np.maximum(0.0, 1.0 - c2 * c2))
            s3 = sign_det * np.sqrt(np.maximum(0.0, 1.0 - c3 * c3))

            ok &= (np.abs(s1) >= 1e-8) & (np.abs(s2) >= 1e-8) & (np.abs(s3) >= 1e-8)
            if not ok.any():
                continue

            with np.errstate(divide='ignore', invalid='ignore'):
                w1 = (theta1 - c2 * theta3 - c3 * theta2) / (d1 * sin_t2 * s3)
                w2 = (theta2 - c3 * theta1 - c1 * theta3) / (d2 * sin_t3 * s1)
                w3 = (theta3 - c1 * theta2 - c2 * theta1) / (d3 * sin_t1 * s2)

            weights[ok, ia] += w1[ok]
            weights[ok, ib] += w2[ok]
            weights[ok, ic] += w3[ok]

        total_w = weights.sum(axis=1)
        good = ~resolved & (np.abs(total_w) > 1e-12)
        if good.any():
            q[good] = (weights[good] / total_w[good][:, None]) @ V_tgt
        return q

    # ── Warp Volume Baking ───────────────────────────────────────────────────

    def bake_warp_volume(self, resolution=32, pad_frac=0.15):
        """Bake the MVC warp W: deformed-space -> rest-space onto a uniform grid.

        Returns dict:
          warp_bytes : bytes    -- float32 RGBA, laid out (nz, ny, nx, 4), X fastest
          nx, ny, nz : int      -- grid point counts (not cell counts)
          bbox_min/max : Vector -- world-space bounds of the grid
          lipschitz  : float    -- conservative max ||J_W||, >= 1.0
        """
        bmin, bmax = self.bounding_box()
        diag = float((bmax - bmin).Length)
        pad = max(pad_frac * diag, 1.0)
        wmin = FreeCAD.Vector(bmin.x - pad, bmin.y - pad, bmin.z - pad)
        wmax = FreeCAD.Vector(bmax.x + pad, bmax.y + pad, bmax.z + pad)

        dx = max(wmax.x - wmin.x, 1e-3)
        dy = max(wmax.y - wmin.y, 1e-3)
        dz = max(wmax.z - wmin.z, 1e-3)
        longest = max(dx, dy, dz)
        step = longest / max(1, resolution - 1)

        nx = max(8, int(round(dx / step)) + 1)
        ny = max(8, int(round(dy / step)) + 1)
        nz = max(8, int(round(dz / step)) + 1)

        xs = np.linspace(wmin.x, wmax.x, nx)
        ys = np.linspace(wmin.y, wmax.y, ny)
        zs = np.linspace(wmin.z, wmax.z, nz)

        grid_z, grid_y, grid_x = np.meshgrid(zs, ys, xs, indexing='ij')
        pts = np.column_stack([grid_x.ravel(), grid_y.ravel(), grid_z.ravel()])

        if self._is_identity:
            W_mvc = pts.copy()
        else:
            W_mvc = self._mvc_reconstruct(pts)

        # Outside-the-cage identity blend
        net = self.vertices
        if len(self.handles) > 0:
            net = np.vstack([self.vertices, self.handles])
        net_min = net.min(axis=0)
        net_max = net.max(axis=0)

        dist_x = np.maximum(0.0, np.maximum(net_min[0] - pts[:, 0], pts[:, 0] - net_max[0]))
        dist_y = np.maximum(0.0, np.maximum(net_min[1] - pts[:, 1], pts[:, 1] - net_max[1]))
        dist_z = np.maximum(0.0, np.maximum(net_min[2] - pts[:, 2], pts[:, 2] - net_max[2]))
        d_out = np.sqrt(dist_x * dist_x + dist_y * dist_y + dist_z * dist_z)

        t_ramp = np.clip(d_out / pad, 0.0, 1.0)
        s_blend = t_ramp * t_ramp * (3.0 - 2.0 * t_ramp)

        shell_mask = np.zeros((nz, ny, nx), dtype=bool)
        shell_mask[0, :, :] = True
        shell_mask[-1, :, :] = True
        shell_mask[:, 0, :] = True
        shell_mask[:, -1, :] = True
        shell_mask[:, :, 0] = True
        shell_mask[:, :, -1] = True
        s_blend[shell_mask.ravel()] = 1.0

        W_stored = (1.0 - s_blend[:, None]) * W_mvc + s_blend[:, None] * pts

        # Lipschitz bound estimation via Jacobian Frobenius norm
        W_grid = W_stored.reshape((nz, ny, nx, 3))
        dW_dz, dW_dy, dW_dx = np.gradient(W_grid, zs, ys, xs, axis=(0, 1, 2))
        frob_sq = np.sum(dW_dx**2 + dW_dy**2 + dW_dz**2, axis=-1)
        l_est = float(np.max(np.sqrt(frob_sq)))
        lipschitz = max(1.0, min(8.0, 1.05 * l_est))
        if l_est > 7.5:
            from freecad.fields.core import fld_logger
            fld_logger.warn(f"SdfCageDeformField: high Lipschitz bound L={l_est:.2f} (clamped to {lipschitz:.2f})")

        # Remembered for lipschitz(): this is the only place the warp's Jacobian is
        # ever measured, and the CPU consumers (sdf_octree's cull test, SdfField's
        # own ray_march) have no other way to learn that this field is not 1-Lipschitz.
        self._warp_lipschitz = float(lipschitz)

        rgba = np.zeros((nz, ny, nx, 4), dtype=np.float32)
        rgba[..., :3] = W_grid
        warp_bytes = np.ascontiguousarray(rgba, dtype=np.float32).tobytes()

        return {
            "warp_bytes": warp_bytes,
            "nx": nx,
            "ny": ny,
            "nz": nz,
            "bbox_min": wmin,
            "bbox_max": wmax,
            "lipschitz": lipschitz,
        }

    # ── Field Evaluation ──────────────────────────────────────────────────────

    def evaluate(self, point: FreeCAD.Vector) -> float:
        if self._is_identity:
            return self.source.evaluate(point)
        pts = np.array([[point.x, point.y, point.z]], dtype=np.float64)
        q = self._mvc_reconstruct(pts)
        return float(self.source.evaluate(FreeCAD.Vector(q[0, 0], q[0, 1], q[0, 2])))

    def evaluate_grid(self, points: np.ndarray) -> np.ndarray:
        if self._is_identity:
            return self.source.evaluate_grid(points)
        q = self._mvc_reconstruct(points.astype(np.float64))
        return self.source.evaluate_grid(q)

    def lipschitz(self) -> float:
        """Source bound times the warp's, which only bake_warp_volume can measure.

        Inheriting the 1.0 default was a straight CPU/GPU disagreement: to_glsl already
        divides by the same constant (`u_lip`, set from the bake), so the GPU has always
        treated this field as up to 8-Lipschitz while every CPU consumer was told 1.0.
        Measured on a sphere in a box cage, one vertex pulled 15 mm, |grad| reaches 20.

        Falls back to the source's own bound until the first bake, which is no worse
        than the value this replaces. Note the bound describes the BAKED trilinear warp
        the GPU samples; evaluate_grid runs the exact MVC warp, whose spikes are finer
        than the 32^3 grid can resolve, so this is not conservative for the CPU path in
        the way the name suggests -- see the todo.
        """
        return self.source.lipschitz() * max(1.0, getattr(self, "_warp_lipschitz", 1.0))

    def to_glsl(self, ctx, point_var="p"):
        if self._is_identity:
            return self.source.to_glsl(ctx, point_var=point_var)

        tex_name = ctx.sampler3d("warp", provider=self)          # renderer binds this
        u_wmin   = ctx.uniform("vec3",  [0.0, 0.0, 0.0], name="wmin")   # set per-frame by renderer
        u_wmax   = ctx.uniform("vec3",  [0.0, 0.0, 0.0], name="wmax")
        u_lip    = ctx.uniform("float", 1.0, name="lip")
        ctx.sampler3d_uniform(tex_name, "wmin", u_wmin)
        ctx.sampler3d_uniform(tex_name, "wmax", u_wmax)
        ctx.sampler3d_uniform(tex_name, "lip",  u_lip)
        func_name = ctx.get_unique_name("warp_lookup")
        helper_body = f"""
vec3 {func_name}(vec3 p) {{
    vec3 uvw = (p - {u_wmin}) / max({u_wmax} - {u_wmin}, vec3(1e-6));
    if (any(lessThan(uvw, vec3(0.0))) || any(greaterThan(uvw, vec3(1.0)))) return p;
    return texture({tex_name}, uvw).rgb;
}}
"""
        ctx.add_custom_helper(func_name, helper_body)
        inner = self.source.to_glsl(ctx, point_var=f"{func_name}({point_var})")
        # UX-009: return the field UNSCALED. The Lipschitz bound divides the
        # marcher's STEP (`u_step_div_{i}` in build_multi_raymarch_*), never the
        # value. Dividing the value here kept the step safe but also shrank
        # everything the marcher compares against a constant -- the hit test
        # `abs(d) < 0.2` and the polish `clamp(d, -0.2, 0.2)` are in millimetres,
        # so at u_lip = 2.87 the effective tolerance became 0.57 mm. Measured
        # hit-point error on a deformed box: 0.227 mm mean / 0.882 mm max that
        # way, 0.014 mm / 0.599 mm this way.
        return f"({inner})"

    def to_glsl_sample(self, ctx, point_var="p"):
        if self._is_identity:
            return self.source.to_glsl_sample(ctx, point_var=point_var)

        tex_name = ctx.sampler3d("warp", provider=self)
        u_wmin   = ctx.uniform("vec3",  [0.0, 0.0, 0.0], name="wmin")
        u_wmax   = ctx.uniform("vec3",  [0.0, 0.0, 0.0], name="wmax")
        u_lip    = ctx.uniform("float", 1.0, name="lip")
        ctx.sampler3d_uniform(tex_name, "wmin", u_wmin)
        ctx.sampler3d_uniform(tex_name, "wmax", u_wmax)
        ctx.sampler3d_uniform(tex_name, "lip",  u_lip)
        func_name = ctx.get_unique_name("warp_lookup")
        helper_body = f"""
vec3 {func_name}(vec3 p) {{
    vec3 uvw = (p - {u_wmin}) / max({u_wmax} - {u_wmin}, vec3(1e-6));
    if (any(lessThan(uvw, vec3(0.0))) || any(greaterThan(uvw, vec3(1.0)))) return p;
    return texture({tex_name}, uvw).rgb;
}}
"""
        ctx.add_custom_helper(func_name, helper_body)
        return self.source.to_glsl_sample(ctx, point_var=f"{func_name}({point_var})")

    def texture3d_key(self):
        if getattr(self, "_is_identity", True):
            return None
        return f"warp_{self.field_uid}_{getattr(self, 'geometry_version', 0)}"

    def texture3d_data(self):
        if getattr(self, "_is_identity", True):
            return None
        from freecad.fields.core.objects.fld_object import get_sdf_warp_resolution
        baked = self.bake_warp_volume(resolution=get_sdf_warp_resolution())
        return {
            "nx": baked["nx"], "ny": baked["ny"], "nz": baked["nz"],
            "fmt": "rgba32f",
            "bytes": baked["warp_bytes"],
            "uniforms": {
                "wmin": ("vec3", (baked["bbox_min"].x, baked["bbox_min"].y, baked["bbox_min"].z)),
                "wmax": ("vec3", (baked["bbox_max"].x, baked["bbox_max"].y, baked["bbox_max"].z)),
                "lip":  ("float", baked["lipschitz"]),
            },
        }

    def changed_region(self, prev):
        """World box where this field differs from `prev`, or None if unknown.

        Only the identity case is answered. A live warp maps a source-space box
        to a curved region, and the honest bound on that is the whole cage -- no
        better than the caller's fallback -- so there is nothing to gain by
        approximating it, and an under-estimate here erases geometry silently.

        Identity is not the rare case it sounds like: a face-extrude drag leaves
        the control net alone and only rewrites the extrusion stack underneath,
        so this is exactly the path that matters for extrude performance.

        The 0.5 margin matches bounding_box()'s, and like it this treats the
        source box as already being in world coordinates.
        """
        if not isinstance(prev, SdfCageDeformField):
            return None
        if not (self._is_identity and prev._is_identity):
            return None
        if (self.vertices.shape != prev.vertices.shape
                or self.handles.shape != prev.handles.shape
                or not np.array_equal(self.vertices, prev.vertices)
                or not np.array_equal(self.handles, prev.handles)):
            return None
        sub = getattr(self.source, "changed_region", None)
        if sub is None:
            return None
        box = sub(prev.source)
        if box is None:
            return None
        m = 0.5
        bmin, bmax = box
        return (FreeCAD.Vector(bmin.x - m, bmin.y - m, bmin.z - m),
                FreeCAD.Vector(bmax.x + m, bmax.y + m, bmax.z + m))

    def bounding_box(self):
        # The deformed surface is bounded by the DEFORMED control net (MVC maps
        # the interior of the deformed cage onto the interior of the rest cage,
        # and the source surface lives inside the rest cage). Returning the
        # source (rest) box here clips any control point pulled outward — the
        # ray marcher only samples the SDF within this AABB, so the deformation
        # becomes invisible. Union the source box with the deformed net's extent
        # (verts + handles) plus a margin so outward bulges stay inside bounds.
        src_min, src_max = self.source.bounding_box()
        sb_min = np.array([src_min.x, src_min.y, src_min.z])
        sb_max = np.array([src_max.x, src_max.y, src_max.z])

        if self._is_identity:
            # Small insurance margin so the ray-march AABB test never clips the
            # exact silhouette of a surface that sits right on the source box.
            m = 0.5
            return (FreeCAD.Vector(*(sb_min - m)), FreeCAD.Vector(*(sb_max + m)))

        net = self.vertices
        if len(self.handles) > 0:
            net = np.vstack([self.vertices, self.handles])
        net_min = net.min(axis=0)
        net_max = net.max(axis=0)

        # The margin must scale with how far control points have been pulled: the
        # smooth MVC field overshoots the raw net near a large pull, so a fixed
        # margin clips the tip of a big deformation. Measure the live displacement
        # (verts/handles are mutated in place during a drag — don't trust the
        # cached self.displacements, which isn't updated mid-drag).
        dv = np.max(np.abs(self.vertices - self.rest_vertices)) if len(self.vertices) else 0.0
        dh = np.max(np.abs(self.handles - self.rest_handles)) if len(self.handles) else 0.0
        margin = 2.0 + 0.5 * float(max(dv, dh))
        bmin = np.minimum(sb_min, net_min) - margin
        bmax = np.maximum(sb_max, net_max) + margin
        return (FreeCAD.Vector(bmin[0], bmin[1], bmin[2]),
                FreeCAD.Vector(bmax[0], bmax[1], bmax[2]))

    @property
    def octree_cache(self):
        # The octree accelerates ray_march by skipping empty space, but it caches
        # the SOURCE (rest) SDF. Once the cage deforms, the source octree no longer
        # matches the world-space surface: a point inside an extruded bulge reads
        # as empty in the source octree (its rest preimage is far outside the
        # source sphere), so ray_march skips it and never detects the deformed
        # surface — CageEditTool's face picking then fails with "No face selected"
        # on the SECOND extrude. Only hand back the source octree while identity;
        # once deformed, return None so ray_march sphere-traces via evaluate_grid
        # (the true MVC-warped distance, matching the GPU).
        if self._is_identity:
            return self.source.octree_cache
        return None
