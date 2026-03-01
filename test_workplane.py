import FreeCAD
import FreeCADGui

doc = FreeCAD.newDocument()

try:
    from FCDirectModeling.dm_workplane import create_dm_workplane
    from FCDirectModeling.primitives.work_plane_creator import WorkPlaneCreator
    from dm_commands.command_work_plane import DM_WorkPlane
    
    cmd = DM_WorkPlane()
    print("DM_WorkPlane loaded successfully")
    
    wp = create_dm_workplane(name="TestWP")
    print(f"Created {wp.Name} successfully, proxy: {wp.Proxy}")
except Exception as e:
    import traceback
    traceback.print_exc()
    print("Error loading or creating work plane:", e)
