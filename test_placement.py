import FreeCAD

def synthesize_placement(pt, normal, up_hint):
    x_axis = up_hint.cross(normal)
    if x_axis.Length < 0.01:
        x_axis = FreeCAD.Vector(1,0,0).cross(normal)
        if x_axis.Length < 0.01:
            x_axis = FreeCAD.Vector(0,1,0).cross(normal)
    x_axis.normalize()
    y_axis = normal.cross(x_axis)
    y_axis.normalize()
    
    m = FreeCAD.Matrix(
        x_axis.x, y_axis.x, normal.x, pt.x,
        x_axis.y, y_axis.y, normal.y, pt.y,
        x_axis.z, y_axis.z, normal.z, pt.z,
        0, 0, 0, 1
    )
    return FreeCAD.Placement(m)

pt = FreeCAD.Vector(0,0,0)
normal = FreeCAD.Vector(0,1,0) # User clicking on Y-facing plane (e.g. front of box)
up_hint = FreeCAD.Vector(0,1,0) # Initial workplane is XY plane. Up is +Y

plc = synthesize_placement(pt, normal, up_hint)
rot = plc.Rotation
print(f"Normal: {normal}")
print(f"Up hint: {up_hint}")
print(f"Result X: {rot.multVec(FreeCAD.Vector(1,0,0))}")
print(f"Result Y: {rot.multVec(FreeCAD.Vector(0,1,0))}")
print(f"Result Z: {rot.multVec(FreeCAD.Vector(0,0,1))}")
