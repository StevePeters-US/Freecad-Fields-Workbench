# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Pure cage rebuild: a new topology and the old field in, the replacement field and its property arrays out."""
import FreeCAD
import numpy as np

from freecad.fields.core.sdf.sdf.cage import SdfCageField, rebuild_handles_and_types, remap_handle_displacements
from freecad.fields.core.sdf.sdf.cage_deform import SdfCageDeformField


def build_replacement_field(field, new_topo, vertex_map=None, new_source=None,
                            vertex_overrides=None, handles=None, handle_types=None,
                            edge_sharpness=None):
    """Return (new_field, new_types, new_points_fc, face_verts_flat, face_sizes)
    for the topology `new_topo`, as `_rebuild_cage_from_topology` built them."""
    v_map_fn = vertex_map if callable(vertex_map) else (lambda v: vertex_map.get(v, v) if isinstance(vertex_map, dict) else v)

    new_faces = new_topo.face_vertex_lists()
    face_verts_flat, face_sizes, _ = new_topo.net_arrays()

    if isinstance(field, SdfCageDeformField):
        rest_verts = np.array([v.pos for v in new_topo.vertices], dtype=np.float64)
        if handles is not None:
            curr_handles = np.array(handles, dtype=np.float64)
            rest_handles = curr_handles.copy()
            new_types = list(handle_types) if handle_types is not None else [1] * len(curr_handles)
            disp_h = np.zeros_like(curr_handles)
        else:
            class _RestProxy:
                pass
            rest_proxy = _RestProxy()
            rest_proxy.vertices = field.rest_vertices
            rest_proxy.handles = field.rest_handles
            rest_proxy._edges = field._edges
            rest_proxy._handle_types = field._handle_types
            rest_handles, new_types = rebuild_handles_and_types(rest_proxy, new_topo, v_map_fn)

            # Preserve handle displacements frame-consistently. new_topo lives in
            # the REST frame while field.handles live in the CURRENT (deformed)
            # frame; rebuilding absolute curr_handles by mixing the two (the
            # patch-cage path) leaks the vertex displacement into every handle and
            # blows the cage into spikes on the second extrude. Carry the
            # frame-independent handle displacement instead. (Handle types are
            # frame-independent, so take them from the rest rebuild above.)
            old_handle_disp = field.displacements[len(field.vertices):]
            disp_h = remap_handle_displacements(
                field._edges, old_handle_disp, new_topo.edges, v_map_fn)
            curr_handles = rest_handles + disp_h

        curr_verts = rest_verts.copy()
        for vi_new in range(len(new_topo.vertices)):
            if vertex_overrides and vi_new in vertex_overrides:
                curr_verts[vi_new] = vertex_overrides[vi_new]
                continue
            old_v = v_map_fn(vi_new)
            if old_v < len(field.vertices):
                curr_verts[vi_new] = field.vertices[old_v]

        disp_v = curr_verts - rest_verts
        displacements = np.vstack([disp_v, disp_h])

        new_field = SdfCageDeformField(
            source=(new_source if new_source is not None else field.source),
            vertices=curr_verts,
            handles=curr_handles,
            face_verts=new_faces,  # list-of-lists: SdfCageDeformField has no face_sizes param
            edges=new_topo.edges,
            handle_types=new_types,
            edge_straight=getattr(field, "_edge_straight", None),
            edge_sharpness=edge_sharpness if edge_sharpness is not None else getattr(field, "_edge_sharpness", None),
            rest_vertices=rest_verts,
            rest_handles=rest_handles,
            displacements=displacements,
            placement=field.placement,
        )
        new_field.sign_mode = getattr(field, "sign_mode", "closest")
        new_points_list = list(curr_verts) + list(curr_handles)
        new_points_fc = [FreeCAD.Vector(p[0], p[1], p[2]) for p in new_points_list]
    else:
        new_verts = np.array([v.pos for v in new_topo.vertices], dtype=np.float64)
        if handles is not None:
            new_handles = np.array(handles, dtype=np.float64)
            new_types = list(handle_types) if handle_types is not None else [1] * len(new_handles)
        else:
            new_handles, new_types = rebuild_handles_and_types(field, new_topo, v_map_fn)
        new_field = SdfCageField(
            vertices=new_verts,
            handles=new_handles,
            face_verts=face_verts_flat,
            face_sizes=face_sizes,
            edges=new_topo.edges,
            placement=field.placement,
            handle_types=new_types,
            edge_sharpness=edge_sharpness if edge_sharpness is not None else getattr(field, "_edge_sharpness", None),
        )
        new_field.sign_mode = getattr(field, "sign_mode", "closest")
        new_points_list = list(new_verts) + list(new_handles)
        new_points_fc = [FreeCAD.Vector(p[0], p[1], p[2]) for p in new_points_list]

    return new_field, new_types, new_points_fc, face_verts_flat, face_sizes
