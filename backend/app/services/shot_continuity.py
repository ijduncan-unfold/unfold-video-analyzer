"""Canonical editorial shots remain independent of processing section boundaries."""
from app.services.analysis_support import time_to_seconds

PENDING_CONTINUITY = 'This shot reaches a processing section boundary; continuity will be checked with the next section.'


def previous_at_boundary(scenes, start):
    for scene in reversed(scenes):
        if scene.shots:
            shot = scene.shots[-1]
            return shot if abs(time_to_seconds(shot.end_time) - start) <= .001 else None
    return None


def stitch_section(previous, shots):
    """Merge only an explicit continuation with adjacent, fully analyzed coverage.

    Preserve the original shot number and timed evidence; the model supplies a
    single updated description after actually viewing the boundary context.
    """
    if not previous or not shots:
        return shots, shots
    first = shots[0]
    adjacent = abs(time_to_seconds(previous.end_time) - time_to_seconds(first.start_time)) <= .001
    if not adjacent or first.start_boundary != 'continuation':
        if adjacent and first.start_boundary == 'cut':
            previous.analysis_warnings = [w for w in previous.analysis_warnings if w != PENDING_CONTINUITY]
        return shots, shots
    first.shot_number = previous.shot_number
    previous.end_time = first.end_time
    previous.visual_description = first.visual_description
    previous.shot_type = first.shot_type
    previous.camera_movement = first.camera_movement
    previous.audio_notes = '\n'.join(dict.fromkeys(text for text in (previous.audio_notes, first.audio_notes) if text))
    previous.transcript = ' '.join(text for text in (previous.transcript, first.transcript) if text)
    previous.location = first.location or previous.location
    previous.mood = first.mood or previous.mood
    previous.evidence.extend(first.evidence)
    previous.slates.extend(first.slates)
    for key in ('subjects', 'actions', 'tags', 'dominant_colors', 'visible_text', 'logos', 'section_numbers'):
        setattr(previous, key, list(dict.fromkeys([*getattr(previous, key), *getattr(first, key)])))
    previous.analysis_warnings = list(dict.fromkeys(
        [w for w in previous.analysis_warnings if w != PENDING_CONTINUITY] + first.analysis_warnings))
    if previous.confidence is not None and first.confidence is not None:
        previous.confidence = min(previous.confidence, first.confidence)
    for offset, shot in enumerate(shots[1:], previous.shot_number + 1):
        shot.shot_number = offset
    return shots[1:], [previous, *shots[1:]]


def section_has_shots(scene, scenes):
    start, end = time_to_seconds(scene.start_time), time_to_seconds(scene.end_time)
    return any(time_to_seconds(shot.start_time) < end and time_to_seconds(shot.end_time) > start
               for owner in scenes for shot in owner.shots)
