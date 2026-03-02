
import FreeCAD
import FreeCADGui
import sys
import subprocess
from core import dm_logger
from PySide import QtGui, QtCore

class InstallDependenciesCommand:
    """
    Command to install required Python dependencies.
    """

    def GetResources(self):
        return {
            "Pixmap": "Std_Addons", # Use a standard icon or generic one
            "MenuText": "Install Dependencies",
            "ToolTip": "Installs shapely, numpy, and scikit-image using pip",
        }

    def Activated(self):
        msg = "This will attempt to install the following libraries using pip:\n\n"
        msg += "- shapely\n- numpy\n- scikit-image\n- scipy\n\n"
        msg += "FreeCAD might become unresponsive for a moment.\n"
        msg += "Do you want to continue?"
        
        reply = QtGui.QMessageBox.question(
            FreeCADGui.getMainWindow(),
            "Install Dependencies",
            msg,
            QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            QtGui.QMessageBox.No
        )
        
        if reply == QtGui.QMessageBox.Yes:
            self.install()

    def install(self):
        # Python executable to use
        python_exe = sys.executable
        
        # specific for FreeCAD bundled python on some platforms? 
        # Usually sys.executable is correct for the internal python.
        
        pkgs = ["shapely", "numpy", "scikit-image", "scipy"]
        
        cmd = [python_exe, "-m", "pip", "install"] + pkgs
        
        try:
            # Create a progress dialog (simple)
            progress = QtGui.QProgressDialog("Installing dependencies...", "Cancel", 0, 0, FreeCADGui.getMainWindow())
            progress.setWindowModality(QtCore.Qt.WindowModal)
            progress.show()
            
            # Using Popen to capture output could be better but blocking check_call is simpler for now
            # To keep GUI alive, we'd need QProcess or threading. 
            # For simplicity, we'll block but it might freeze GUI.
            
            # Let's try subprocess.run
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            progress.close()
            
            if result.returncode == 0:
                QtGui.QMessageBox.information(
                    FreeCADGui.getMainWindow(),
                    "Success",
                    "Dependencies installed successfully!\nPlease restart FreeCAD."
                )
                dm_logger.info("Dependency Install Output:\n" + result.stdout)
            else:
                 QtGui.QMessageBox.critical(
                    FreeCADGui.getMainWindow(),
                    "Installation Failed",
                    f"Error Occurred:\n{result.stderr}"
                )
                 dm_logger.error("Dependency Install Failed:\n" + result.stderr)

        except Exception as e:
            QtGui.QMessageBox.critical(
                FreeCADGui.getMainWindow(),
                "Error",
                str(e)
            )
            dm_logger.error(f"Exception during install: {e}")

    def IsActive(self):
        return True

FreeCADGui.addCommand("DM_InstallDependencies", InstallDependenciesCommand())
