# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""
core/render/render_profiler.py

Per-pass timing for the scene voxel renderer. Split out of
fld_scene_voxel_renderer.py; the stage list and the reasoning behind each column
are in the class docstring and the comments on `_STAGES`.
"""
import time

from freecad.fields.core import fld_logger


class RenderProfiler:
    """Accumulates per-pass render timings and periodically emits an [PERF]
    summary log, then resets. Gated on get_perf_profiler_enabled() — when off,
    stages are still cheaply tallied (a couple of perf_counter() calls) but
    never logged, matching the mesh_timer pattern in core/mesh/fld_mesher.py.

    Each stage tracks its own call count instead of sharing one frame counter:
    Pass 1-3 only run on 'expensive' frames (throttled to 30fps at rest) while
    Pass 4 (composite) runs every callback, so a shared divisor would
    understate the true per-call cost of the throttled passes.
    """
    # voxel_bake is split by KIND, not merged: a full bake and a region rebake
    # are the same call costing wildly different amounts, and which one a drag
    # gets is the open question (SceneVolume.ensure remaps the world->voxel
    # mapping whenever the scene box grows, and bake() then refuses a region).
    # One averaged column cannot answer it; two counts and two averages can.
    # `inherited_queue` is first because it is not our cost: it is the host's
    # queued drawing, drained at a known point so that no pass below inherits
    # it. Before it existed, Pass 4 -- the only unthrottled pass, and so usually
    # the first to glFinish -- was billed for all of it (LE-001).
    _STAGES = [
        "inherited_queue",
        "callback_total", "ssbo_pack",
        "tex3d_upload", "voxel_bake", "voxel_bake_full", "voxel_bake_region",
        "pass1_dispatch", "pass2_ssao", "pass3_blur", "pass4_composite",
        "rebuild_total", "rebuild_compile", "rebuild_shadergen",
    ]

    def __init__(self):
        self.reset()
        self.session_reset()

    def reset(self):
        self._totals = {}
        self._counts = {}
        self._frame_events = 0
        self._flush_t = time.perf_counter()

    def session_reset(self):
        """Start a new named span (a tool drag). Independent of the periodic
        flush above, which resets itself every couple of seconds and so cannot
        answer 'where did this 30-second drag go?'."""
        self._s_totals = {}
        self._s_counts = {}
        self._s_frames = 0

    def session_snapshot(self):
        """(totals, counts, frames) accumulated since the last session_reset."""
        return dict(self._s_totals), dict(self._s_counts), self._s_frames

    def add(self, stage, seconds):
        self._totals[stage] = self._totals.get(stage, 0.0) + seconds
        self._counts[stage] = self._counts.get(stage, 0) + 1
        self._s_totals[stage] = self._s_totals.get(stage, 0.0) + seconds
        self._s_counts[stage] = self._s_counts.get(stage, 0) + 1

    def note_frame(self):
        self._frame_events += 1
        self._s_frames += 1

    def maybe_flush(self, min_interval=2.0):
        """Emit + reset roughly every `min_interval` seconds (not every N
        frames) so a slow render reports quickly instead of waiting for a
        frame count that a heavy scene may take a long time to reach."""
        from freecad.fields.core.fld_settings import get_perf_profiler_enabled
        if not get_perf_profiler_enabled():
            self.reset()
            return
        now = time.perf_counter()
        elapsed = now - self._flush_t
        if elapsed < min_interval or self._frame_events == 0:
            return
        fps = self._frame_events / elapsed if elapsed > 0 else 0.0
        lines = [f"[PERF] SceneVoxel — {self._frame_events} callback(s) over {elapsed:.1f}s ({fps:.1f} fps)"]
        dynamic = sorted(s for s in self._totals if s not in self._STAGES)
        for s in self._STAGES + dynamic:
            n = self._counts.get(s, 0)
            if n == 0:
                continue
            t = self._totals[s] * 1000
            lines.append(f"  {s:<18} {t:7.1f} ms  ({t/n:5.2f} ms/call, {n} calls)")
        fld_logger.info("\n".join(lines))
        self.reset()
