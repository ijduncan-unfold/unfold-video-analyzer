"""Progressive API contracts using only the isolated test database and mocked AI."""
import asyncio
import copy
import csv
import io
import os
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest

from app.models.library import AnalyzeRequest
from app.config import settings
from app.services import job_runner, job_store
from app.services.library_export import NS


PROGRESS = {
    'stage': 'indexing', 'completed_sections': 1, 'completed_shots': 1,
    'processed_seconds': 30, 'total_sections': 3, 'total_seconds': 90,
    'failed_sections': 0, 'updated_at': '2026-09-09T12:00:00+00:00',
}


def partial_asset(asset, status='analyzing'):
    partial = copy.deepcopy(asset.flash_result)
    partial['scenes'][0]['shots'] = partial['scenes'][0]['shots'][:1]
    partial['scenes'][0]['end_time'] = '00:30.000'
    partial.update(total_shots=1, total_scenes=1, total_duration='01:30.000')
    return job_store.update_job(
        asset.job_id, status=status, flash_result=partial, deep_results=[], summary=None,
        technical={**asset.technical, 'duration_seconds': 90},
        analysis_progress=dict(PROGRESS), progress='Indexed section 1 of 3',
    )


def assert_counts_preserved(progress):
    for key in ('completed_sections', 'completed_shots', 'processed_seconds', 'total_sections', 'total_seconds'):
        assert progress[key] == PROGRESS[key], key


def test_partial_results_are_available_in_detail_asset_search_and_shot_search(client, asset):
    partial_asset(asset)
    detail = client.get(f'/api/library/{asset.job_id}')
    assert detail.status_code == 200
    result = detail.json()
    assert result['status'] == 'analyzing'
    assert result['shot_count'] == 1
    assert len(result['flash']['scenes'][0]['shots']) == 1
    assert result['analysis_progress'] == PROGRESS
    assert result['video_summary'] is None
    assert client.get('/api/library?q=sailboat').json()['total'] == 1
    shots = client.get('/api/library/search/shots?q=sailboat').json()
    assert shots['total'] == 1 and shots['shots'][0]['end_seconds'] == 3.5
    assert client.get('/api/library/search/shots?q=rope').json()['total'] == 0
    assert result['flash']['total_shots'] == 1


def test_later_sections_appear_without_losing_edits_to_available_shots(client, asset):
    partial_asset(asset)
    edited = client.patch(f'/api/library/{asset.job_id}/shots/1', json={
        'notes': 'Keep this opening', 'tags': ['select'], 'review_status': 'reviewed',
    })
    assert edited.status_code == 200
    latest = job_store.get_job(asset.job_id)
    following = copy.deepcopy(latest.flash_result)
    shot = copy.deepcopy(asset.flash_result['scenes'][0]['shots'][1])
    shot.update(start_time='00:31.000', end_time='00:34.000')
    following['scenes'].append({
        'scene_number': 2, 'scene_title': 'Rope detail', 'start_time': '00:30.000',
        'end_time': '01:00.000', 'shots': [shot],
    })
    following.update(total_shots=2, total_scenes=2)
    job_store.update_job(asset.job_id, flash_result=following, analysis_progress={
        **PROGRESS, 'completed_sections': 2, 'completed_shots': 2, 'processed_seconds': 60,
    })
    detail = client.get(f'/api/library/{asset.job_id}').json()
    assert detail['shot_count'] == 2 and detail['status'] == 'analyzing'
    assert detail['shot_annotations']['1']['notes'] == 'Keep this opening'
    found = client.get('/api/library/search/shots?q=rope').json()
    assert found['total'] == 1 and found['shots'][0]['start_seconds'] == 31
    assert client.get('/api/library/search/shots?tag=select&review_status=reviewed').json()['total'] == 1


def test_shot_edit_with_current_interval_saves_during_analysis(client, asset):
    partial_asset(asset)
    response = client.patch(f'/api/library/{asset.job_id}/shots/1', params={
        'expected_start_time': '00:00.000', 'expected_end_time': '00:03.500',
    }, json={'notes': 'Approved opening', 'tags': ['select']})
    assert response.status_code == 200
    saved = job_store.get_job(asset.job_id).shot_annotations['1']
    assert saved['notes'] == 'Approved opening' and saved['tags'] == ['select']


@pytest.mark.parametrize('expected', [
    {'expected_start_time': '00:00.000', 'expected_end_time': '00:03.500'},
    {'expected_start_time': '00:00.000'},
    {'expected_end_time': '00:03.500'},
])
def test_stale_shot_edit_cannot_attach_to_recycled_number(client, asset, expected):
    latest = partial_asset(asset)
    replacement = copy.deepcopy(latest.flash_result)
    replacement['scenes'][0]['shots'][0].update(start_time='00:05.000', end_time='00:09.000')
    current_annotations = {'1': {'notes': 'Current shot note', 'tags': ['current']}}
    job_store.update_job(asset.job_id, flash_result=replacement, shot_annotations=current_annotations)
    response = client.patch(f'/api/library/{asset.job_id}/shots/1', params=expected,
                            json={'notes': 'Stale opening note', 'tags': ['wrong']})
    assert response.status_code == 409
    assert 'Reload the shot' in response.json()['detail']
    assert job_store.get_job(asset.job_id).shot_annotations == current_annotations
    # Legacy clients that do not supply an interval retain their existing behavior.
    unguarded = client.patch(f'/api/library/{asset.job_id}/shots/1', json={'notes': 'Legacy edit'})
    assert unguarded.status_code == 200
    assert job_store.get_job(asset.job_id).shot_annotations['1']['notes'] == 'Legacy edit'


