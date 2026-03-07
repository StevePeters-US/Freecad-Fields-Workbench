import os
import time
import FreeCAD
import traceback

_throttle_times = {}  # key -> last_emit_time

_PARAM_PATH = "User parameter:FCDirectModeling"

def get_enable_crash_log():
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnableCrashLog", True) # Default True
    except:
        return True

def set_enable_crash_log(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnableCrashLog", bool(val))
    except:
        pass

def get_log_path():
    try:
        app_data = FreeCAD.ConfigGet("UserAppData")
        return os.path.join(app_data, "DirectModeling.log")
    except:
        return os.path.expanduser("~/DirectModeling.log")

def _log(level, msg):
    """Internal helper to write to file and console."""
    # Handle multi-line messages (e.g. tracebacks)
    text = str(msg)
    lines = text.splitlines()
    
    # Always print to FreeCAD console
    # Use Message for INFO/DEBUG, Warning for WARN, Error for ERROR
    for line in lines:
        formatted = f"[{level}] {line}" if level else line
        
        if level == "ERROR":
            FreeCAD.Console.PrintError(formatted + "\n")
        elif level == "WARN":
            FreeCAD.Console.PrintWarning(formatted + "\n")
        elif level == "LOG":
            FreeCAD.Console.PrintLog(formatted + "\n")
        else:
            FreeCAD.Console.PrintMessage(formatted + "\n")

    # Log to file if enabled OR if it's an ERROR (crash investigation)
    if get_enable_crash_log() or level == "ERROR":
        try:
            log_p = get_log_path()
            d = os.path.dirname(log_p)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            
            with open(log_p, "a", encoding="utf-8") as f:
                for line in lines:
                    formatted = f"[{level}] {line}" if level else line
                    f.write(formatted + "\n")
                f.flush()
        except:
            pass

def log(msg):
    """Generic log message (FreeCAD.Console.PrintLog equivalent)."""
    _log("LOG", msg)

def info(msg):
    _log("INFO", msg)

def debug(msg):
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)

def exception(msg_header=""):
    """Logs the current exception traceback as an ERROR."""
    full_msg = f"{msg_header}\n{traceback.format_exc()}"
    _log("ERROR", full_msg)

def debug_throttled(key, msg, interval=0.5):
    """Like debug(), but only emits once per `interval` seconds for a given key."""
    now = time.monotonic()
    if now - _throttle_times.get(key, 0) >= interval:
        _throttle_times[key] = now
        _log("DEBUG", msg)
