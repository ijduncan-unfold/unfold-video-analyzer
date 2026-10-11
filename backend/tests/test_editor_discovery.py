"""Local discovery never exposes credentials remotely or replaces a live successor."""
import asyncio
import json
import os
from pathlib import Path
import ssl
import stat
import time
from types import SimpleNamespace
from unittest.mock import Mock
import urllib.error
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import editor_bridge, editor_discovery as discovery


@pytest.fixture(autouse=True)
def use_real_verifier(monkeypatch, isolate_editor_discovery):
    # These tests explicitly mock their opener; the rest of the suite stays network-free.
    monkeypatch.setattr(discovery, 'probe_instance', discovery_probe_original)


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'database_path', str(tmp_path / 'library.sqlite3'))
    monkeypatch.setattr(settings, 'editor_token_path', str(tmp_path / 'editor-token.txt'))
    monkeypatch.setattr(settings, 'upload_dir', str(tmp_path / 'uploads'))
    return discovery.EditorDiscovery(tmp_path / 'private' / 'editor-service.json',
                                     'http://127.0.0.1:8000', str(uuid.uuid4()))


def descriptor(instance):
    return json.loads(instance.path.read_text(encoding='utf-8'))


async def wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Isolated discovery did not update')
        await asyncio.sleep(0.01)


