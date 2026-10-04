# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Allocation budgets for cage topology operations.

Every budget is checked *before* the operation allocates. Predicting the
result size from the input is the whole point: computing a refined topology
and then measuring it has already paid the cost the budget exists to avoid.
"""

# Hard cap on faces in a control cage. See CGE-002 -- the "shader cap" this
# was originally justified by does not appear to exist any more.
MAX_CAGE_FACES = 64

# Hard caps on single-object cage bake resolution and memory (C2S-020, C2S-021).
# These two must agree: MAX_BAKE_RESOLUTION**3 * 4 bytes <= MAX_BAKE_MEMORY_MB.
# 320**3 * 4 = 125 MB; 384**3 * 4 = 216 MB, which the memory cap would refuse.
MAX_BAKE_MEMORY_MB = 128
MAX_BAKE_RESOLUTION = 320


class CageBudgetError(ValueError):
    """A cage operation was refused because its result exceeds a budget."""


class CageTopologyError(ValueError):
    """A cage operation was refused because its input is not valid."""


def check_face_budget(face_count, operation):
    """Refuse *operation* if it would produce more than MAX_CAGE_FACES faces."""
    face_count = int(face_count)
    if face_count > MAX_CAGE_FACES:
        raise CageBudgetError(
            f"{operation} refused: the result would have {face_count} faces, "
            f"over the {MAX_CAGE_FACES}-face cage limit")
    return face_count


def check_bake_budget(resolution=None, total_voxels=None, operation="Cage bake"):
    """Refuse *operation* if requested resolution or memory exceeds limits."""
    if resolution is not None and resolution > MAX_BAKE_RESOLUTION:
        raise CageBudgetError(
            f"{operation} refused: requested resolution {resolution} exceeds "
            f"the {MAX_BAKE_RESOLUTION} limit"
        )
    if total_voxels is not None:
        mb = (total_voxels * 4) / (1024 * 1024)
        if mb > MAX_BAKE_MEMORY_MB:
            raise CageBudgetError(
                f"{operation} refused: requested grid requires {mb:.1f} MB, "
                f"over the {MAX_BAKE_MEMORY_MB} MB limit"
            )
    return True


def predict_subdivide_faces(topology):
    """Face count of one Catmull-Clark step, without running it.

    Catmull-Clark emits one quad per corner of every input face, so the
    result has sum(len(face)) faces regardless of input face degree.
    """
    return sum(len(face) for face in topology.face_vertex_lists())

