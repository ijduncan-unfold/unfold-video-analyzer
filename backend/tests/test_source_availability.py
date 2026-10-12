from app.services import source_availability
from app.services.job_store import update_job


def test_offline_indicator_checks_original_independently_of_proxy_and_recovers(client, asset, tmp_path, monkeypatch):
    original = tmp_path / 'original.mxf'
    proxy = tmp_path / 'viewing.mp4'
    original.write_bytes(b'original')
    proxy.write_bytes(b'proxy')
    update_job(asset.job_id, local_path=str(original), viewing_path=str(proxy), media_origin='external')
    monkeypatch.setattr(source_availability, '_cache', {})
    clock = [100.0]
    monkeypatch.setattr(source_availability.time, 'monotonic', lambda: clock[0])
    body = {'job_ids': [asset.job_id, asset.job_id, 'unknown']}
    status = client.post('/api/library/source-status', json=body)
    assert status.status_code == 200 and status.json()['sources'] == {asset.job_id: False}
    offline = original.with_suffix('.offline')
    original.rename(offline)
    clock[0] += 3
    assert client.post('/api/library/source-status', json=body).json()['sources'] == {asset.job_id: True}
    assert proxy.is_file()
    offline.rename(original)
    clock[0] += 3
    assert client.post('/api/library/source-status', json=body).json()['sources'] == {asset.job_id: False}
    assert str(tmp_path) not in status.text


def test_nonlocal_sources_are_not_marked_offline_and_batch_size_is_bounded(client, asset):
    assert client.post('/api/library/source-status', json={'job_ids': [asset.job_id]}).json()['sources'] == {asset.job_id: None}
    assert client.post('/api/library/source-status', json={'job_ids': []}).status_code == 422
    assert client.post('/api/library/source-status', json={'job_ids': ['unknown'] * 101}).status_code == 422