@pytest.mark.parametrize('platform,suffix', [
    ('win32', ('.video-analyzer', 'editor-service.json')),
    ('darwin', ('Library', 'Application Support', 'VideoAnalyzer', 'editor-service.json')),
    ('linux', ('.local', 'share', 'video-analyzer', 'editor-service.json')),
])
def test_fixed_discovery_path_is_in_current_user_data(monkeypatch, tmp_path, platform, suffix):
    monkeypatch.setattr(discovery.sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    assert discovery.default_discovery_path() == tmp_path.joinpath(*suffix)


def test_explicit_discovery_override_is_isolated_and_requires_absolute_path(monkeypatch, tmp_path):
    private = tmp_path / 'isolated' / 'editor-service.json'
    monkeypatch.setattr(settings, 'editor_discovery_path', str(private))
    assert discovery.discovery_path() == private
    monkeypatch.setattr(settings, 'editor_discovery_path', 'relative/editor-service.json')
    with pytest.raises(ValueError, match='absolute'):
        discovery.discovery_path()


@pytest.mark.parametrize('server,normalized', [
    ('http://localhost:8000', 'http://localhost:8000'),
    ('http://127.0.0.1:8000/', 'http://127.0.0.1:8000'),
    (' HTTPS://LOCALHOST:8443/ ', 'https://localhost:8443'),
    ('https://127.0.0.1:8443', 'https://127.0.0.1:8443'),
])
def test_supported_local_origins_normalize(server, normalized):
    assert discovery.normalize_server(server) == normalized


@pytest.mark.parametrize('server', [
    'http://localhost:8443', 'https://localhost:8000', 'http://localhost:18742',
    'http://user@localhost:8000', 'http://localhost:8000/path', 'http://localhost:8000?x=1',
    'http://127.0.0.1:8000.evil', 'https://example.com:8443', 'file:///secret',
])
def test_unsupported_addresses_never_receive_token(monkeypatch, server):
    opener = Mock()
    monkeypatch.setattr(discovery.urllib.request, 'build_opener', opener)
    assert not discovery.probe_instance(server, 't' * 43, str(uuid.uuid4()))
    opener.assert_not_called()


def response_for(instance, **overrides):
    data = {'status': 'ok', 'host': 'resolve', 'protocol_version': 1, 'instance_id': instance.instance_id}
    data.update(overrides)
    response = Mock()
    response.read.return_value = json.dumps(data).encode('utf-8')
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def test_probe_requires_our_authenticated_health_and_disables_proxy_redirects(instance, monkeypatch):
    token = editor_bridge.get_pairing_token()
    opener = Mock()
    opener.open.return_value = response_for(instance)
    build = Mock(return_value=opener)
    monkeypatch.setattr(discovery.urllib.request, 'build_opener', build)
    assert discovery.probe_instance(instance.server, token, instance.instance_id)
    request = opener.open.call_args.args[0]
    assert request.full_url == 'http://127.0.0.1:8000/api/editor/resolve/health'
    assert request.get_header('Authorization') == 'Bearer ' + token
    assert opener.open.call_args.kwargs == {'timeout': 1}
    handlers = build.call_args.args
    assert next(h for h in handlers if isinstance(h, discovery.urllib.request.ProxyHandler)).proxies == {}
    redirect = next(h for h in handlers if isinstance(h, discovery._NoRedirect))
    assert redirect.redirect_request(request, None, 302, 'Found', {}, 'https://example.com') is None
    opener.open.side_effect = urllib.error.HTTPError(request.full_url, 302, 'Found', {}, None)
    assert not discovery.probe_instance(instance.server, token, instance.instance_id)
    assert opener.open.call_count == 2  # One origin request for each probe, no forwarded request.


@pytest.mark.parametrize('overrides', [
    {'instance_id': str(uuid.uuid4())}, {'instance_id': None}, {'host': 'premiere'},
    {'status': 'starting'}, {'protocol_version': 2},
])
def test_wrong_service_or_instance_cannot_prove_readiness(instance, monkeypatch, overrides):
    opener = Mock()
    opener.open.return_value = response_for(instance, **overrides)
    monkeypatch.setattr(discovery.urllib.request, 'build_opener', Mock(return_value=opener))
    assert not discovery.probe_instance(instance.server, 't' * 43, instance.instance_id)


def test_https_probe_uses_strict_launcher_ca_context_and_refuses_tls_bypass(instance, monkeypatch):
    context = ssl.create_default_context()
    opener = Mock()
    opener.open.return_value = response_for(instance)
    build = Mock(return_value=opener)
    monkeypatch.setattr(discovery.urllib.request, 'build_opener', build)
    assert discovery.probe_instance('https://localhost:8443', 't' * 43, instance.instance_id, context)
    handler = next(h for h in build.call_args.args if isinstance(h, discovery.urllib.request.HTTPSHandler))
    assert handler._context is context
    build.reset_mock()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    assert not discovery.probe_instance('https://localhost:8443', 't' * 43, instance.instance_id, context)
    build.assert_not_called()


def test_atomic_publication_restricts_empty_file_before_writing_token(instance, monkeypatch):
    token = editor_bridge.get_pairing_token()
    original_private = discovery._make_private
    checked_empty = []

    def private(path, directory=False):
        if path.suffix == '.tmp':
            assert path.read_bytes() == b''
            checked_empty.append(path)
        original_private(path, directory)

    monkeypatch.setattr(discovery, '_make_private', private)
    assert instance._publish(token)
    assert descriptor(instance) == {'version': 1, 'server': instance.server, 'token': token,
                                    'instance_id': instance.instance_id}
    assert len(checked_empty) == 1
    assert not list(instance.path.parent.glob('*.tmp'))
    if os.name != 'nt':
        assert stat.S_IMODE(instance.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(instance.path.parent.stat().st_mode) == 0o700


def test_failed_atomic_update_keeps_existing_descriptor_and_removes_staging(instance, monkeypatch):
    token = editor_bridge.get_pairing_token()
    instance._publish(token)
    retained = instance.path.read_bytes()
    monkeypatch.setattr(discovery.os, 'replace', Mock(side_effect=OSError('Injected atomic replacement failure')))
    with pytest.raises(OSError):
        instance._publish('n' * 43, require_owned=True)
    assert instance.path.read_bytes() == retained
    assert not list(instance.path.parent.glob('*.tmp'))


def test_failed_private_permissions_never_write_or_log_token(instance, monkeypatch, caplog):
    token = editor_bridge.get_pairing_token()
    monkeypatch.setattr(discovery, 'probe_instance', lambda *args: True)
    monkeypatch.setattr(discovery, '_make_private', Mock(side_effect=OSError('Injected private permission failure')))
    asyncio.run(instance.run())
    assert not instance.path.exists()
    assert token not in caplog.text
    assert 'private local discovery' in caplog.text


@pytest.mark.parametrize('linked', ['file', 'parent', 'lock'])
def test_reparse_paths_are_rejected_without_touching_target(instance, monkeypatch, linked):
    instance.path.parent.mkdir()
    instance.path.write_bytes(b'untouched-descriptor')
    lock = instance.path.with_name(f'.{instance.path.name}.lock')
    lock.write_bytes(b'untouched-lock')
    target = {'file': instance.path, 'parent': instance.path.parent, 'lock': lock}[linked]
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink,
                                   st_file_attributes=0x400)
        return info

    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ValueError, match='reparse'):
        instance._publish('t' * 43)
    assert instance.path.read_bytes() == b'untouched-descriptor'
    assert lock.read_bytes() == b'untouched-lock'


