"""One public mapping for the inspector, sidecars and native editor handoffs.

Processing section numbers are not production scenes. Slate readings are AI
observations, not corrections to the source's embedded camera/timecode facts.
"""
from app.services.library_export import job_data, portable_document, _public


FIELDS = (
    ('description', 'Description', 'Description', 'dc:description', 'Column.PropertyText.Description'),
    ('scene_description', 'Scene description', None, 'va:sceneDescription', 'VideoAnalyzerSceneDescription'),
    ('cosplay_description', 'Cosplay description', None, 'va:cosplayDescription', 'VideoAnalyzerCosplayDescription'),
    ('cosplay_characters', 'Cosplay characters', None, 'va:cosplayCharacters', 'VideoAnalyzerCosplayCharacters'),
    ('cosplay_franchises', 'Cosplay franchises', None, 'va:cosplayFranchises', 'VideoAnalyzerCosplayFranchises'),
    ('cosplay_variants', 'Costume variants', None, 'va:cosplayVariants', 'VideoAnalyzerCosplayVariants'),
    ('cosplay_status', 'Cosplay identification', None, 'va:cosplayIdentification', 'VideoAnalyzerCosplayIdentification'),
    ('keywords', 'Keywords', 'Keywords', 'dc:subject', 'VideoAnalyzerKeywords'),
    ('notes', 'Notes', 'Comments', 'xmpDM:logComment', 'Column.Intrinsic.LogNote'),
    ('scene', 'Scene', 'Scene', 'xmpDM:scene', 'Column.PropertyText.Scene'),
    ('shot', 'Slate shot', 'Shot', 'xmpDM:shotNumber', 'Column.PropertyText.Shot'),
    ('take', 'Take', 'Take', 'xmpDM:takeNumber', 'VideoAnalyzerTake'),
    ('camera_roll', 'Camera roll', 'Roll Card #', 'xmpDM:tapeName', 'Column.Intrinsic.TapeName'),
    ('camera', 'Camera', 'Camera #', 'xmpDM:cameraLabel', 'VideoAnalyzerCamera'),
    ('production_title', 'Production', 'Production Name', 'xmpDM:projectName', 'VideoAnalyzerProduction'),
    ('director', 'Director', 'Director', 'xmpDM:director', 'VideoAnalyzerDirector'),
    ('cinematographer', 'Cinematographer', 'DOP', 'xmpDM:directorPhotography', 'VideoAnalyzerCinematographer'),
    ('shot_type', 'Shot type', 'Shot Type', 'xmpDM:shotSize', 'VideoAnalyzerShotType'),
    ('camera_movement', 'Camera movement', 'Move', 'xmpDM:cameraMove', 'VideoAnalyzerCameraMovement'),
    ('location', 'Location', 'Location', 'xmpDM:shotLocation', 'VideoAnalyzerLocation'),
    ('colors', 'Colors', None, 'va:colors', 'VideoAnalyzerColors'),
    ('subjects', 'Subjects', None, 'va:subjects', 'VideoAnalyzerSubjects'),
    ('transcript', 'Dialogue', 'Dialog Notes', 'va:transcript', 'VideoAnalyzerDialogue'),
)


def _strings(value):
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _unique(values):
    result, seen = [], set()
    for value in values:
        if value.casefold() not in seen:
            seen.add(value.casefold())
            result.append(value)
    return result


def effective_editor_shot(shot, annotation):
    from app.services.cosplay import effective_cosplay
    return {**shot, 'cosplay': effective_cosplay(shot, annotation),
            'human_tags': annotation.get('tags', []), 'notes': annotation.get('notes', '')}


def _cosplay_fields(shots):
    blocks, keywords, records = [], [], []
    for shot in shots:
        costumes = shot.get('cosplay') or []
        lines = []
        for index, costume in enumerate(costumes, 1):
            status = costume.get('identification', 'unknown')
            label = {'confirmed': 'Confirmed by you', 'suggested': 'Suggested', 'unknown': 'Unknown'}.get(status, 'Unknown')
            character = costume.get('character') if status != 'unknown' else ''
            title = ' · '.join(filter(None, [character or 'Unknown character', costume.get('franchise'), costume.get('costume_variant')]))
            lines.append(f"Costume {index}: {title} [{label}]")
            for key, heading in [('visible_details', 'Visible details'), ('activity', 'Activity')]:
                if costume.get(key):
                    lines.append(f"{heading}: {costume[key]}")
            if costume.get('possible_characters'):
                lines.append('Possible characters (unconfirmed): ' + ', '.join(costume['possible_characters']))
            # Candidate guesses stay in the description, never character keywords.
            keywords.extend(filter(None, [character, costume.get('franchise'), costume.get('costume_variant')]))
            records.append({**costume, 'character': character or '', 'status_label': label})
        if lines:
            if len(shots) > 1:
                lines.insert(0, f"Clip {shot.get('shot_number', '')} ({shot.get('start_time', '')}–{shot.get('end_time', '')})")
            blocks.append('\n'.join(lines))
    values = {'cosplay_description': '\n\n'.join(blocks)}
    for key, source in [('cosplay_characters', 'character'), ('cosplay_franchises', 'franchise'),
                        ('cosplay_variants', 'costume_variant'), ('cosplay_status', 'status_label')]:
        values[key] = ' · '.join(_unique([record[source] for record in records if record.get(source)]))
    return values, (['Cosplay'] + keywords if records else [])


