"""Canonical editorial shots remain independent of processing section boundaries."""
from app.services.analysis_support import time_to_seconds

PENDING_CONTINUITY = 'This shot reaches a processing section boundary; continuity will be checked with the next section.'


def resolve_shot_mode(job, requested='auto'):
    if requested not in {'auto', 'single_take', 'detect_cuts'}:
        raise ValueError('Unknown clip boundary mode')
    if requested != 'auto':
        return requested
    decoder = job.technical.get('viewing_proxy', {}).get('decoder')
    return 'single_take' if decoder in {'sony_f55_raw', 'arri_raw', 'red_raw', 'blackmagic_raw'} else 'detect_cuts'


def coalesce_take(shots, previous=None):
    """Single-take logging preserves timed observations and uncovered gaps.

    This is an explicit source policy, not a claim of verified model cuts.
    Adjacent fragments are joined; a failed/uncovered interval remains visible.
    """
    result = []
    for shot in shots:
        if not shot._dialogue_fragments and shot.transcript.strip():
            shot._dialogue_fragments = [{'start_time': shot.start_time, 'end_time': shot.end_time, 'text': shot.transcript}]
        left = result[-1] if result else previous
        if left and abs(time_to_seconds(left.end_time) - time_to_seconds(shot.start_time)) <= .001:
            shot.start_boundary = 'continuation'
            shot.visual_description = ' '.join(dict.fromkeys(v for v in
                (left.visual_description, shot.visual_description) if v))
            shot.camera_movement = ' / '.join(dict.fromkeys(v for v in
                (left.camera_movement, shot.camera_movement) if v))
            if result:
                dialogue = [*left._dialogue_fragments, *shot._dialogue_fragments]
                stitch_section(left, [shot])
                left._dialogue_fragments = dialogue
                continue
        result.append(shot)
    return result


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