def test_hard_linked_descriptor_is_rejected_without_changing_original(instance, tmp_path):
    instance.path.parent.mkdir()
    original = tmp_path / 'original-file'
    original.write_bytes(b'original-stays-private')
    os.link(original, instance.path)
    with pytest.raises(ValueError, match='unlinked regular file'):
        instance._publish('t' * 43)
    assert original.read_bytes() == instance.path.read_bytes() == b'original-stays-private'


def test_successor_is_preserved_by_old_cleanup_and_rotation(instance):
    token = editor_bridge.get_pairing_token()
    instance._publish(token)
    successor = discovery.EditorDiscovery(instance.path, instance.server, str(uuid.uuid4()))
    successor._publish('s' * 43)
    retained = instance.path.read_bytes()
    assert not instance._publish('n' * 43, require_owned=True)
    instance.stop()
    instance.remove_owned()
    assert instance.path.read_bytes() == retained
    successor.stop()
    successor.remove_owned()
    assert not instance.path.exists()


def test_failed_competing_startup_never_overwrites_active_descriptor(instance, monkeypatch):
    instance._publish(editor_bridge.get_pairing_token())
    retained = instance.path.read_bytes()
    competitor = discovery.EditorDiscovery(instance.path, instance.server, str(uuid.uuid4()))
    monkeypatch.setattr(discovery, 'READY_TIMEOUT', 0.04)
    monkeypatch.setattr(discovery, 'READY_INTERVAL', 0.005)
    # The active server returns the first instance's UUID, never the competitor's.
    opener = Mock()
    opener.open.return_value = response_for(instance)
    monkeypatch.setattr(discovery.urllib.request, 'build_opener', Mock(return_value=opener))
    monkeypatch.setattr(discovery, 'probe_instance', discovery_probe_original)
    asyncio.run(competitor.run())
    competitor.stop()
    competitor.remove_owned()
    assert instance.path.read_bytes() == retained


# Preserve the actual verifier before conftest substitutes its network-free default.
discovery_probe_original = discovery.probe_instance


def test_publication_waits_for_readiness_then_refreshes_rotated_token(instance, monkeypatch):
    old = editor_bridge.get_pairing_token()
    calls = []

    def ready(*args):
        calls.append(args)
        if len(calls) < 2:
            assert not instance.path.exists()
            return False
        return True

    monkeypatch.setattr(discovery, 'probe_instance', ready)
    monkeypatch.setattr(discovery, 'READY_INTERVAL', 0.01)
    monkeypatch.setattr(discovery, 'ROTATION_INTERVAL', 0.01)

    async def exercise():
        task = asyncio.create_task(instance.run())
        try:
            await wait_until(lambda: instance._published_token == old)
            assert descriptor(instance)['token'] == old
            fresh = editor_bridge.get_pairing_token(rotate=True)
            # Wait for atomic publication to finish before opening the file.
            # A tight read loop can lock the target during replacement on Windows.
            await wait_until(lambda: instance._published_token == fresh)
            assert descriptor(instance)['token'] == fresh
            assert fresh != old
            assert calls[-1][1] == fresh and calls[-1][2] == instance.instance_id
        finally:
            instance.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            instance.remove_owned()
        assert not instance.path.exists()

    asyncio.run(exercise())


def test_stopped_publisher_cannot_recreate_descriptor(instance):
    token = editor_bridge.get_pairing_token()
    instance.stop()
    assert not instance._publish(token)
    assert not instance.path.exists()


def test_lifespan_health_identity_is_authenticated_and_descriptor_removed(instance, monkeypatch):
    monkeypatch.setattr(settings, 'editor_discovery_path', str(instance.path))
    monkeypatch.setattr(app.state, 'editor_connection_server', 'https://localhost:8443', raising=False)
    context = ssl.create_default_context()
    monkeypatch.setattr(app.state, 'editor_connection_ssl_context', context, raising=False)
    calls = []
    monkeypatch.setattr(discovery, 'probe_instance', lambda *args: calls.append(args) or True)
    token = editor_bridge.get_pairing_token()
    with TestClient(app, client=('127.0.0.1', 52000)) as client:
        assert client.get('/api/editor/resolve/health').status_code == 401
        response = client.get('/api/editor/resolve/health', headers={'Authorization': 'Bearer ' + token})
        assert response.status_code == 200
        identity = response.json()['instance_id']
        assert str(uuid.UUID(identity)) == identity
        assert token not in response.text
        deadline = time.monotonic() + 5
        while not instance.path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        data = descriptor(instance)
        assert data['instance_id'] == identity
        assert data['server'] == 'https://localhost:8443'
        assert calls[0] == ('https://localhost:8443', token, identity, context)
    assert not instance.path.exists()