def test_guarded_edit_of_disappeared_shot_is_conflict(client, asset):
    partial_asset(asset)  # The second shot is absent from this partial snapshot.
    response = client.patch(f'/api/library/{asset.job_id}/shots/2', params={
        'expected_start_time': '00:03.500', 'expected_end_time': '00:08.000',
    }, json={'notes': 'Stale rope note'})
    assert response.status_code == 409
    assert job_store.get_job(asset.job_id).shot_annotations == {}
    assert client.patch(f'/api/library/{asset.job_id}/shots/2', json={'notes': 'Legacy edit'}).status_code == 404


@pytest.mark.parametrize('status', ['analyzing', 'error'])
def test_partial_exports_include_only_persisted_shots_and_honest_progress(client, asset, status):
    partial_asset(asset, status)
    if status == 'error':
        job_store.update_job(asset.job_id, analysis_progress={**PROGRESS, 'stage': 'error'})
    base = f'/api/library/{asset.job_id}/export'
    response = client.get(base, params={'format': 'json'})
    assert response.status_code == 200
    exported = response.json()
    assert exported['provenance']['status'] == status
    assert_counts_preserved(exported['provenance']['analysis_progress'])
    assert exported['technical']['duration_seconds'] == 90
    assert exported['analysis']['flash_result']['total_shots'] == 1
    for private in ('private-provider-id', 'private-provider-uri', 'local_path'):
        assert private not in response.text
    csv_response = client.get(base, params={'format': 'csv'})
    assert csv_response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
    assert len(rows) == 1 and rows[0]['Shot'] == '1'
    assert rows[0]['Source Duration Seconds'] == '90'
    xmp = client.get(base, params={'format': 'xmp'})
    assert xmp.status_code == 200
    markers = ET.fromstring(xmp.content).findall('.//xmpDM:markers/rdf:Seq/rdf:li', NS)
    assert len(markers) == 1
    assert client.get(base, params={'format': 'srt'}).status_code == 422


def test_actual_partial_transcript_can_export_before_analysis_completes(client, asset):
    partial_asset(asset)
    job_store.update_job(asset.job_id, transcript=[{
        'start_time': '00:01.000', 'end_time': '00:03.000',
        'text': 'Actual words from the first section.', 'timestamp_accuracy': 'approximate',
    }])
    response = client.get(f'/api/library/{asset.job_id}/export?format=srt')
    assert response.status_code == 200
    assert '00:00:01,000 --> 00:00:03,000' in response.text
    assert 'Actual words from the first section.' in response.text
    assert 'Waves' not in response.text


@pytest.mark.parametrize('endpoint', ['/api/library', '/api/library/search/shots'])
def test_active_job_count_ignores_query_filters_and_pagination(client, asset, endpoint):
    partial_asset(asset)
    for job_id, status in [('queued-other', 'queued'), ('processing-other', 'processing'), ('idle-other', 'complete')]:
        job_store.create_job(replace(asset, job_id=job_id, status=status))
    response = client.get(endpoint, params={
        'q': 'willnotmatchanything', 'project': 'another-project', 'collection': 'another-collection',
        'offset': 100, 'limit': 1,
    })
    assert response.status_code == 200
    data = response.json()
    assert data['total'] == 0
    assert data['active_jobs'] == 3


def test_progress_survives_storage_reload_and_restart_recovery(client, asset):
    partial = partial_asset(asset)
    # get_job reads a fresh record rather than sharing an in-memory object.
    restored = job_store.get_job(asset.job_id)
    assert restored is not partial and restored.analysis_progress == PROGRESS
    job_store.recover_interrupted_jobs()
    after = job_store.get_job(asset.job_id)
    assert after.status == 'error'
    assert after.flash_result == partial.flash_result
    assert_counts_preserved(after.analysis_progress)
    assert after.analysis_progress['stage'] == 'interrupted'
    detail = client.get(f'/api/library/{asset.job_id}').json()
    assert_counts_preserved(detail['analysis_progress'])
    assert detail['shot_count'] == 1
    assert client.get('/api/library').json()['active_jobs'] == 0


def test_legacy_processing_status_blocks_claims_and_recovers_partial_results(client, asset):
    partial = partial_asset(asset, status='processing')
    assert job_store.claim_analysis(asset.job_id) is False
    assert job_store.claim_deletion(asset.job_id) is False
    before = job_store.get_job(asset.job_id)
    assert before.status == 'processing' and before.analysis_progress == PROGRESS
    assert client.get('/api/library').json()['active_jobs'] == 1
    job_store.recover_interrupted_jobs()
    after = job_store.get_job(asset.job_id)
    assert after.status == 'error'
    assert after.flash_result == partial.flash_result
    assert after.analysis_progress['stage'] == 'interrupted'
    assert_counts_preserved(after.analysis_progress)
    assert client.get('/api/library').json()['active_jobs'] == 0


