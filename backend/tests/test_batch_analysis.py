"""Library bulk actions use the existing durable claims and bounded workers."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.library import AnalyzeRequest
from app.services import job_runner, job_store


def create_clip(job_id):
    return job_store.create_job(job_store.Job(job_id=job_id, filename=f'{job_id}.mov',
        file_id='test-provider-id', file_uri='test-provider-uri', mime_type='video/quicktime',
        size_bytes=100, local_path='', technical={'duration_seconds': 8}))


@pytest.mark.parametrize('mode', ['flash_only', 'flash_pro'])
def test_queue_multiple_projects_and_reject_duplicate_without_resetting_results(client, asset, monkeypatch, mode):
    from app.routers import library
    submitted = []
    monkeypatch.setattr(library, 'start_job', lambda job_id, request, key: submitted.append((job_id, request.mode)))
    create_clip('second')
    headers = {'X-API-Key': 'test-key'}
    for job_id in [asset.job_id, 'second']:
        response = client.post(f'/api/library/{job_id}/analyze', json={'mode': mode}, headers=headers)
        assert response.status_code == 202
    assert client.post(f'/api/library/{asset.job_id}/analyze', json={'mode': mode}, headers=headers).status_code == 409
    assert submitted == [(asset.job_id, mode), ('second', mode)]
    views = {item['job_id']: item for item in client.get('/api/library').json()['assets']}
    assert views[asset.job_id]['status'] == views['second']['status'] == 'queued'
    assert views[asset.job_id]['analysis_progress']['stage'] == 'queued'
    assert job_store.get_job(asset.job_id).flash_result == asset.flash_result


def test_workers_run_two_in_parallel_and_start_next_waiting_clip(client, monkeypatch):
    monkeypatch.setattr(settings, 'max_concurrent_analyses', 2)
    monkeypatch.setattr(job_runner, '_gate', None)
    monkeypatch.setattr(job_runner, '_tasks', {})
    monkeypatch.setattr(job_runner, 'ensure_remote_file', AsyncMock())
    for job_id in ['a', 'b', 'c']:
        create_clip(job_id)
        assert job_store.claim_analysis(job_id)

    async def scenario():
        started = {job_id: asyncio.Event() for job_id in ['a', 'b', 'c']}
        release = {job_id: asyncio.Event() for job_id in ['a', 'b', 'c']}
        active = set()
        peak = 0

        async def analyze(job, fps, mode, **kwargs):
            nonlocal peak
            assert mode == 'flash_pro'
            active.add(job.job_id)
            peak = max(peak, len(active))
            started[job.job_id].set()
            await release[job.job_id].wait()
            active.remove(job.job_id)
            job_store.update_job(job.job_id, status='complete')
            yield {'event': 'analysis_complete', 'data': '{}'}

        monkeypatch.setattr(job_runner, 'run_analysis', analyze)
        for job_id in ['a', 'b', 'c']:
            job_runner.start_job(job_id, AnalyzeRequest(mode='flash_pro'), 'test-key')
        tasks = list(job_runner._tasks.values())
        try:
            await asyncio.wait_for(asyncio.gather(started['a'].wait(), started['b'].wait()), 2)
            assert active == {'a', 'b'}
            assert job_store.get_job('c').status == 'queued'
            assert not started['c'].is_set()
            release['a'].set()
            await asyncio.wait_for(started['c'].wait(), 2)
            assert active == {'b', 'c'}
        finally:
            for event in release.values():
                event.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert peak == 2
        assert all(job_store.get_job(job_id).status == 'complete' for job_id in ['a', 'b', 'c'])

    asyncio.run(scenario())
