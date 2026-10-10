"""Searchable public asset views, separate from private storage/provider fields."""
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from app.config import settings
from app.models.library import AssetMetadata
from app.services.library_export import parse_seconds, segment_times


_PRIVATE_SEARCH_KEYS = {
    'localpath', 'fileuri', 'fileid', 'apikey', 'googleapikey', 'credentials',
    'authorization', 'accesstoken', 'refreshtoken', 'token', 'secret', 'password',
    'warnings', 'analysiswarnings', 'internalwarnings', 'analysishistory',
    'analysisconfig', 'analysisprogress', 'progress', 'error', 'traceback',
    'costestimate', 'rawusage',
}
_COLOR_TERMS = {
    'red', 'tan', 'blue', 'gold', 'golden', 'rose', 'pink', 'orange', 'yellow',
    'black', 'white', 'gray', 'grey', 'green', 'purple', 'brown', 'cyan',
    'magenta', 'amber', 'beige', 'teal', 'maroon', 'violet', 'lime', 'navy',
    'aqua', 'aquamarine', 'azure', 'burgundy', 'bronze', 'charcoal', 'copper',
    'coral', 'cream', 'crimson', 'emerald', 'fuchsia', 'indigo', 'ivory',
    'khaki', 'lavender', 'lilac', 'mauve', 'mint', 'mustard', 'ochre', 'olive',
    'peach', 'plum', 'salmon', 'scarlet', 'silver', 'taupe', 'turquoise',
}


def _search_key_allowed(key):
    normalized = re.sub(r'[^a-z0-9]', '', str(key).casefold())
    return (not str(key).startswith('_') and normalized not in _PRIVATE_SEARCH_KEYS
            and not normalized.endswith(('apikey', 'password', 'secret', 'token')))


def public_search_metadata(value):
    """Keep descriptive fields while excluding provider state and internal diagnostics."""
    if isinstance(value, dict):
        return {key: public_search_metadata(item) for key, item in value.items() if _search_key_allowed(key)}
    if isinstance(value, (list, tuple)):
        return [public_search_metadata(item) for item in value]
    return value


def _source_duration(job):
    try:
        duration = parse_seconds(job.technical.get('duration_seconds'))
        return duration if duration > 0 else None
    except ValueError:
        return None


def _timed_transcript(job, duration):
    for cue in job.transcript or []:
        if not isinstance(cue, dict) or not isinstance(cue.get('text'), str) or not cue['text'].strip():
            continue
        try:
            start, end = segment_times(cue, duration)
        except ValueError:
            continue
        yield start, end, public_search_metadata(cue)


def public_transcript_segments(job):
    """Actual timed text only; descriptive audio notes are never transcript cues."""
    return [cue for _, _, cue in _timed_transcript(job, _source_duration(job))]


def _search_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if _search_key_allowed(key):
                yield from _search_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _search_values(item)
    elif isinstance(value, (str, int, float, bool)):
        yield str(value)


def _search_text(value):
    return ' '.join(_search_values(value)).casefold()


def _term_pattern(term):
    # Colors, shot types and review states must not match inside unrelated words.
    exact = len(term) <= 2 or term in _COLOR_TERMS or term in {'ecu', 'mcu', 'ots', 'pov', 'els', 'reviewed', 'unreviewed', 'needs_review'}
    return (r'(?<!\w)' + re.escape(term) + r'(?!\w)') if exact else re.escape(term)


def _term_count(text, term):
    return len(re.findall(_term_pattern(term), text))


def _match_snippet(value, terms):
    candidates = [' '.join(text.split()) for text in _search_values(value)]
    candidates = [text for text in candidates if any(_term_count(text.casefold(), term) for term in terms)]
    if not candidates:
        return ''
    text = max(candidates, key=lambda candidate: sum(bool(_term_count(candidate.casefold(), term)) for term in terms))
    if len(text) <= 130:
        return text
    first = min(match.start() for term in terms if (match := re.search(_term_pattern(term), text.casefold())))
    start = max(0, first - 30)
    end = min(len(text), start + 125)
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def seconds(value: str) -> float:
    try:
        total = 0.0
        for part in str(value).split(':'):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return 0.0


def thumbnail_url(job, number):
    thumb = Path(settings.upload_dir) / job.job_id / 'thumbs' / f'shot_{number}.jpg'
    try:
        stat = thumb.stat()
    except OSError:
        return None
    started = job.analysis_config.get('started_at')
    if started:
        try:
            # A stale file must never illustrate a different interval during reanalysis.
            if stat.st_mtime < datetime.fromisoformat(started).timestamp():
                return None
        except (ValueError, TypeError, OSError):
            return None
    # A regenerated frame must replace the browser's cached image within the same run.
    version = quote(f'{started or ""}:{stat.st_mtime_ns}', safe='')
    return f'/api/thumbnails/{job.job_id}/{number}?v={version}'


