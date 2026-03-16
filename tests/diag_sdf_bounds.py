"""
Diagnostic script: SDF bounds vs ray march render alignment.

Run this in FreeCAD's Python console:
    exec(open("/path/to/tests/diag_sdf_bounds.py").read())

Creates known primitives (sphere, box) at the origin with known dimensions,
then prints baking metadata and enables debug visualization so you can
visually compare the wireframe bounding box against the rendered surface.
"""
import sys, os
# Ensure the workbench root is on the path
_wb_root = "/home/steve/Documents/Github/Freecad-Direct-Modeling"
if _wb_root not in sys.path:
    sys.path.insert(0, _wb_root)

import FreeCAD
import FreeCADGui
import numpy as np
from pivy import coin

from core.dm_object import (
    create_dm_object, get_meshing_cell_size,
    set_render_debug_mode, get_render_debug_mode,
)
from core.frep.sdf.sphere import SdfSphereField
from core.frep.sdf.box import SdfBoxField
from core.frep.sdf_baker import bake_sdf_to_volume
from core.dm_scene_ray_march_renderer import DMSceneRayMarchRenderer


def _print_bake_info(label, field, cell_size):
    """Bake a field and print all metadata for inspection."""
    b = bake_sdf_to_volume(field, cell_size)
    mn, mx = b["bbox_min"], b["bbox_max"]
    print(f"\n=== {label} (cell_size={cell_size}) ===")
    print(f"  Field bbox:  {field.bounding_box()}")
    print(f"  Baked bbox:  min=({mn.x:.2f}, {mn.y:.2f}, {mn.z:.2f})  "
          f"max=({mx.x:.2f}, {mx.y:.2f}, {mx.z:.2f})")
    print(f"  Grid cells:  nx={b['nx']}  ny={b['ny']}  nz={b['nz']}")
    print(f"  Sample pts:  {b['nx']+1} x {b['ny']+1} x {b['nz']+1}")
    vol_bytes = b["volume_bytes"]
    vol = np.frombuffer(vol_bytes, dtype=np.float32)
    print(f"  Volume shape: {vol.shape}  "
          f"(expect {(b['nz']+1)*(b['ny']+1)*(b['nx']+1)})")
    print(f"  SDF range:   [{vol.min():.4f}, {vol.max():.4f}]")
    # Check center texel value (should be negative = inside)
    cx = (b['nx']+1) // 2
    cy = (b['ny']+1) // 2
    cz = (b['nz']+1) // 2
    vol3 = vol.reshape(b['nz']+1, b['ny']+1, b['nx']+1)
    print(f"  Center texel [{cx},{cy},{cz}] = {vol3[cz, cy, cx]:.4f}")

    # Texture dimension check
    tex_w, tex_h, tex_d = b['nx']+1, b['ny']+1, b['nz']+1
    expected_bytes = tex_w * tex_h * tex_d * 4
    print(f"  Texture dims: {tex_w} x {tex_h} x {tex_d}  "
          f"({expected_bytes} bytes, got {len(vol_bytes)})")

    # Verify axis ordering: sample SDF at known world-space points
    # Point at bbox center should be inside (negative SDF)
    center_world = FreeCAD.Vector(
        (mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2)
    center_sdf = field.evaluate(center_world)
    print(f"  Analytical SDF at bbox center: {center_sdf:.4f}")

    # Points at bbox faces should be outside (positive SDF) or near-zero
    for axis, name in [(0, "X"), (1, "Y"), (2, "Z")]:
        pt_min = FreeCAD.Vector(mn.x, mn.y, mn.z)
        pt_max = FreeCAD.Vector(mx.x, mx.y, mx.z)
        # Midpoint of min face
        face_pt = FreeCAD.Vector(
            (mn.x + mx.x) / 2, (mn.y + mx.y) / 2, (mn.z + mx.z) / 2)
        if axis == 0:
            face_pt.x = mn.x
        elif axis == 1:
            face_pt.y = mn.y
        else:
            face_pt.z = mn.z
        face_sdf = field.evaluate(face_pt)
        print(f"  SDF at {name}-min face center: {face_sdf:.4f}")

    return b


def _create_test_primitive(name, field):
    """Create a frep object and assign the field."""
    obj = create_dm_object(name=name, shape_type="frep")
    obj.Proxy.FRepField = field
    obj.touch()
    obj.Document.recompute([obj])
    return obj


def _add_debug_cube(mn, mx, label=""):
    """Add a thin wireframe cube at the baked bbox for visual comparison."""
    sg = FreeCADGui.ActiveDocument.ActiveView.getSceneGraph()
    sep = coin.SoSeparator()

    # Orange wireframe
    mat = coin.SoMaterial()
    mat.diffuseColor.setValue(1.0, 0.0, 0.0)
    mat.transparency.setValue(0.0)
    sep.addChild(mat)

    draw = coin.SoDrawStyle()
    draw.style.setValue(coin.SoDrawStyle.LINES)
    draw.lineWidth.setValue(2.0)
    sep.addChild(draw)

    coords = coin.SoCoordinate3()
    coords.point.setValues(0, 8, [
        (mn.x, mn.y, mn.z), (mx.x, mn.y, mn.z),
        (mn.x, mx.y, mn.z), (mx.x, mx.y, mn.z),
        (mn.x, mn.y, mx.z), (mx.x, mn.y, mx.z),
        (mn.x, mx.y, mx.z), (mx.x, mx.y, mx.z),
    ])
    sep.addChild(coords)

    lines = coin.SoIndexedLineSet()
    lines.coordIndex.setValues(0, 36, [
        0,1,-1, 1,3,-1, 3,2,-1, 2,0,-1,
        4,5,-1, 5,7,-1, 7,6,-1, 6,4,-1,
        0,4,-1, 1,5,-1, 2,6,-1, 3,7,-1,
    ])
    sep.addChild(lines)

    sg.addChild(sep)
    print(f"  [DEBUG] Added red wireframe cube for '{label}' at "
          f"({mn.x:.1f},{mn.y:.1f},{mn.z:.1f})-({mx.x:.1f},{mx.y:.1f},{mx.z:.1f})")
    return sep


def run_diagnostic():
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("SDF_Diag")

    cell_size = get_meshing_cell_size()
    print(f"\n{'='*60}")
    print(f"SDF Bounds Diagnostic")
    print(f"{'='*60}")
    print(f"Global cell_size = {cell_size}")

    # --- Test 1: Sphere at origin, radius=25 ---
    sphere_field = SdfSphereField(
        center=FreeCAD.Vector(0, 0, 0),
        radius=25.0,
        placement=None  # No transform — pure world space
    )
    b_sphere = _print_bake_info("Sphere(r=25, center=origin)", sphere_field, cell_size)
    obj_sphere = _create_test_primitive("DiagSphere", sphere_field)
    _add_debug_cube(b_sphere["bbox_min"], b_sphere["bbox_max"], "Sphere")

    # --- Test 2: Box at origin, size=40x40x40 ---
    box_field = SdfBoxField(
        center=FreeCAD.Vector(0, 0, 0),
        size=FreeCAD.Vector(40, 40, 40),
        placement=None
    )
    b_box = _print_bake_info("Box(40x40x40, center=origin)", box_field, cell_size)
    obj_box = _create_test_primitive("DiagBox", box_field)
    _add_debug_cube(b_box["bbox_min"], b_box["bbox_max"], "Box")

    # --- Test 3: Offset sphere to check spatial alignment ---
    sphere_field_offset = SdfSphereField(
        center=FreeCAD.Vector(60, 0, 0),
        radius=15.0,
        placement=None
    )
    b_off = _print_bake_info("Sphere(r=15, center=(60,0,0))", sphere_field_offset, cell_size)
    obj_off = _create_test_primitive("DiagSphereOffset", sphere_field_offset)
    _add_debug_cube(b_off["bbox_min"], b_off["bbox_max"], "SphereOffset")

    # --- Enable debug mode ---
    set_render_debug_mode(True)
    sr = DMSceneRayMarchRenderer.get_instance()
    sr.on_prefs_changed()
    print(f"\nRenderDebugMode = {get_render_debug_mode()}")
    print(f"Scene renderer has {len(sr._fields)} fields registered")
    for label, (f, vis) in sr._fields.items():
        print(f"  '{label}': visible={vis}, type={type(f).__name__}")

    # --- Print scene renderer uniform state ---
    print("\nScene renderer uniforms:")
    for key in ["u_num_fields", "u_z_total"]:
        print(f"  {key} = {sr._u[key].value.getValue()}")
    for fi in range(min(sr._u["u_num_fields"].value.getValue(), 8)):
        nx = sr._u[f"u_nx[{fi}]"].value.getValue()
        ny = sr._u[f"u_ny[{fi}]"].value.getValue()
        nz = sr._u[f"u_nz[{fi}]"].value.getValue()
        zo = sr._u[f"u_z_offset[{fi}]"].value.getValue()
        bmn = sr._u[f"u_bbox_min[{fi}]"].value.getValue()
        bmx = sr._u[f"u_bbox_max[{fi}]"].value.getValue()
        sub = sr._u[f"u_is_subtractive[{fi}]"].value.getValue()
        print(f"  field[{fi}]: nx={nx} ny={ny} nz={nz} z_off={zo} sub={sub}")
        print(f"    bbox_min=({bmn[0]:.2f},{bmn[1]:.2f},{bmn[2]:.2f}) "
              f"bbox_max=({bmx[0]:.2f},{bmx[1]:.2f},{bmx[2]:.2f})")

    # --- Zoom to fit ---
    try:
        FreeCADGui.ActiveDocument.ActiveView.viewIsometric()
        FreeCADGui.ActiveDocument.ActiveView.fitAll()
    except Exception:
        pass

    print(f"\n{'='*60}")
    print("DIAGNOSTIC COMPLETE")
    print("Compare: red wireframe cubes (baked bbox) vs orange rendered surfaces.")
    print("If the surface extends beyond or doesn't fill the red cube,")
    print("there's a bounds/axis mismatch.")
    print(f"{'='*60}")

    # --- Take screenshot ---
    try:
        shot_path = os.path.join(_wb_root, "tests", "diag_screenshot.png")
        FreeCADGui.ActiveDocument.ActiveView.saveImage(shot_path, 1920, 1080, "Current")
        print(f"\nScreenshot saved: {shot_path}")
    except Exception as e:
        print(f"\nScreenshot failed: {e}")


# Run it
run_diagnostic()
