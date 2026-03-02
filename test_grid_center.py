import FreeCAD

def test_grid():
    try:
        doc = FreeCAD.ActiveDocument
        if not doc:
            doc = FreeCAD.newDocument()
            
        print("Creating proxy DMWorkPlane object...")
        from FCDirectModeling.dm_workplane import create_dm_workplane
        wp = create_dm_workplane("DebugGrid")
        
        # Check initial placement
        print(f"Base Placement: {wp.Placement.Base}")
        
    except Exception as e:
        print(f"Error: {e}")

test_grid()
