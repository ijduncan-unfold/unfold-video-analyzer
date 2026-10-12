from app.services.job_store import update_job, Job, create_job


def add_slates(asset):
    flash = asset.flash_result
    flash['scenes'][0]['shots'][0]['slates'] = [{
        'production_title': 'Freeman Chronicles', 'scene': '7B', 'take': '4',
        'camera_roll': 'A001 / B001', 'cinematographer': 'C. WESTERVELT',
    }]
    flash['scenes'][0]['shots'][1]['slates'] = [{
        'production_title': 'Freeman Chronicles', 'scene': '8A', 'take': '5',
        'camera_roll': 'A002', 'cinematographer': 'C. WESTERVELT',
    }]
    update_job(asset.job_id, flash_result=flash, shot_annotations={'2': {'tags': ['favorite']}})


def test_slate_and_manual_production_facets_and_exact_filters(client, asset):
    add_slates(asset)
    facets = client.get('/api/library').json()['facets']
    assert facets['production'] == ['Freeman Chronicles', 'Spring launch']
    assert facets['scene'] == ['7B', '8A']
    assert facets['take'] == ['4', '5']
    assert facets['camera_roll'] == ['A001 / B001', 'A002']
    assert facets['cinematographer'] == ['C. WESTERVELT']
    assert set(facets['tags']) == {'ocean', 'boat', 'hands', 'favorite'}
    assert client.get('/api/library', params={'production': 'freeman chronicles', 'scene': '7b', 'take': '4'}).json()['total'] == 1
    assert client.get('/api/library', params={'production': 'Spring launch', 'take': '5', 'camera_roll': 'A002'}).json()['total'] == 1
    assert client.get('/api/library', params={'scene': '7', 'take': '4'}).json()['total'] == 0
    assert client.get('/api/library', params={'scene': '7B', 'take': '5'}).json()['total'] == 0
    assert client.get('/api/library', params={'cinematographer': 'C. WESTERVELT'}).json()['total'] == 1
    assert client.get('/api/library?tag=favorite').json()['total'] == 1
    assert client.get('/api/library?tag=boat').json()['total'] == 1


def test_clip_metadata_filters_only_return_matching_clip(client, asset):
    add_slates(asset)
    result = client.get('/api/library/search/shots', params={'job_id': asset.job_id, 'scene': '8A', 'take': '5'}).json()
    assert [shot['shot_number'] for shot in result['shots']] == [2]
    assert client.get('/api/library/search/shots', params={'scene': '7B', 'take': '5'}).json()['total'] == 0
    assert client.get('/api/library/search/shots', params={'production': 'Spring launch', 'scene': '7B'}).json()['total'] == 1
    assert client.get('/api/library/search/shots', params={'camera_roll': 'A002', 'q': 'rope'}).json()['total'] == 1


def test_facets_keep_options_after_filter_and_respect_folder(client, asset):
    add_slates(asset)
    create_job(Job(job_id='unanalysed', filename='Other.mov', file_id='', file_uri='',
                   mime_type='video/quicktime', size_bytes=0, local_path='', metadata={'project': 'Other production'}))
    update_job(asset.job_id, folder_id='camera-a')
    result = client.get('/api/library', params={'folder_id': 'camera-a', 'take': 'missing'}).json()
    assert result['total'] == 0
    assert result['facets']['take'] == ['4', '5']
    assert 'Other production' not in result['facets']['production']
    assert client.get('/api/library', params={'production': 'Other production'}).json()['total'] == 1
    assert client.get('/api/library', params={'production': 'Other production', 'take': '4'}).json()['total'] == 0
