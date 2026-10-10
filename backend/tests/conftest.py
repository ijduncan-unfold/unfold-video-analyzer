import os
import tempfile
from pathlib import Path

# Set before the first application import: tests never touch the real workspace DB.
_test_root = tempfile.TemporaryDirectory(prefix='video-analyzer-tests-')
os.environ['DATABASE_PATH'] = str(Path(_test_root.name) / 'library.sqlite3')
os.environ['UPLOAD_DIR'] = str(Path(_test_root.name) / 'uploads')
os.environ['EDITOR_TOKEN_PATH'] = str(Path(_test_root.name) / 'premiere-token.txt')
os.environ['GOOGLE_API_KEY'] = ''

import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path):
    settings.database_path = str(tmp_path / 'library.sqlite3')
    settings.upload_dir = str(tmp_path / 'uploads')
    settings.editor_token_path = str(tmp_path / 'premiere-token.txt')
    settings.google_api_key = ''
    with TestClient(app) as client:
        yield client


@pytest.fixture
def asset(client):
    from app.services.job_store import Job, create_job
    return create_job(Job(job_id='test-asset', file_id='private-provider-id', file_uri='private-provider-uri',
        filename='Harbor commercial.mov', mime_type='video/quicktime', size_bytes=100, local_path='',
        metadata={'title': 'Harbor film', 'client': 'Studio', 'project': 'Spring launch', 'tags': ['ocean']},
        technical={'duration_seconds': 8, 'frame_rate': 25, 'frame_rate_fraction': '25/1',
                   'source_timecode': '01:00:00:00', 'width': 1920, 'height': 1080, 'has_audio': True},
        flash_result={'total_duration': '00:08.000', 'total_scenes': 1, 'total_shots': 2, 'scenes': [
            {'scene_number': 1, 'scene_title': 'At the harbor', 'start_time': '00:00.000', 'end_time': '00:08.000', 'shots': [
                {'shot_number': 1, 'start_time': '00:00.000', 'end_time': '00:03.500', 'shot_type': 'WS',
                 'camera_movement': 'Static', 'visual_description': 'A sailboat crosses blue water', 'tags': ['boat'],
                 'audio_notes': 'Waves', 'subjects': ['sailboat'], 'mood': 'Calm'},
                {'shot_number': 2, 'start_time': '00:03.500', 'end_time': '00:08.000', 'shot_type': 'CU',
                 'camera_movement': 'Handheld', 'visual_description': 'Hands tighten a rope', 'tags': ['hands'],
                 'audio_notes': '', 'subjects': ['hands', 'rope'], 'mood': 'Focused'}]}]}))
