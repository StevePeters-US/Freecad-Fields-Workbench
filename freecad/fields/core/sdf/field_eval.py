# SPDX-License-Identifier: CC-BY-NC-SA-4.0
import numpy as np
import time
import weakref
from freecad.fields.core.objects.fld_object import get_gpu_field_eval
from freecad.fields.core.sdf.gpu_field_eval import GpuFieldEvaluator

_gpu_evaluators = weakref.WeakKeyDictionary()
_GPU_FAILED = "gpu_failed"  # sentinel: don't retry shader compile for this field

# Running tally of every field evaluation that goes through eval_grid — the one
# funnel for CPU SDF work (octree builds, meshing, snapping, warp bakes). Always
# on: two perf_counter() calls against a batched evaluation of thousands of
# points is free, and the alternative is having no idea where a stalled
# interaction spent its seconds. Nested evaluate_grid() calls inside a composed
# field do not re-enter here, so a point is counted once, against the top field.
_EVAL_STATS = {"calls": 0, "points": 0, "seconds": 0.0, "by_field": {}}


def eval_stats_reset():
    """Zero the tally. Call at the start of a phase you want to attribute."""
    _EVAL_STATS["calls"] = 0
    _EVAL_STATS["points"] = 0
    _EVAL_STATS["seconds"] = 0.0
    _EVAL_STATS["by_field"] = {}


def eval_stats_snapshot():
    """A copy of the tally: totals plus a {field_class: (calls, points, seconds)}."""
    snap = dict(_EVAL_STATS)
    snap["by_field"] = dict(_EVAL_STATS["by_field"])
    return snap


def eval_stats_delta(before):
    """What has been evaluated since `before` (an earlier snapshot)."""
    return {
        "calls": _EVAL_STATS["calls"] - before["calls"],
        "points": _EVAL_STATS["points"] - before["points"],
        "seconds": _EVAL_STATS["seconds"] - before["seconds"],
    }


def eval_stats_line(stats=None):
    """One-line summary of a snapshot or delta, for a log message."""
    s = stats if stats is not None else _EVAL_STATS
    return (f"{s['calls']} eval_grid call(s), {s['points']} points, "
            f"{s['seconds']*1000:.1f}ms")


def _tally(field, n_points, t0):
    _EVAL_STATS["calls"] += 1
    _EVAL_STATS["points"] += n_points
    dt = time.perf_counter() - t0
    _EVAL_STATS["seconds"] += dt
    name = type(field).__name__
    calls, points, seconds = _EVAL_STATS["by_field"].get(name, (0, 0, 0.0))
    _EVAL_STATS["by_field"][name] = (calls + 1, points + n_points, seconds + dt)


def eval_grid(field, points_np) -> np.ndarray:
    """Evaluates the field on an (N, 3) array of points.
    Routes to GPU compute shader evaluation if enabled and available,
    otherwise falls back to CPU evaluation.
    """
    t0 = time.perf_counter()
    n_points = len(points_np)

    if get_gpu_field_eval() and GpuFieldEvaluator.is_available():
        evaluator = _gpu_evaluators.get(field)
        if evaluator is not _GPU_FAILED:
            try:
                if evaluator is None:
                    evaluator = GpuFieldEvaluator(field)
                    _gpu_evaluators[field] = evaluator
                out = evaluator.evaluate(points_np)
                _tally(field, n_points, t0)
                return out
            except NotImplementedError as e:
                # A capability the GPU path declines up front, not a failure: the
                # evaluator checked, said no, and the CPU result below is correct.
                # Logging it at error level puts a red line in the report view for a
                # working operation, which is how "patch extrude, then add a torus"
                # came in as a bug report. Say it once, at info, and move on.
                from freecad.fields.core import fld_logger
                fld_logger.info(
                    f"{type(field).__name__}: evaluating on the CPU because the GPU "
                    f"path does not support this field — {e}")
                _gpu_evaluators[field] = _GPU_FAILED
            except Exception as e:
                from freecad.fields.core import fld_logger
                fld_logger.error(
                    f"GPU field eval failed for {type(field).__name__}, "
                    f"using CPU for this field from now on: {e}")
                # Cache the failure — retrying means a full shader recompile
                # on every eval_grid call (e.g. once per meshing batch).
                _gpu_evaluators[field] = _GPU_FAILED

    out = field.evaluate_grid(points_np)
    _tally(field, n_points, t0)
    return out