def map_fields(metadata, shots, transcript, summary=None, shot_scope=False, cosplay_primary=False):
    slates = [slate for shot in shots for slate in shot.get('slates') or []]
    values, origins, conflicts = {}, {}, []
    for key in ('scene', 'take', 'slate_shot', 'camera_roll', 'camera', 'production_title', 'director', 'cinematographer'):
        readings = _unique([text for slate in slates for text in _strings(slate.get(key))])
        target = 'shot' if key == 'slate_shot' else key
        if len(readings) == 1:
            values[target], origins[target] = readings[0], 'Slate reading · unreviewed'
        elif len(readings) > 1:
            conflicts.append(f'{key.replace("_", " ")}: multiple readings; retained per shot, not assigned to one clip field.')
    descriptions = (_strings(shots[0].get('visual_description')) if shot_scope and len(shots) == 1 else []) or _strings(metadata.get('notes')) or _strings((summary or {}).get('executive_summary'))
    if not descriptions and len(shots) == 1:
        descriptions = _strings(shots[0].get('visual_description'))
    values['description'] = '\n'.join(descriptions)
    origins['description'] = 'Your notes' if metadata.get('notes') and not shot_scope else 'Analysis'
    cosplay_values, cosplay_keywords = _cosplay_fields(shots)
    if cosplay_values['cosplay_description']:
        values.update(cosplay_values)
        origins.update({key: 'Cosplay · see identification status' for key in cosplay_values})
        scene_descriptions = _unique([text for shot in shots for text in _strings(shot.get('visual_description'))])
        values['scene_description'] = '\n\n'.join(scene_descriptions)
        if cosplay_primary:
            parts = [cosplay_values['cosplay_description']]
            if scene_descriptions:
                parts.append('Scene description: ' + values['scene_description'])
            if metadata.get('notes'):
                parts.append('Your notes: ' + metadata['notes'])
            values['description'] = '\n\n'.join(parts)
            origins['description'] = 'Cosplay + scene description'
    notes = _strings(metadata.get('notes'))
    if shot_scope:
        notes.extend(text for shot in shots for text in _strings(shot.get('notes')))
    values['notes'], origins['notes'] = '\n'.join(_unique(notes)), 'Your notes'
    keywords = _strings(metadata.get('tags')) + cosplay_keywords
    for shot in shots:
        for key in ('tags', 'human_tags', 'subjects', 'actions', 'dominant_colors', 'shot_type', 'camera_movement', 'mood', 'location'):
            keywords.extend(_strings(shot.get(key)))
    values['keywords'] = ', '.join(_unique(keywords))
    for key, source in (('shot_type', 'shot_type'), ('camera_movement', 'camera_movement'), ('location', 'location'), ('colors', 'dominant_colors'), ('subjects', 'subjects')):
        values[key] = ' · '.join(_unique([text for shot in shots for text in _strings(shot.get(source))]))
    values['transcript'] = '\n'.join(f"{cue.get('start_time', cue.get('start_seconds', ''))}–{cue.get('end_time', cue.get('end_seconds', ''))} "
                                     f"{cue.get('speaker', '') + ': ' if cue.get('speaker') else ''}{cue.get('text', '')}"
                                     for cue in transcript if isinstance(cue, dict) and _strings(cue.get('text')))
    fields = [{'key': key, 'label': label, 'value': values[key], 'origin': origins.get(key, 'Analysis'),
               'resolve': resolve, 'xmp': xmp, 'premiere': premiere}
              for key, label, resolve, xmp, premiere in FIELDS if values.get(key)]
    for field in fields:
        if field['key'] == 'keywords':
            field['xmp_value'] = _unique(keywords)
    return {'fields': fields, 'conflicts': conflicts}


def editor_metadata(job, include_record=False):
    from app.services.library_service import shot_views, public_transcript_segments
    data = job_data(job)
    # Enriched shots carry human annotations, section analysis and overlapping
    # dialogue; the complete document retains every other analysis field too.
    shots = list(shot_views(job))
    result = map_fields(data.get('metadata') or {}, shots, public_transcript_segments(job), data.get('summary'),
                        cosplay_primary=(data.get('analysis_config') or {}).get('mode') == 'cosplay')
    result.update(schema_version='1.0')
    if include_record:
        result['record'] = portable_document(data)
        result['record']['shots'] = _public(shots)
    # Search/UI URLs are app navigation, not editorial metadata.
    for shot in result.get('record', {}).get('shots', []):
        for key in ('preview_url', 'thumbnail_url', 'match_context', 'match_sources'):
            shot.pop(key, None)
    result['shot_fields'] = {str(shot['shot_number']): map_fields(
        shot.get('project_metadata') or {}, [shot], shot.get('transcript_segments') or [], shot_scope=True,
        cosplay_primary=(data.get('analysis_config') or {}).get('mode') == 'cosplay')['fields'] for shot in shots}
    return result