def test_thumbnails_hide_prior_run_files_and_version_fresh_evidence(client, asset):
    partial_asset(asset)
    thumb = Path(settings.upload_dir) / asset.job_id / 'thumbs' / 'shot_1.jpg'
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b'prior run frame')
    started = '2026-09-09T12:00:00+00:00'
    epoch = datetime.fromisoformat(started).timestamp()
    os.utime(thumb, (epoch - 60, epoch - 60))
    job_store.update_job(asset.job_id, analysis_config={'started_at': started})

    def public_thumbnail_urls():
        detail = client.get(f'/api/library/{asset.job_id}').json()
        search = client.get('/api/library/search/shots?q=sailboat').json()
        return (detail['flash']['scenes'][0]['shots'][0]['thumbnail_url'],
                search['shots'][0]['thumbnail_url'])

    assert public_thumbnail_urls() == (None, None)
    thumb.write_bytes(b'current run frame')
    os.utime(thumb, (epoch + 1, epoch + 1))
    url = f'/api/thumbnails/{asset.job_id}/1?v={quote(f"{started}:{thumb.stat().st_mtime_ns}", safe="")}'
    assert public_thumbnail_urls() == (url, url)
    response = client.get(url)
    assert response.status_code == 200 and response.content == b'current run frame'
    # View enrichment must not persist an old URL into the underlying model result.
    assert 'thumbnail_url' not in job_store.get_job(asset.job_id).flash_result['scenes'][0]['shots'][0]

    thumb.write_bytes(b'regenerated midpoint frame')
    os.utime(thumb, (epoch + 2, epoch + 2))
    refreshed = public_thumbnail_urls()
    assert refreshed[0] == refreshed[1] and refreshed[0] != url
    assert client.get(refreshed[0]).content == b'regenerated midpoint frame'

    later = '2026-09-09T12:05:00+00:00'
    later_epoch = datetime.fromisoformat(later).timestamp()
    job_store.update_job(asset.job_id, analysis_config={'started_at': later})
    assert public_thumbnail_urls() == (None, None)
    thumb.write_bytes(b'next run frame')
    os.utime(thumb, (later_epoch + 1, later_epoch + 1))
    later_url = f'/api/thumbnails/{asset.job_id}/1?v={quote(f"{later}:{thumb.stat().st_mtime_ns}", safe="")}'
    assert later_url != url
    assert public_thumbnail_urls() == (later_url, later_url)
    assert client.get(later_url).content == b'next run frame'


def test_runner_error_keeps_successful_partial_counters_and_results(client, asset, monkeypatch):
    partial_asset(asset)
    monkeypatch.setattr(job_runner, 'ensure_remote_file', AsyncMock())

    async def fail_after_section(job, *args, **kwargs):
        job_store.update_job(job.job_id, analysis_progress=dict(PROGRESS))
        yield {'event': 'pass_start', 'data': '{"name":"Section indexing"}'}
        raise RuntimeError('mocked stage failure')

    monkeypatch.setattr(job_runner, 'run_analysis', fail_after_section)
    asyncio.run(job_runner._run(asset.job_id, AnalyzeRequest(), None))
    result = job_store.get_job(asset.job_id)
    assert result.status == 'error'
    assert result.analysis_progress['stage'] == 'error'
    assert_counts_preserved(result.analysis_progress)
    assert result.flash_result['total_shots'] == 1
    assert client.get('/api/library/search/shots?q=sailboat').json()['total'] == 1


def test_cancel_after_section_keeps_successful_partial_counters(client, asset, monkeypatch):
    partial_asset(asset)
    monkeypatch.setattr(job_runner, 'ensure_remote_file', AsyncMock())

    async def scenario():
        ready = asyncio.Event()
        wait_forever = asyncio.Event()

        async def wait_after_section(job, *args, **kwargs):
            job_store.update_job(job.job_id, analysis_progress=dict(PROGRESS))
            ready.set()
            yield {'event': 'pass_start', 'data': '{"name":"Section indexing"}'}
            await wait_forever.wait()

        monkeypatch.setattr(job_runner, 'run_analysis', wait_after_section)
        job_runner.start_job(asset.job_id, AnalyzeRequest(), None)
        task = job_runner._tasks[asset.job_id]
        await asyncio.wait_for(ready.wait(), timeout=1)
        assert job_runner.cancel_job(asset.job_id)
        await asyncio.gather(task, return_exceptions=True)
        assert asset.job_id not in job_runner._tasks

    asyncio.run(scenario())
    result = job_store.get_job(asset.job_id)
    assert result.status == 'error'
    assert result.analysis_progress['stage'] == 'cancelled'
    assert_counts_preserved(result.analysis_progress)
    assert result.flash_result['total_shots'] == 1
