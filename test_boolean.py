
import FreeCAD
import FCDirectModeling.sdf_renderer as sdf_renderer
import FCDirectModeling.sdf_lib as sdf_lib

def test_boolean():
    doc = FreeCAD.newDocument("TestBoolean")
    
    # Box 1
    box1 = doc.addObject("Part::FeaturePython", "Box1")
    sdf_renderer.SDFBoxFeature(box1)
    box1.addProperty("App::PropertyLength", "Length").Length = 20.0
    box1.addProperty("App::PropertyLength", "Width").Width = 20.0
    box1.addProperty("App::PropertyLength", "Height").Height = 20.0
    box1.Placement = FreeCAD.Placement(FreeCAD.Vector(0,0,0), FreeCAD.Rotation())
    sdf_renderer.SDFRenderer(box1.ViewObject)
    
    # Box 2 (Offset)
    box2 = doc.addObject("Part::FeaturePython", "Box2")
    sdf_renderer.SDFBoxFeature(box2)
    box2.addProperty("App::PropertyLength", "Length").Length = 20.0
    box2.addProperty("App::PropertyLength", "Width").Width = 20.0
    box2.addProperty("App::PropertyLength", "Height").Height = 20.0
    box2.Placement = FreeCAD.Placement(FreeCAD.Vector(10,10,10), FreeCAD.Rotation())
    sdf_renderer.SDFRenderer(box2.ViewObject)
    
    # Boolean Union
    union = doc.addObject("App::FeaturePython", "Union")
    sdf_renderer.SDFBooleanFeature(union)
    union.addProperty("App::PropertyEnumeration", "Operation")
    union.Operation = ["Union", "Difference", "Intersection"]
    union.Operation = "Union"
    
    union.addProperty("App::PropertyLink", "Base").Base = box1
    union.addProperty("App::PropertyLink", "Tool").Tool = box2
    union.addProperty("App::PropertyInteger", "Resolution").Resolution = 32
    union.addProperty("App::PropertyFloat", "Margin").Margin = 0.5 # Larger margin for combined shape
    
    # Attach Renderer
    vp = sdf_renderer.SDFRenderer(union.ViewObject)
    
    print("Updating Union Object...")
    vp.update()
    
    # Check if logic works (would typically verify mesh vertices count)
    print(f"Union Coords: {vp.coords.point.getNum()}")
    if vp.coords.point.getNum() > 0:
        print("Union Mesh Generated Successfully")
    else:
        print("Union Mesh Generation Failed")

    # Boolean Difference
    diff = doc.addObject("App::FeaturePython", "Difference")
    sdf_renderer.SDFBooleanFeature(diff)
    diff.addProperty("App::PropertyEnumeration", "Operation")
    diff.Operation = ["Union", "Difference", "Intersection"]
    diff.Operation = "Difference"
    
    diff.addProperty("App::PropertyLink", "Base").Base = box1
    diff.addProperty("App::PropertyLink", "Tool").Tool = box2
    diff.addProperty("App::PropertyInteger", "Resolution").Resolution = 32
    diff.addProperty("App::PropertyFloat", "Margin").Margin = 0.5
    
    vp_diff = sdf_renderer.SDFRenderer(diff.ViewObject)
    print("Updating Difference Object...")
    vp_diff.update()
    print(f"Difference Coords: {vp_diff.coords.point.getNum()}")
    
    if vp_diff.coords.point.getNum() > 0:
        print("Difference Mesh Generated Successfully")
    else:
        print("Difference Mesh Generation Failed")
        
test_boolean()
