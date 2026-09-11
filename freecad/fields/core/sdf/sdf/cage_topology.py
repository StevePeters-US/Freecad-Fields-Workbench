# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Half-edge cage topology: vertices, faces, edge loops, subdivision.

Split out of cage.py unchanged (ST-006). Nothing here knows what an SDF is —
this layer is pure connectivity plus the canonical box and octahedron nets.
"""
import math
import numpy as np

MAX_CAGE_FACES = 64

class HandleType(int):
    FREE    = 0  # absolute position, no constraints
    LINKED  = 1  # moves with its vertex; offset from vertex is preserved
    ALIGNED = 2  # direction locked to edge tangent at vertex; only magnitude editable

# ── Box cage topology ─────────────────────────────────────────────────────────
# Canonical vertex ordering:
#   0=(-x,-y,-z)  1=(+x,-y,-z)  2=(+x,+y,-z)  3=(-x,+y,-z)
#   4=(-x,-y,+z)  5=(+x,-y,+z)  6=(+x,+y,+z)  7=(-x,+y,+z)

_BOX_EDGES = [
    (0,1),(1,2),(2,3),(3,0),   # bottom ring   (edges 0–3)
    (4,5),(5,6),(6,7),(7,4),   # top ring      (edges 4–7)
    (0,4),(1,5),(2,6),(3,7),   # verticals     (edges 8–11)
]

# Each face: [v0,v1,v2,v3] CCW from outside.
# Coons: s goes v0→v1, t goes v0→v3.
_BOX_FACES = [
    [0, 3, 2, 1],  # bottom  (z−)
    [4, 5, 6, 7],  # top     (z+)
    [0, 1, 5, 4],  # front   (y−)
    [2, 3, 7, 6],  # back    (y+)
    [0, 4, 7, 3],  # left    (x−)
    [1, 2, 6, 5],  # right   (x+)
]

_BOX_EDGE_LOOKUP = {frozenset(e): i for i, e in enumerate(_BOX_EDGES)}

# ── Octahedron cage topology ─────────────────────────────────────────────────
# Vertices: 0=(+x,0,0) 1=(-x,0,0) 2=(0,+y,0) 3=(0,-y,0) 4=(0,0,+z) 5=(0,0,-z)

_OCT_EDGES = [
    (0, 2),   # 0: +x → +y   (equatorial)
    (2, 1),   # 1: +y → -x
    (1, 3),   # 2: -x → -y
    (3, 0),   # 3: -y → +x
    (0, 4),   # 4: +x → +z   (upper)
    (2, 4),   # 5: +y → +z
    (1, 4),   # 6: -x → +z
    (3, 4),   # 7: -y → +z
    (0, 5),   # 8: +x → -z   (lower)
    (2, 5),   # 9: +y → -z
    (1, 5),   # 10: -x → -z
    (3, 5),   # 11: -y → -z
]

# Each face [v0,v1,v2] CCW so (v1-v0)×(v2-v0) points outward.
_OCT_FACES = [
    [4, 0, 2],  # F0: +z,+x,+y  normal (+,+,+)
    [4, 2, 1],  # F1: +z,+y,-x  normal (-,+,+)
    [4, 1, 3],  # F2: +z,-x,-y  normal (-,-,+)
    [4, 3, 0],  # F3: +z,-y,+x  normal (+,-,+)
    [5, 2, 0],  # F4: -z,+y,+x  normal (+,+,-)
    [5, 1, 2],  # F5: -z,-x,+y  normal (-,+,-)
    [5, 3, 1],  # F6: -z,-y,-x  normal (-,-,-)
    [5, 0, 3],  # F7: -z,+x,-y  normal (+,-,-)
]

_OCT_EDGE_LOOKUP = {frozenset(e): i for i, e in enumerate(_OCT_EDGES)}


class Vertex:
    def __init__(self, idx, pos):
        self.idx = idx
        self.pos = pos
        self.half_edge = None

class Face:
    def __init__(self, idx):
        self.idx = idx
        self.half_edge = None

class HalfEdge:
    def __init__(self):
        self.vertex = None  # Target vertex
        self.face = None
        self.next = None
        self.prev = None
        self.twin = None
        self.edge_idx = None

class CageTopology:
    def __init__(self, face_verts_flat, face_sizes, vertices_np):
        self.vertices = [Vertex(i, coords.copy()) for i, coords in enumerate(vertices_np)]
        
        faces_list = []
        offset = 0
        for sz in face_sizes:
            faces_list.append(face_verts_flat[offset:offset+sz])
            offset += sz
            
        self.faces = [Face(i) for i in range(len(faces_list))]
        self.half_edges = []
        
        edge_map = {}
        
        for fi, f_verts in enumerate(faces_list):
            face = self.faces[fi]
            face_he = []
            n = len(f_verts)
            for i in range(n):
                u = f_verts[i]
                v = f_verts[(i + 1) % n]
                
                he = HalfEdge()
                he.vertex = self.vertices[v]
                he.face = face
                
                self.vertices[u].half_edge = he
                face_he.append(he)
                edge_map[(u, v)] = he
                self.half_edges.append(he)
                
            face.half_edge = face_he[0]
            
            for i in range(n):
                face_he[i].next = face_he[(i + 1) % n]
                face_he[i].prev = face_he[(i - 1) % n]
                
        undirected_edges = []
        edge_to_idx = {}
        for he in self.half_edges:
            v = he.vertex.idx
            u = he.prev.vertex.idx
            
            twin = edge_map.get((v, u))
            if twin:
                he.twin = twin
                twin.twin = he
                
            edge_key = frozenset([u, v])
            if edge_key not in edge_to_idx:
                edge_idx = len(undirected_edges)
                undirected_edges.append((u, v))
                edge_to_idx[edge_key] = edge_idx
            he.edge_idx = edge_to_idx[edge_key]
            
        self.edges = undirected_edges

    def face_vertex_lists(self):
        """Return per-face vertex index lists [[v0, v1, ...], ...]."""
        faces_list = []
        for f in self.faces:
            he_start = f.half_edge
            he = he_start
            f_verts = []
            while True:
                f_verts.append(he.prev.vertex.idx)
                he = he.next
                if he == he_start:
                    break
            faces_list.append(f_verts)
        return faces_list

    def net_arrays(self):
        """Extract (face_verts_flat, face_sizes, vertices_np) to duplicate or construct a new CageTopology."""
        faces_list = self.face_vertex_lists()
        face_verts_flat = [v for f in faces_list for v in f]
        face_sizes = [len(f) for f in faces_list]
        vertices_np = np.array([v.pos for v in self.vertices], dtype=np.float64)
        return face_verts_flat, face_sizes, vertices_np

    def rebuild_from_geometry(self, vertices_np):
        for i, coords in enumerate(vertices_np):
            if i < len(self.vertices):
                self.vertices[i].pos = coords.copy()

    def extrude_face(self, face_idx, distance):
        """Extrude the face at face_idx by distance along its normal."""
        # 1. Get current face lists
        faces_list = []
        for f in self.faces:
            he_start = f.half_edge
            he = he_start
            f_verts = []
            while True:
                f_verts.append(he.prev.vertex.idx)
                he = he.next
                if he == he_start:
                    break
            faces_list.append(f_verts)
            
        # 2. Get face to extrude
        target_face_verts = faces_list[face_idx]
        n = len(target_face_verts)
        
        # Compute normal
        v0 = self.vertices[target_face_verts[0]].pos
        v1 = self.vertices[target_face_verts[1]].pos
        v2 = self.vertices[target_face_verts[2]].pos
        normal = np.cross(v1 - v0, v2 - v0)
        norm_len = np.linalg.norm(normal)
        if norm_len > 1e-8:
            normal /= norm_len
        else:
            normal = np.array([0.0, 0.0, 1.0])
            
        # 3. Create new vertices
        new_vert_indices = []
        for vi in target_face_verts:
            new_pos = self.vertices[vi].pos + normal * distance
            new_idx = len(self.vertices)
            new_vert_indices.append(new_idx)
            self.vertices.append(Vertex(new_idx, new_pos))
            
        # 4. Create new faces
        # The target face is replaced by the top face (new vertices)
        faces_list[face_idx] = new_vert_indices
        
        # Create side faces
        for i in range(n):
            v0 = target_face_verts[i]
            v1 = target_face_verts[(i + 1) % n]
            v1_new = new_vert_indices[(i + 1) % n]
            v0_new = new_vert_indices[i]
            
            # CCW side face: v0 -> v1 -> v1_new -> v0_new
            faces_list.append([v0, v1, v1_new, v0_new])
            
        # 5. Rebuild connectivity
        face_verts_flat = [v for face in faces_list for v in face]
        face_sizes = [len(face) for face in faces_list]
        vertices_np = np.array([v.pos for v in self.vertices], dtype=np.float64)
        
        self.__init__(face_verts_flat, face_sizes, vertices_np)

    def insert_edge_loop(self, edge_idx, t):
        """Insert an edge loop perpendicular to edge_idx at parameter t [0..1]."""
        # Get current faces
        faces_list = []
        for f in self.faces:
            he_start = f.half_edge
            he = he_start
            f_verts = []
            while True:
                f_verts.append(he.prev.vertex.idx)
                he = he.next
                if he == he_start:
                    break
            faces_list.append(f_verts)
            
        split_verts = {} # (u, v) -> new_v_idx
        
        he_start = None
        for he in self.half_edges:
            if he.edge_idx == edge_idx:
                he_start = he
                break
                 
        if he_start is None:
            return
             
        queue = []
        # Propagate in one direction
        curr = he_start
        while curr is not None:
            queue.append(curr)
            if curr.face is None:
                break
            
            # Check if face is quad
            he_f = curr.face.half_edge
            he_curr = he_f
            f_len = 0
            while True:
                f_len += 1
                he_curr = he_curr.next
                if he_curr == he_f:
                    break
            if f_len != 4:
                break
                
            opposite_he = curr.next.next
            curr = opposite_he.twin
            if curr == he_start or curr == he_start.twin:
                break
                 
        # Propagate in other direction from twin
        if he_start.twin is not None:
            curr = he_start.twin
            while curr is not None:
                if curr in queue:
                     break
                queue.insert(0, curr)
                if curr.face is None:
                    break
                
                he_f = curr.face.half_edge
                he_curr = he_f
                f_len = 0
                while True:
                    f_len += 1
                    he_curr = he_curr.next
                    if he_curr == he_f:
                        break
                if f_len != 4:
                    break
                    
                opposite_he = curr.next.next
                curr = opposite_he.twin
                if curr == he_start or curr == he_start.twin:
                    break
                     
        faces_to_remove = set()
        new_faces = []
        
        for he in queue:
            face = he.face
            fi = face.idx
            if fi in faces_to_remove:
                continue
                 
            faces_to_remove.add(fi)
             
            v0 = he.prev.vertex.idx
            v1 = he.vertex.idx
            v2 = he.next.vertex.idx
            v3 = he.next.next.vertex.idx
             
            edge_key1 = (min(v0, v1), max(v0, v1))
            if edge_key1 in split_verts:
                va = split_verts[edge_key1]
            else:
                pos_a = (1.0 - t) * self.vertices[v0].pos + t * self.vertices[v1].pos
                va = len(self.vertices)
                self.vertices.append(Vertex(va, pos_a))
                split_verts[edge_key1] = va
                 
            edge_key2 = (min(v3, v2), max(v3, v2))
            if edge_key2 in split_verts:
                vb = split_verts[edge_key2]
            else:
                pos_b = (1.0 - t) * self.vertices[v3].pos + t * self.vertices[v2].pos
                vb = len(self.vertices)
                self.vertices.append(Vertex(vb, pos_b))
                split_verts[edge_key2] = vb
                 
            new_faces.append([v0, va, vb, v3])
            new_faces.append([va, v1, v2, vb])
             
        final_faces = []
        for fi, f in enumerate(faces_list):
            if fi not in faces_to_remove:
                final_faces.append(f)
        final_faces.extend(new_faces)
         
        face_verts_flat = [v for face in final_faces for v in face]
        face_sizes = [len(face) for face in final_faces]
        vertices_np = np.array([v.pos for v in self.vertices], dtype=np.float64)
        self.__init__(face_verts_flat, face_sizes, vertices_np)

    def weld_vertices(self, v_from, v_to):
        """Weld vertex v_from to v_to, removing degenerate faces and shifting indices."""
        # 1. Get current face lists
        faces_list = []
        for f in self.faces:
            he_start = f.half_edge
            he = he_start
            f_verts = []
            while True:
                f_verts.append(he.prev.vertex.idx)
                he = he.next
                if he == he_start:
                    break
            faces_list.append(f_verts)
            
        # 2. Update indices: replace v_from with v_to
        for fi, f in enumerate(faces_list):
            new_f = []
            for vi in f:
                if vi == v_from:
                    new_f.append(v_to)
                else:
                    new_f.append(vi)
            # Deduplicate consecutive indices
            dedup_f = []
            for vi in new_f:
                if not dedup_f or dedup_f[-1] != vi:
                    dedup_f.append(vi)
            if len(dedup_f) > 1 and dedup_f[0] == dedup_f[-1]:
                dedup_f.pop()
            faces_list[fi] = dedup_f
            
        # 3. Discard degenerate faces (length < 3)
        final_faces = [f for f in faces_list if len(f) >= 3]
        
        # 4. Shift indices for vertices > v_from
        for fi, f in enumerate(final_faces):
            final_faces[fi] = [v - 1 if v > v_from else v for v in f]
            
        self.vertices.pop(v_from)
        for i, v in enumerate(self.vertices):
            v.idx = i
            
        # 5. Rebuild connectivity
        face_verts_flat = [v for face in final_faces for v in face]
        face_sizes = [len(face) for face in final_faces]
        vertices_np = np.array([v.pos for v in self.vertices], dtype=np.float64)
        self.__init__(face_verts_flat, face_sizes, vertices_np)

    def subdivide_smooth(self):
        """Catmull-Clark subdivision on the control cage.
        Returns: (vertices, handles, edges, face_verts, handle_types)
        """
        # 1. Face points
        face_pts = []
        for fi, f in enumerate(self.faces):
            he_start = f.half_edge
            he = he_start
            pts = []
            while True:
                pts.append(he.prev.vertex.pos)
                he = he.next
                if he == he_start:
                    break
            face_pts.append(np.mean(pts, axis=0))

        # 2. Edge points
        edge_pts = []
        for ei, (vi, vj) in enumerate(self.edges):
            he_found = None
            for he in self.half_edges:
                if he.edge_idx == ei:
                    he_found = he
                    break
            pos_i = self.vertices[vi].pos
            pos_j = self.vertices[vj].pos
            if he_found and he_found.twin is not None:
                f1 = he_found.face.idx
                f2 = he_found.twin.face.idx
                edge_pts.append((pos_i + pos_j + face_pts[f1] + face_pts[f2]) / 4.0)
            else:
                edge_pts.append((pos_i + pos_j) / 2.0)

        # 3. Vertex updates
        updated_verts = []
        for vi in range(len(self.vertices)):
            he_incoming = [he for he in self.half_edges if he.vertex.idx == vi]
            
            boundary_edges = []
            for he in he_incoming:
                if he.twin is None:
                    boundary_edges.append(he)
                if he.next.twin is None:
                    boundary_edges.append(he.next)
            
            if len(boundary_edges) >= 2:
                # Boundary vertex rule: (6*P + M0 + M1)/8
                b_neighbors = []
                for he in he_incoming:
                    if he.twin is None:
                        b_neighbors.append(he.prev.vertex.pos)
                    if he.next.twin is None:
                        b_neighbors.append(he.next.vertex.pos)
                if len(b_neighbors) >= 2:
                    M0 = 0.5 * (self.vertices[vi].pos + b_neighbors[0])
                    M1 = 0.5 * (self.vertices[vi].pos + b_neighbors[1])
                    v_new = (6.0 * self.vertices[vi].pos + M0 + M1) / 8.0
                else:
                    v_new = self.vertices[vi].pos
            else:
                # Interior vertex rule: (F + 2*R + (n-3)*P) / n
                adj_faces = [he.face.idx for he in he_incoming if he.face is not None]
                if len(adj_faces) > 0:
                    F = np.mean([face_pts[fi] for fi in adj_faces], axis=0)
                    edge_mids = []
                    for he in he_incoming:
                        v_other = he.prev.vertex.pos
                        edge_mids.append(0.5 * (self.vertices[vi].pos + v_other))
                    R = np.mean(edge_mids, axis=0)
                    n = len(he_incoming)
                    v_new = (F + 2.0 * R + (n - 3.0) * self.vertices[vi].pos) / n
                else:
                    v_new = self.vertices[vi].pos
            updated_verts.append(v_new)

        # 4. New topology: each face of size n becomes n quads
        new_faces = []
        V = len(self.vertices)
        F = len(self.faces)
        for he in self.half_edges:
            fi = he.face.idx
            v_curr = he.prev.vertex.idx
            new_faces.append([
                v_curr,
                V + F + he.edge_idx,
                V + fi,
                V + F + he.prev.edge_idx
            ])

        new_face_verts_flat = [v for f in new_faces for v in f]
        new_face_sizes = [4] * len(new_faces)
        new_vertices_np = np.vstack(updated_verts + face_pts + edge_pts)

        # Build sub_topo to get new edges
        sub_topo = CageTopology(new_face_verts_flat, new_face_sizes, new_vertices_np)

        # 5. Default handles
        handles = np.zeros((2 * len(sub_topo.edges), 3), dtype=np.float64)
        handle_types = [1] * (2 * len(sub_topo.edges))  # LINKED = 1
        for ei, (va, vb) in enumerate(sub_topo.edges):
            pos_a = sub_topo.vertices[va].pos
            pos_b = sub_topo.vertices[vb].pos
            handles[2*ei] = pos_a + (pos_b - pos_a) / 3.0
            handles[2*ei+1] = pos_a + (pos_b - pos_a) * 2.0 / 3.0

        # Project handles onto vertex tangent planes for valence >= 3
        _svd_failed = 0
        _svd_attempted = 0
        for vi in range(len(sub_topo.vertices)):
            he_incoming = [he for he in sub_topo.half_edges if he.vertex.idx == vi]
            if len(he_incoming) >= 3:
                aligned_hi = []
                aligned_vectors = []
                for ei, (va, vb) in enumerate(sub_topo.edges):
                    if va == vi:
                        aligned_hi.append(2*ei)
                        aligned_vectors.append(handles[2*ei] - new_vertices_np[vi])
                    elif vb == vi:
                        aligned_hi.append(2*ei+1)
                        aligned_vectors.append(handles[2*ei+1] - new_vertices_np[vi])
                if len(aligned_hi) >= 3:
                    _svd_attempted += 1
                    try:
                        T_mat = np.array(aligned_vectors)
                        _, _, Vh = np.linalg.svd(T_mat)
                        normal = Vh[2]
                        nlen = np.linalg.norm(normal)
                        if nlen > 1e-8:
                            normal = normal / nlen
                            for hi in aligned_hi:
                                T = handles[hi] - new_vertices_np[vi]
                                T_proj = T - np.dot(T, normal) * normal
                                handles[hi] = new_vertices_np[vi] + T_proj
                                handle_types[hi] = 2  # ALIGNED
                    except Exception as e:
                        _svd_failed += 1
                        _svd_last_err = e

        if _svd_failed:
            from freecad.fields.core import fld_logger
            fld_logger.warn(
                f"subdivide_smooth: SVD handle alignment failed for {_svd_failed} "
                f"of {_svd_attempted} vertices (last: {_svd_last_err}); those "
                f"handles keep their unprojected directions and the surface will "
                f"not be tangent-continuous there."
            )

        return new_vertices_np, handles, sub_topo.edges, new_face_verts_flat, handle_types
