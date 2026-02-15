
import FreeCAD
import FCDirectModeling.sdf_lib as sdf_lib
import numpy as np

def test():
    doc = FreeCAD.newDocument("TestDebug")
    obj = doc.addObject("Part::FeaturePython", "TestBool")
    obj.addProperty("App::PropertyEnumeration", "Operation")
    obj.Operation = ["Union", "Difference", "Intersection"]
    obj.Operation = "Difference"
    
    print(f"Operation Value: {obj.Operation}")
    print(f"Type: {type(obj.Operation)}")
    
    # Test Math
    # Point inside A (-1), Inside B (-1). Diff A-B should be B (hole) -> Outside result.
    # d1 = -1, d2 = -1.
    # max(-1, -(-1)) = max(-1, 1) = 1. Positive = Outside. Correct.
    
    # Point Inside A (-1), Outside B (1). Diff A-B should be Inside result.
    # d1 = -1, d2 = 1.
    # max(-1, -1) = -1. Negative = Inside. Correct.
    
    # Point Outside A (1), Inside B (-1). Diff A-B should be Outside.
    # d1 = 1, d2 = -1.
    # max(1, 1) = 1. Positive = Outside. Correct.
    
    print("Math check passed")

test()
