# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_postprocess.py

Post-processing passes `FldMesher.mesh()` runs on the raw (flat_verts,
flat_idx) triangle arrays a mesher produced: `decimate_flat_tris` merges
coplanar adjacent triangles, `deduplicate_verts` collapses vertices shared
by adjacent triangles (the meshers themselves emit unique vertices per
triangle, with no sharing).
"""
import numpy as np


def decimate_flat_tris(verts, indices, angle_tol=5.0):
    """
    Merges coplanar adjacent triangles to reduce triangle count.

    verts: (N*3, 3) float32 - flat vertex array
    indices: (N*4,) int32 - flat index array with -1 sentinels
    angle_tol: float - maximum angle in degrees between triangle normals to be considered coplanar

    Returns: (new_verts, new_indices) in the same format.
    """
    if len(indices) == 0:
        return verts, indices

    n_tris = len(indices) // 4
    tri_verts = verts.reshape(n_tris, 3, 3).astype(np.float64)

    # 1. Calculate Face Normals
    v0 = tri_verts[:, 0, :]
    v1 = tri_verts[:, 1, :]
    v2 = tri_verts[:, 2, :]

    raw_normals = np.cross(v1 - v0, v2 - v0)
    norms = np.linalg.norm(raw_normals, axis=1)

    valid_mask = norms > 1e-12
    normals = np.zeros_like(raw_normals)
    normals[valid_mask] = raw_normals[valid_mask] / norms[valid_mask][:, None]

    # 2. Build Face Adjacency
    # We use rounded vertex positions as keys since vertices are not shared.
    def get_v_hash(v):
        return tuple((v / 1e-5).round().astype(np.int64))

    edge_to_faces = {} # edge_hash -> list of face_indices

    for i in range(n_tris):
        if not valid_mask[i]:
            continue
        v_hashes = [get_v_hash(tri_verts[i, j]) for j in range(3)]
        # Triangle edges
        edges = [
            frozenset([v_hashes[0], v_hashes[1]]),
            frozenset([v_hashes[1], v_hashes[2]]),
            frozenset([v_hashes[2], v_hashes[0]])
        ]
        for e in edges:
            if e not in edge_to_faces:
                edge_to_faces[e] = []
            edge_to_faces[e].append(i)

    # Adjacency graph
    adj = [[] for _ in range(n_tris)]
    for faces in edge_to_faces.values():
        if len(faces) == 2:
            f1, f2 = faces
            adj[f1].append(f2)
            adj[f2].append(f1)

    # 3. Group Coplanar Adjacent Triangles
    cos_tol = np.cos(np.radians(angle_tol))
    visited = np.zeros(n_tris, dtype=bool)
    groups = []

    for i in range(n_tris):
        if visited[i] or not valid_mask[i]:
            continue

        group = []
        stack = [i]
        visited[i] = True
        n0 = normals[i]

        while stack:
            curr = stack.pop()
            group.append(curr)

            for neighbor in adj[curr]:
                if not visited[neighbor]:
                    # Planarity check
                    if np.dot(n0, normals[neighbor]) > cos_tol:
                        visited[neighbor] = True
                        stack.append(neighbor)
        groups.append(group)

    # 4. Extract Boundaries and Re-triangulate
    final_verts = []
    final_idx = []
    curr_v_count = 0

    # Pre-mapping of hashes to representative positions for boundary reconstruction
    # (Using the very first encounter of a rounded vertex to keep it consistent)
    hash_to_pos = {}
    for i in range(n_tris):
        if not valid_mask[i]: continue
        for j in range(3):
            h = get_v_hash(tri_verts[i, j])
            if h not in hash_to_pos:
                hash_to_pos[h] = tri_verts[i, j]

    for group in groups:
        if len(group) == 1:
            # No decimation possible for single triangle
            f_idx = group[0]
            final_verts.append(tri_verts[f_idx])
            final_idx.extend([curr_v_count, curr_v_count+1, curr_v_count+2, -1])
            curr_v_count += 3
            continue

        # Count edge occurrences within the group
        group_edge_counts = {}
        for f_idx in group:
            v_hashes = [get_v_hash(tri_verts[f_idx, j]) for j in range(3)]
            # Use directed edges for boundary tracing: (v1, v2)
            edges = [(v_hashes[0], v_hashes[1]), (v_hashes[1], v_hashes[2]), (v_hashes[2], v_hashes[0])]
            for e in edges:
                # Store as sorted tuple for existence check, but we need direction for loops
                rev_e = (e[1], e[0])
                canonical = tuple(sorted(e))
                group_edge_counts[canonical] = group_edge_counts.get(canonical, 0) + 1

        # Boundary edges are those that appear only once in the group
        boundary_edges = []
        for f_idx in group:
            v_hashes = [get_v_hash(tri_verts[f_idx, j]) for j in range(3)]
            edges = [(v_hashes[0], v_hashes[1]), (v_hashes[1], v_hashes[2]), (v_hashes[2], v_hashes[0])]
            for e in edges:
                if group_edge_counts[tuple(sorted(e))] == 1:
                    boundary_edges.append(e)

        if not boundary_edges:
            continue

        # 5. Assemble and Simplify Boundary Loops
        # Group boundary edges into contiguous loops
        edge_map = {e[0]: e[1] for e in boundary_edges}
        loops = []
        while edge_map:
            start_v = next(iter(edge_map))
            loop = [start_v]
            curr_v = edge_map.pop(start_v)
            while curr_v != start_v and curr_v in edge_map:
                loop.append(curr_v)
                next_v = edge_map.pop(curr_v)
                curr_v = next_v
            loops.append(loop)

        # Simplify loops by removing collinear vertices
        simplified_loops = []
        for loop in loops:
            if len(loop) < 3: continue
            simple = []
            for i in range(len(loop)):
                p0 = hash_to_pos[loop[i-1]]
                p1 = hash_to_pos[loop[i]]
                p2 = hash_to_pos[loop[(i+1)%len(loop)]]

                v1 = p1 - p0
                v2 = p2 - p1
                v1_n = np.linalg.norm(v1)
                v2_n = np.linalg.norm(v2)

                if v1_n > 1e-8 and v2_n > 1e-8:
                    cos_a = np.dot(v1, v2) / (v1_n * v2_n)
                    if cos_a > 1.0 - 1e-6: # Collinear
                        continue
                simple.append(loop[i])
            simplified_loops.append(simple)

        def _ear_clip_triangulate(loop_ids):
            """Ear-clipping triangulation of a flat (possibly concave) polygon.
            Returns a list of (a, b, c) index triples into loop_ids."""
            pts = [hash_to_pos[h] for h in loop_ids]
            n = len(pts)
            if n < 3:
                return []
            # Best-fit normal via Newell's method (robust for near-planar loops)
            normal = np.zeros(3)
            for i in range(n):
                p0, p1 = pts[i], pts[(i + 1) % n]
                normal += np.cross(p0, p1)
            norm_len = np.linalg.norm(normal)
            if norm_len < 1e-12:
                return []
            normal /= norm_len
            # Project to 2D on the dominant plane
            axis = np.argmax(np.abs(normal))
            u_axis, v_axis = [i for i in range(3) if i != axis]
            pts2d = [(p[u_axis], p[v_axis]) for p in pts]
            if normal[axis] < 0:
                pts2d = pts2d[::-1]
                loop_ids = loop_ids[::-1]

            def cross2(o, a, b):
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

            def point_in_tri(p, a, b, c):
                d1 = cross2(a, b, p)
                d2 = cross2(b, c, p)
                d3 = cross2(c, a, p)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                return not (has_neg and has_pos)

            indices = list(range(n))
            tris = []
            guard = 0
            while len(indices) > 3 and guard < 10 * n:
                guard += 1
                ear_found = False
                for k in range(len(indices)):
                    i_prev = indices[k - 1]
                    i_curr = indices[k]
                    i_next = indices[(k + 1) % len(indices)]
                    a, b, c = pts2d[i_prev], pts2d[i_curr], pts2d[i_next]
                    if cross2(a, b, c) <= 1e-12:
                        continue  # reflex or degenerate, not an ear
                    if any(
                        point_in_tri(pts2d[idx], a, b, c)
                        for idx in indices
                        if idx not in (i_prev, i_curr, i_next)
                    ):
                        continue
                    tris.append((loop_ids[i_prev], loop_ids[i_curr], loop_ids[i_next]))
                    del indices[k]
                    ear_found = True
                    break
                if not ear_found:
                    break  # degenerate polygon, stop clipping what's left
            if len(indices) == 3:
                tris.append((loop_ids[indices[0]], loop_ids[indices[1]], loop_ids[indices[2]]))
            return tris

        # 6. Triangulate (ear-clipping, handles concave loops)
        for loop in simplified_loops:
            if len(loop) < 3: continue
            for a, b, c in _ear_clip_triangulate(loop):
                final_verts.append([hash_to_pos[a], hash_to_pos[b], hash_to_pos[c]])
                final_idx.extend([curr_v_count, curr_v_count+1, curr_v_count+2, -1])
                curr_v_count += 3


    if not final_verts:
        return np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int32)

    return np.array(final_verts).reshape(-1, 3).astype(np.float32), np.array(final_idx, dtype=np.int32)


def deduplicate_verts(flat_verts, flat_idx, tol=1e-5):
    """
    Deduplicates vertices and updates the index buffer.

    flat_verts: (N, 3) float32
    flat_idx: (M,) int32 with -1 sentinels
    tol: float - rounding tolerance for vertex matching

    Returns: (unique_verts, new_idx)
    """
    if len(flat_verts) == 0:
        return flat_verts, flat_idx

    # 1. Round vertices to tolerance
    rounded = np.round(flat_verts / tol) * tol

    # 2. Find unique vertices
    # np.unique returns unique rows and indices to reconstruct the original array
    unique_verts, inverse = np.unique(rounded, axis=0, return_inverse=True)

    # 3. Remap index buffer
    # flat_idx contains indices into flat_verts. We need to remap them using 'inverse'.
    # -1 sentinels must remain unchanged.

    # Create a mapping for all indices in flat_verts
    # inverse[i] is the index in unique_verts corresponding to flat_verts[i]

    # Work on a copy of flat_idx
    new_idx = flat_idx.copy()

    # Mask for non-sentinel indices
    mask = new_idx != -1

    # Replace mesh indices with their unique vertex counterparts
    # Since flat_verts were emitted 3-per-triangle, flat_idx [0, 1, 2, -1, 3, 4, 5, -1]
    # maps exactly to inverse indices [inverse[0], inverse[1], inverse[2], -1, ...]

    # If the meshers emitted Shared Index buffers this would be different,
    # but currently they emit unique vertices per triangle.
    # The valid indices in flat_idx are always 0..len(flat_verts)-1 in order.
    # Wait, SN and DC might actually use indices differently?
    # No, look at mesh_build in MC/SN/DC:
    # MC: tri_idx = np.stack([base, base+1, base+2], axis=1) -> flat_idx
    # SN: flat_verts = cell_verts[all_tris.ravel()] ... tri_idx = np.stack([base, base+1, base+2], axis=1)
    # DC: (in the portion not seen, but likely similar)

    # So new_idx[mask] are currently 0, 1, 2, 3, 4, 5...
    # We replace them with inverse[new_idx[mask]]
    new_idx[mask] = inverse[new_idx[mask]]

    return unique_verts.astype(np.float32), new_idx.astype(np.int32)