def asset_view(job, detail=False):
    metadata = AssetMetadata(**job.metadata).model_dump()
    if not metadata['title']:
        metadata['title'] = Path(job.filename).stem
    poster = Path(settings.upload_dir) / job.job_id / 'poster.jpg'
    view = {
        'job_id': job.job_id, 'filename': job.filename, 'status': job.status,
        'created_at': job.created_at.isoformat(), 'size_bytes': job.size_bytes,
        'mime_type': job.mime_type, 'youtube_url': job.youtube_url,
        'metadata': metadata, 'technical': job.technical,
        'shot_count': sum(len(scene.get('shots', [])) for scene in (job.flash_result or {}).get('scenes', [])),
        'summary': (job.summary or {}).get('executive_summary', ''),
        'thumbnail_url': f'/api/library/{job.job_id}/poster' if poster.is_file() else None,
        'preview_url': f'/api/library/{job.job_id}/media' if job.local_path else None,
        'progress': job.progress, 'error': job.error, 'warnings': job.warnings,
        'analysis_progress': job.analysis_progress,
    }
    if detail:
        view.update(flash=job.flash_result, deep=job.deep_results, video_summary=job.summary,
                    custom_result=job.custom_result, shot_matches=job.shot_matches,
                    analysis_config=job.analysis_config, shot_annotations=job.shot_annotations,
                    transcript=job.transcript, cost_estimate=job.cost_estimate,
                    rough_cut=job.rough_cut)
        view['analysis_history'] = job.analysis_history
        if job.flash_result:
            view['flash'] = {**job.flash_result, 'scenes': [
                {**scene, 'shots': [{**shot, 'thumbnail_url': thumbnail_url(job, shot.get('shot_number', 0))}
                                    for shot in scene.get('shots', [])]}
                for scene in job.flash_result.get('scenes', [])
            ]}
    return view


def search_score(query: str, data) -> int:
    """Explainable case-insensitive keyword matching; deliberately not called semantic search."""
    terms = re.findall(r'\w+', query.casefold())
    text = _search_text(data)
    if not all(_term_count(text, term) for term in terms):
        return -1
    return sum(min(_term_count(text, term), 20) for term in terms)


def match_shot(query: str, shot: dict) -> tuple[int, dict]:
    """AND terms across one shot and its parents, with explicit scope attribution."""
    context_keys = {'job_id', 'filename', 'scene_number', 'scene_title', 'start_seconds',
                    'end_seconds', 'thumbnail_url', 'preview_url', 'section_context',
                    'section_analysis', 'video_summary', 'project_metadata',
                    'custom_analysis', 'transcript_segments',
                    'match_sources', 'match_context'}
    scopes = [
        ('Shot metadata', {key: value for key, value in shot.items() if key not in context_keys}),
        ('Transcript', shot.get('transcript_segments')),
        ('Section analysis', [shot.get('section_context'), shot.get('section_analysis')]),
        ('Video summary', shot.get('video_summary')),
        ('Custom analysis', shot.get('custom_analysis')),
        ('Project metadata', [shot.get('filename'), shot.get('project_metadata')]),
    ]
    terms = re.findall(r'\w+', query.casefold())
    texts = [(label, value, _search_text(value)) for label, value in scopes]
    combined = ' '.join(text for _, _, text in texts)
    if not all(_term_count(combined, term) for term in terms):
        return -1, {}
    contributors = [(label, value) for label, value, text in texts if any(_term_count(text, term) for term in terms)]
    score = sum(min(_term_count(text, term), 20) * weight
                for (_, _, text), weight in zip(texts, (4, 4, 2, 1, 1, 1)) for term in terms)
    return score, {
        'match_sources': [label for label, _ in contributors],
        'match_context': '; '.join(f'{label}: {_match_snippet(value, terms)}' for label, value in contributors),
    }


def shot_views(job):
    metadata = AssetMetadata(**job.metadata).model_dump()
    if not metadata['title']:
        metadata['title'] = Path(job.filename).stem
    summary = public_search_metadata(job.summary) if job.summary else None
    custom_analysis = public_search_metadata(job.custom_result) if job.custom_result else None
    duration = _source_duration(job)
    transcript = list(_timed_transcript(job, duration))
    for scene in (job.flash_result or {}).get('scenes', []):
        section_context = {key: scene.get(key, '') for key in
                           ('scene_number', 'scene_title', 'scene_description', 'start_time', 'end_time')}
        section_analysis = next((public_search_metadata(detail) for detail in reversed(job.deep_results)
                                 if scene.get('scene_number') is not None
                                 and detail.get('scene_number') == scene.get('scene_number')), None)
        for shot in scene.get('shots', []):
            number = shot.get('shot_number', 0)
            annotation = job.shot_annotations.get(str(number), {})
            try:
                start, end = segment_times(shot, duration)
                # Keep complete overlapping cues on the source timeline. A cue that
                # ends exactly at this shot's start belongs to the preceding shot.
                transcript_segments = [cue for a, b, cue in transcript if a < end and b > start]
            except ValueError:
                transcript_segments = []
            yield {
                **public_search_metadata(shot), 'job_id': job.job_id, 'filename': job.filename,
                'scene_number': scene.get('scene_number'), 'scene_title': scene.get('scene_title', ''),
                'section_context': section_context, 'section_analysis': section_analysis,
                'video_summary': summary, 'project_metadata': metadata,
                'custom_analysis': custom_analysis, 'transcript_segments': transcript_segments,
                'start_seconds': seconds(shot.get('start_time', '0')),
                'end_seconds': seconds(shot.get('end_time', '0')),
                'tags': list(dict.fromkeys(shot.get('tags', []) + annotation.get('tags', []))),
                'human_tags': annotation.get('tags', []), 'notes': annotation.get('notes', ''),
                'review_status': annotation.get('review_status', 'unreviewed'),
                'thumbnail_url': thumbnail_url(job, number),
                'preview_url': f'/api/library/{job.job_id}/media' if job.local_path else None,
            }
