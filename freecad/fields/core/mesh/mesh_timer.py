# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/mesh/mesh_timer.py

Per-stage timing accumulator for the mesh() pipeline in fld_mesher.py.
`mesh_timer` is a module-level singleton shared across all mesher calls for
the same tool session; call `.summary()` once (on tool commit) to print a
single INFO log and reset.
"""
import time

from freecad.fields.core import fld_logger
from freecad.fields.core.fld_settings import get_perf_profiler_enabled


class MeshTimer:
    """Accumulates timing across multiple mesh() calls.
    Call summary() once (on tool commit) to print a single INFO log.
    """
    _STAGES = [
        "field_eval", "cube_index", "active_filter",
        "corner_extract", "edge_interp", "tri_extract", "mesh_build",
        "decimate", "deduplicate",
        "mb_list_conv", "mb_mesh_obj", "mb_make_shape", "mb_make_solid"
    ]
    # Sub-stages to indent in summary output
    _SUB_STAGES = {"mb_list_conv", "mb_mesh_obj", "mb_make_shape", "mb_make_solid"}

    def __init__(self):
        self.reset()

    def reset(self):
        self._totals  = {s: 0.0 for s in self._STAGES}
        self._calls   = 0
        self._start   = {}

    def start(self, stage: str):
        self._start[stage] = time.perf_counter()

    def stop(self, stage: str):
        if stage in self._start:
            if stage not in self._totals:
                self._totals[stage] = 0.0
            self._totals[stage] += time.perf_counter() - self._start.pop(stage)

    def tick(self):
        """Call once per mesh() invocation so we can compute averages."""
        self._calls += 1

    def summary(self, label: str = "Fields Mesh"):
        """Emit one INFO log with totals and per-call averages, then reset."""
        if not get_perf_profiler_enabled():
            self.reset()
            return

        if self._calls == 0:
            self.reset()
            return
        n = self._calls
        total_ms = sum(self._totals.values()) * 1000
        lines = [f"[PERF] {label} - {n} call(s), {total_ms:.1f} ms total"]

        # Display stages in _STAGES order first, then any other timed stages
        dynamic_stages = sorted([s for s in self._totals if s not in self._STAGES and self._totals[s] > 0])
        all_ordered = self._STAGES + dynamic_stages

        for s in all_ordered:
            if s not in self._totals: continue
            t = self._totals[s] * 1000
            if t == 0 and s not in self._STAGES: continue
            indent = "    " if s in self._SUB_STAGES else "  "
            lines.append(f"{indent}{s:<18} {t:6.1f} ms  ({t/n:5.2f} ms/call)")
        fld_logger.info("\n".join(lines))
        self.reset()


# Module-level singleton - shared across all mesher calls for the same tool session
mesh_timer = MeshTimer()
