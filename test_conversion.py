
import FreeCAD
import FreeCADGui
import sys
import os
import math

# Ensure local modules can be found
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Import the command class directly to test logic without GUI selection
from dm_commands import command_convert_to_sdf

def test_conversion():
    doc = FreeCAD.newDocument("TestSDFConversion")
    
    # 1. Create a Standard Part Box
    box_obj = doc.addObject("Part::Box", "OriginalBox")
    box_obj.Length = 25.0
    box_obj.Width = 15.0
    box_obj.Height = 5.0
    
    # Set Reference Placement
    # Rotate 45 deg around Z, move to (10, 20, 0)
    rot = FreeCAD.Rotation(FreeCAD.Vector(0,0,1), 45)
    pos = FreeCAD.Vector(10, 20, 0)
    box_obj.Placement = FreeCAD.Placement(pos, rot)
    
    doc.recompute()
    
    print(f"Created Original Box: {box_obj.Name}", flush=True)
    print(f"  Dims: {box_obj.Length}, {box_obj.Width}, {box_obj.Height}", flush=True)
    print(f"  Pos: {box_obj.Placement.Base}", flush=True)
    
    # 2. Instantiate Command Logic
    cmd = command_convert_to_sdf.ConvertToSDFCommand()
    
    # Verify is_box
    if cmd.is_box(box_obj):
        print("is_box check passed.", flush=True)
    else:
        print("FAILED: is_box check failed.", flush=True)
        return

    # 3. Execute Convert
    print("Converting...", flush=True)
    cmd.convert_box(doc, box_obj)
    
    doc.recompute()
    
    # 4. Verify Result
    sdf_obj = doc.getObject("SDF_OriginalBox")
    if sdf_obj:
        print(f"Created SDF Object: {sdf_obj.Name}")
    else:
        print("FAILED: No SDF object created.")
        return
        
    # Check Properties
    if (sdf_obj.Length == 25.0 and sdf_obj.Width == 15.0 and sdf_obj.Height == 5.0):
        print("Property check passed.")
    else:
        print(f"FAILED: Properties mismatch. L={sdf_obj.Length}, W={sdf_obj.Width}, H={sdf_obj.Height}")
        
    # Check Placement
    # Note: Precision comparison might be needed for floats
    p1 = box_obj.Placement
    p2 = sdf_obj.Placement
    
    dist = (p1.Base - p2.Base).Length
    rot_diff = p1.Rotation.multiply(p2.Rotation.inverted()).getAngle(FreeCAD.Vector(0,0,1))
    
    if dist < 0.001 and abs(rot_diff) < 0.001:
        print("Placement check passed.")
    else:
        print(f"FAILED: Placement mismatch. Dist={dist}, RotDiff={rot_diff}")

    # Check Visibility
    if box_obj.ViewObject.Visibility == False:
        print("Original visibility check passed (Hidden).")
    else:
        print("Warning: Original object not hidden (requires GUI up usually).")

    print("\nTest Complete.")

if __name__ == "__main__":
    test_conversion()
    exit()
