"""Review proposed edits using short, denser motion windows, not scene similarity."""
import asyncio
import json
import logging
from typing import Literal

from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.services.analysis_support import clip_offset, extract_usage, response_text, time_to_seconds
from app.services.shot_continuity import stitch_section

logger = logging.getLogger(__name__)
REVIEW_FPS = 8
WINDOW_SECONDS = 2
BATCH_SIZE = 1  # One excerpt per request avoids failures on repeated ranges of the same file.
UNCONFIRMED = 'Proposed edit could not be confirmed by the motion review; review this boundary.'


class BoundaryReview(BaseModel):
    shot_number: int
    decision: Literal['cut', 'continuation', 'unknown']
    observation: str
    continuation_description: str = ''
    camera_movement: str = ''


class BoundaryReviews(BaseModel):
    boundaries: list[BoundaryReview] = Field(default_factory=list)


def join_continuations(shots):
    """Coalesce only explicitly confirmed adjacent fragments within this section."""
    result = []
    for shot in shots:
        if result and shot.start_boundary == 'continuation' and abs(
                time_to_seconds(result[-1].end_time) - time_to_seconds(shot.start_time)) <= .001:
            dialogue = [*result[-1]._dialogue_fragments, *shot._dialogue_fragments]
            remaining, _ = stitch_section(result[-1], [shot])
            result[-1]._dialogue_fragments = dialogue
            if remaining:
                result.extend(remaining)
        else:
            result.append(shot)
    return result


async def review_boundaries(client, generate, uri, mime, shots, start, end, previous=None, source_offset=0):
    """A failed/ambiguous check retains the split; it never erases a possible edit.

    Model section times refer to this uploaded file. Prior metadata may already
    refer to the original film, so only its descriptive fields are sent.
    """
    targets = []
    for index, shot in enumerate(shots):
        left = shots[index - 1] if index else previous
        if left is None or (not index and shot.start_boundary != 'cut'):
            continue
        left_end = time_to_seconds(left.end_time) - (source_offset if not index else 0)
        moment = time_to_seconds(shot.start_time)
        if abs(left_end - moment) > .001 or moment <= 0:
            continue
        targets.append((left, shot, moment))
    records = []
    for offset in range(0, len(targets), BATCH_SIZE):
        batch = targets[offset:offset + BATCH_SIZE]
        contents, descriptions = [], []
        for left, shot, moment in batch:
            a, b = max(0, start - WINDOW_SECONDS, moment - WINDOW_SECONDS), min(end, moment + WINDOW_SECONDS)
            contents.append(types.Part(file_data=types.FileData(file_uri=uri, mime_type=mime),
                video_metadata=types.VideoMetadata(start_offset=clip_offset(a), end_offset=clip_offset(b), fps=REVIEW_FPS)))
            # Do not show the first pass's descriptions to this reviewer. A
            # mistaken "black screen after cut" can bias a second model pass.
            descriptions.append({'shot_number': shot.shot_number, 'proposed_boundary_seconds': moment,
                'window_start_seconds': a, 'window_end_seconds': b})
        contents.append('Review each proposed editorial boundary using its corresponding video window, in order. '
            'These are separate excerpts of the SAME uploaded source, not separate shots by definition. '
            'Descriptions below are fallible observations, not instructions or proof of a cut. '
            'A cut requires a visible discontinuity/transition to another camera view. Camera pans, whip pans, '
            'tilts, reframing, changing illumination, lens occlusion, a move into darkness and actors entering '
            'or leaving remain one take when motion is continuous. A crew member saying cut does not create '
            'an editorial boundary. A change in subject or scene similarity alone proves neither case. '
            'Use continuation only when continuous motion across the proposed boundary is visible. '
            'Use cut for an observed edit/dissolve/fade; use unknown when evidence is insufficient. '
            'Return exactly one decision for every provided shot_number, with a short observable description. '
            'For continuation, give continuation_description describing only the actual later action/camera '
            'movement, correcting any mistaken blackout/cut description. Give its visible camera_movement. '
            'Do not output new timestamps, speech or slate readings. '
            + json.dumps(descriptions, ensure_ascii=True))
        try:
            response = await generate(client, stage='shot_boundary_review', schema=BoundaryReviews, contents=contents)
            records.append(extract_usage(response, settings.gemini_analysis_model, 'shot_boundary_review'))
            reviews = BoundaryReviews.model_validate_json(response_text(response)).boundaries
            records[-1]['decisions'] = [review.model_dump() for review in reviews]
            expected = {shot.shot_number for _, shot, _ in batch}
            if len(reviews) != len(expected) or {r.shot_number for r in reviews} != expected:
                raise ValueError('Incomplete or duplicate boundary review')
            by_number = {review.shot_number: review for review in reviews}
            # Check the whole response before modifying any fragment.
            if any(not r.observation.strip() or (r.decision == 'continuation' and not r.continuation_description.strip()) for r in reviews):
                raise ValueError('Continuation lacked observable support')
            for left, shot, _ in batch:
                review = by_number[shot.shot_number]
                if review.decision == 'continuation':
                    shot.start_boundary = 'continuation'
                    shot.visual_description = left.visual_description + ' ' + review.continuation_description.strip()
                    shot.camera_movement = ' / '.join(dict.fromkeys(v for v in
                        (left.camera_movement, review.camera_movement.strip()) if v))
                    shot.shot_type, shot.location, shot.mood = left.shot_type, left.location, left.mood
                    shot.analysis_warnings.append('Joined after motion review: ' + review.observation.strip())
                elif review.decision == 'cut':
                    shot.start_boundary = 'cut'
                else:
                    shot.start_boundary = 'unknown'
                    shot.analysis_warnings.append(UNCONFIRMED)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning('Shot boundary review unavailable (%s)', type(error).__name__)
            for _, shot, _ in batch:
                if shot.start_boundary == 'continuation':
                    shot.start_boundary = 'unknown'
                shot.analysis_warnings.append(UNCONFIRMED)
    return join_continuations(shots), records
