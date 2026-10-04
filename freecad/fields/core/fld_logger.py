# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import FreeCAD
import os
import sys
import time
import traceback

_throttle_times = {}  # key -> last_emit_time
_THROTTLE_TIMES_MAX = 1024  # eviction cap so long sessions with many keys don't leak

_PARAM_PATH = "User parameter:BaseApp/Preferences/fields"  # == fld_prefs.PREFS_PATH; pinned by test_prefs_path
_crash_log_enabled = None  # cached EnableCrashLog (None = not yet loaded)

def get_enable_crash_log():
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnableCrashLog", True) # Default True
    except Exception:
        return True

def set_enable_crash_log(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnableCrashLog", bool(val))
    except Exception as exc:
        try:
            if sys.__stderr__ is not None:
                sys.__stderr__.write(f"fld_logger: set_enable_crash_log failed: {exc}\n")
        except Exception:
            pass  # Allowed: sys.__stderr__ write can fail or be None on Windows
    global _crash_log_enabled
    _crash_log_enabled = bool(val)

# ── Debug logging categories ────────────────────────────────────────────────
# debug() output is gated by (a) a master switch and (b) a per-category switch.
# Render debug is very verbose, so it defaults OFF; everything else defaults ON.
# Values are cached (see _refresh_debug_cache) so the hot paths that call
# debug() every frame do not hit FreeCAD.ParamGet on each call.
DEBUG_CATEGORIES = ("general", "render", "cage", "sdf", "freecad.fields.tools", "input")
_DEBUG_CATEGORY_DEFAULTS = {
    "general": True,
    "render":  False,   # very verbose; off by default (see settings to re-enable)
    "cage":    True,
    "sdf":     True,
    "freecad.fields.tools":   True,
    "input":   True,
}

_debug_master = None          # cached master switch (None = not yet loaded)
_debug_cats = None            # cached {category: bool}

def get_enable_debug_log():
    """Master debug switch. When False, no debug()/debug_throttled() output."""
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool("EnableDebugLog", True)
    except Exception:
        return True

def set_enable_debug_log(val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool("EnableDebugLog", bool(val))
    except Exception as exc:
        try:
            if sys.__stderr__ is not None:
                sys.__stderr__.write(f"fld_logger: set_enable_debug_log failed: {exc}\n")
        except Exception:
            pass  # Allowed: sys.__stderr__ write can fail or be None on Windows
    _refresh_debug_cache()

def get_debug_category(cat):
    default = _DEBUG_CATEGORY_DEFAULTS.get(cat, True)
    try:
        return FreeCAD.ParamGet(_PARAM_PATH).GetBool(f"DebugCat_{cat}", default)
    except Exception:
        return default

def set_debug_category(cat, val):
    try:
        FreeCAD.ParamGet(_PARAM_PATH).SetBool(f"DebugCat_{cat}", bool(val))
    except Exception as exc:
        try:
            if sys.__stderr__ is not None:
                sys.__stderr__.write(f"fld_logger: set_debug_category failed: {exc}\n")
        except Exception:
            pass  # Allowed: sys.__stderr__ write can fail or be None on Windows
    _refresh_debug_cache()

def _refresh_debug_cache():
    """Reload cached debug switches from params (call after settings change)."""
    global _debug_master, _debug_cats, _crash_log_enabled
    _debug_master = get_enable_debug_log()
    _debug_cats = {c: get_debug_category(c) for c in DEBUG_CATEGORIES}
    _crash_log_enabled = get_enable_crash_log()

def refresh_debug_settings():
    """Public alias — the settings dialog calls this on accept."""
    _refresh_debug_cache()

def _debug_active(category):
    if _debug_master is None:
        _refresh_debug_cache()
    if not _debug_master:
        return False
    return _debug_cats.get(category, True)

def get_log_path():
    try:
        app_data = FreeCAD.ConfigGet("UserAppData")
        return os.path.join(app_data, "Fields.log")
    except Exception:
        return os.path.expanduser("~/Fields.log")

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
    global _crash_log_enabled
    if _crash_log_enabled is None:
        _crash_log_enabled = get_enable_crash_log()
    if _crash_log_enabled or level == "ERROR":
        try:
            log_p = get_log_path()
            d = os.path.dirname(log_p)
            if d and not os.path.exists(d):
                os.makedirs(d, exist_ok=True)
            _rotate_if_full(log_p)

            with open(log_p, "a", encoding="utf-8") as f:
                for line in lines:
                    formatted = f"[{level}] {line}" if level else line
                    f.write(formatted + "\n")
                f.flush()
        except Exception as exc:
            try:
                if sys.__stderr__ is not None:
                    sys.__stderr__.write(f"fld_logger: log file write failed: {exc}\n")
            except Exception:
                pass  # Allowed: sys.__stderr__ write can fail or be None on Windows

_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 3


def _rotate_if_full(path):
    """Cap the log at _LOG_MAX_BYTES: shift path -> path.1 -> ... -> path.<_LOG_BACKUPS>."""
    if not os.path.exists(path) or os.path.getsize(path) < _LOG_MAX_BYTES:
        return
    for i in range(_LOG_BACKUPS - 1, 0, -1):
        if os.path.exists(f"{path}.{i}"):
            os.replace(f"{path}.{i}", f"{path}.{i + 1}")
    os.replace(path, f"{path}.1")


def log(msg):
    """Generic log message (FreeCAD.Console.PrintLog equivalent)."""
    _log("LOG", msg)

def info(msg):
    _log("INFO", msg)

def debug(msg, category="general"):
    if not _debug_active(category):
        return
    _log("DEBUG", msg)

def warn(msg):
    _log("WARN", msg)

def error(msg):
    _log("ERROR", msg)

def exception(msg_header=""):
    """Logs the current exception traceback as an ERROR."""
    full_msg = f"{msg_header}\n{traceback.format_exc()}"
    _log("ERROR", full_msg)

def debug_throttled(key, msg, interval=0.5, category="general"):
    """Like debug(), but only emits once per `interval` seconds for a given key."""
    if not _debug_active(category):
        return
    now = time.monotonic()
    if now - _throttle_times.get(key, 0) >= interval:
        if len(_throttle_times) > _THROTTLE_TIMES_MAX:
            _throttle_times.clear()
        _throttle_times[key] = now
        _log("DEBUG", msg)


def render_debug(msg):
    """debug() tagged with the (verbose, default-off) 'render' category."""
    debug(msg, category="render")


def render_debug_throttled(key, msg, interval=0.5):
    """debug_throttled() tagged with the 'render' category."""
    debug_throttled(key, msg, interval=interval, category="render")
