"""Effective, human-correctable cosplay metadata and same-costume filtering."""
from app.models.cosplay import CosplayIdentification, name_key, canonical_name


def effective_cosplay(shot, annotation=None):
    override = (annotation or {}).get('cosplay')
    records = override if override is not None else shot.get('cosplay', [])
    return [CosplayIdentification.model_validate(value).model_dump() for value in records]


def job_cosplay(job):
    return [record for scene in (job.flash_result or {}).get('scenes', [])
            for shot in scene.get('shots', [])
            for record in effective_cosplay(shot, job.shot_annotations.get(str(shot['shot_number'])))]


def matches_cosplay(records, character='', franchise='', identification=''):
    if not any((character, franchise, identification)):
        return True
    # All selected fields must describe ONE costume, never different people in a group.
    return any((not character or name_key(item['character']) == name_key(canonical_name(character)))
               and (not franchise or name_key(item['franchise']) == name_key(canonical_name(franchise, True)))
               and (not identification or item['identification'] == identification)
               for item in records)


def cosplay_facets(jobs):
    values = {'character': {}, 'franchise': {}}
    for job in jobs:
        for item in job_cosplay(job):
            for key in values:
                if item[key]:
                    values[key].setdefault(name_key(item[key]), item[key])
    return {key: sorted(items.values(), key=str.casefold) for key, items in values.items()}


def searchable_flash(job):
    # Corrections replace model costume suggestions in active search; raw output is retained.
    return {**(job.flash_result or {}), 'scenes': [
        {**scene, 'shots': [{**shot, 'cosplay': effective_cosplay(
            shot, job.shot_annotations.get(str(shot['shot_number'])))} for shot in scene.get('shots', [])]}
        for scene in (job.flash_result or {}).get('scenes', [])]}
