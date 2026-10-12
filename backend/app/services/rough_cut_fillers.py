"""Convert observed nonlexical filler intervals into reviewable, source-frame edits."""
import math
import re
from fractions import Fraction

from app.services.analysis_support import format_time, time_to_seconds
from app.services.source_timing import verified_rate

FILLER_WINDOW_SECONDS = 20
_FILLER = re.compile(r'(?:u+m+|u+h+|e+r+m*|a+h+|e+h+|h+m+)', re.I)


def cleanup_rate(technical):
    try:
        rate = Fraction(str(verified_rate(technical) or technical.get('frame_rate_fraction') or technical.get('frame_rate')))
    except (ValueError, ZeroDivisionError):
        rate = Fraction(0)
    if rate <= 0:
        raise ValueError('Filler removal requires a measured source frame rate.')
    return rate


def filler_windows(clips, duration):
    indexes = sorted({index for clip in clips for index in range(
        int(clip['start_seconds'] // FILLER_WINDOW_SECONDS), math.ceil(clip['end_seconds'] / FILLER_WINDOW_SECONDS))})
    return [(index * FILLER_WINDOW_SECONDS, min((index + 1) * FILLER_WINDOW_SECONDS, duration)) for index in indexes]


def observed_fillers(items, offset, read_start, read_end):
    accepted, skipped = [], 0
    for item in items:
        tokens = re.findall(r"[a-z]+", item.text.casefold())
        try:
            start = time_to_seconds(item.start_time) + offset
            end = time_to_seconds(item.end_time) + offset
        except (ValueError, TypeError):
            skipped += 1
            continue
        if (not tokens or not all(_FILLER.fullmatch(token) for token in tokens)
                or not read_start <= start < end <= read_end or not .06 <= end - start <= 2.5):
            skipped += 1
            continue
        accepted.append({'start_seconds': start, 'end_seconds': end, 'text': item.text})
    return accepted, skipped


def remove_observed_fillers(draft, observations, rate):
    """Split only at verified candidate intervals; the model never rewrites dialogue."""
    clips, children, removals = [], [], []
    skipped = 0
    frame_seconds = float(1 / rate)
    for clip in draft['clips']:
        first, last = clip['start_seconds'], clip['end_seconds']
        ranges = []
        for span in observations:
            start, end = span['start_seconds'], span['end_seconds']
            if end <= first or start >= last:
                continue
            # Snap inward so the removal does not expand into neighboring words.
            start = max(first, float(math.ceil(Fraction(str(start)) * rate) / rate))
            end = min(last, float(math.floor(Fraction(str(end)) * rate) / rate))
            if end - start < frame_seconds - 1e-6:
                skipped += 1
                continue
            ranges.append({**span, 'start_seconds': start, 'end_seconds': end})
        ranges.sort(key=lambda span: span['start_seconds'])
        merged = []
        for span in ranges:
            if merged and span['start_seconds'] <= merged[-1]['end_seconds'] + 1e-6:
                merged[-1]['end_seconds'] = max(merged[-1]['end_seconds'], span['end_seconds'])
                if span['text'] != merged[-1]['text']:
                    merged[-1]['text'] += ' / ' + span['text']
            else:
                merged.append(dict(span))
        retained, cursor, used = [], first, []
        for index, span in enumerate(merged):
            start, end = span['start_seconds'], span['end_seconds']
            next_edge = merged[index + 1]['start_seconds'] if index + 1 < len(merged) else last
            # Leave uncertain sub-frame remnants for review, rather than deleting speech.
            if (0 < start - cursor < frame_seconds - 1e-6 or 0 < next_edge - end < frame_seconds - 1e-6):
                skipped += 1
                continue
            if start > cursor:
                retained.append((cursor, start))
            cursor = end
            used.append(span)
        if not used:
            clips.append(clip)
            continue
        if cursor < last:
            retained.append((cursor, last))
        for part, (start, end) in enumerate(retained, 1):
            child = {**clip, 'moment_id': f'{clip["moment_id"]}:filler:{part}',
                     'original_moment_id': clip['moment_id'], 'part_index': part, 'part_count': len(retained),
                     'text_is_context': True, 'start_seconds': start, 'end_seconds': end,
                     'start_time': format_time(start), 'end_time': format_time(end),
                     'reason': (clip.get('reason', '') + ' Filler removed. Review this join; the text is context from the original excerpt.').strip()}
            clips.append(child)
            children.append(child)
        removals.extend({**span, 'start_time': format_time(span['start_seconds']),
                         'end_time': format_time(span['end_seconds'])} for span in used)
    if not clips:
        raise ValueError('No usable speech remains after filler removal.')
    if len(clips) > 2000:
        raise ValueError('Filler removal produced too many excerpts. Use a shorter recording or disable filler removal.')
    return clips, children, removals, skipped
