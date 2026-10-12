"""Library facets from recorded slate fields, without inventing missing metadata."""

SLATE_FIELDS = {
    'production': 'production_title', 'scene': 'scene', 'take': 'take',
    'camera_roll': 'camera_roll', 'cinematographer': 'cinematographer',
}


def shots(job):
    return [shot for section in (job.flash_result or {}).get('scenes', [])
            for shot in section.get('shots', [])]


def clean(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def slates(items):
    return [slate for shot in items for slate in shot.get('slates', []) if isinstance(slate, dict)]


def matches_metadata(job, filters, shot=None):
    selected = {key: clean(value).casefold() for key, value in filters.items() if clean(value)}
    if not selected:
        return True
    # A manually assigned production applies to every clip in the source.
    if selected.get('production') == clean(job.metadata.get('project')).casefold():
        selected.pop('production')
    if not selected:
        return True
    # Scene and take must occur together on one board, not on different takes.
    return any(all(clean(slate.get(SLATE_FIELDS[key])).casefold() == value
                   for key, value in selected.items())
               for slate in slates([shot] if shot is not None else shots(job)))


def library_facets(jobs):
    values = {key: [] for key in SLATE_FIELDS}
    values['tags'] = []
    for job in jobs:
        values['production'].append(job.metadata.get('project'))
        values['tags'].extend(job.metadata.get('tags', []))
        items = shots(job)
        for shot in items:
            values['tags'].extend(shot.get('tags', []))
            values['tags'].extend(job.shot_annotations.get(str(shot.get('shot_number')), {}).get('tags', []))
        for slate in slates(items):
            for key, field in SLATE_FIELDS.items():
                values[key].append(slate.get(field))
    result = {}
    for key, terms in values.items():
        unique = {}
        for term in terms:
            if text := clean(term):
                unique.setdefault(text.casefold(), text)
        result[key] = sorted(unique.values(), key=str.casefold)
    return result


def matches_tag(job, tag):
    if not tag:
        return True
    terms = list(job.metadata.get('tags', []))
    for shot in shots(job):
        terms.extend(shot.get('tags', []))
        terms.extend(job.shot_annotations.get(str(shot.get('shot_number')), {}).get('tags', []))
    return clean(tag).casefold() in {clean(term).casefold() for term in terms}
