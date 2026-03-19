import sys
import FreeCAD

def test_box_math():
    try:
        from core.frep.sdf.box import SdfBoxField
        
        # 1. Create angled WP
        rot = FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 45)
        wp = FreeCAD.Placement(FreeCAD.Vector(10, 20, 30), rot)
        
        def to_local(p):
            mat = wp.toMatrix()
            mat.invert()
            return mat.multVec(p)
            
        p1 = wp.multVec(FreeCAD.Vector(0,0,0))
        p2 = wp.multVec(FreeCAD.Vector(10,10,0))
        p3 = wp.multVec(FreeCAD.Vector(10,10,5))
        
        loc_p1 = to_local(p1)
        loc_p2 = to_local(p2)
        loc_p3 = to_local(p3)
        
        size_x = abs(loc_p2.x - loc_p1.x)
        size_y = abs(loc_p2.y - loc_p1.y)
        size_z = abs(loc_p3.z - loc_p1.z)
        cx = (loc_p1.x + loc_p2.x) / 2.0
        cy = (loc_p1.y + loc_p2.y) / 2.0
        cz = (loc_p1.z + loc_p3.z) / 2.0
        
        bx = SdfBoxField(FreeCAD.Vector(cx, cy, cz), FreeCAD.Vector(size_x, size_y, size_z), placement=wp)
        
        # top center
        pt = wp.multVec(FreeCAD.Vector(5, 5, 5))
        dist = bx.evaluate(pt)
        print(f"Distance to top center ({pt}): {dist}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    test_box_math()
