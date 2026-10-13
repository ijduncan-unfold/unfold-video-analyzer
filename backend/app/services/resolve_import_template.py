"""Generated Resolve handoff script. Runs only when explicitly executed in Resolve.

Open the intended project, then use Workspace > Console > Python 3:
exec(open('/path/to/Import into Resolve.py', encoding='utf-8').read())
Requires Resolve's Python scripting runtime. Does not create a timeline or copy media.
"""
import hashlib
import json
import os


def path_key(path):
    return os.path.normcase(os.path.normpath(path))


def check_file(entry):
    path = entry['path']
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise RuntimeError('Original is offline: ' + path)
    stat = os.stat(path)
    if stat.st_size != entry['size'] or stat.st_mtime_ns != entry['mtime_ns']:
        raise RuntimeError('Original changed since export; export a fresh package: ' + path)


def mapped_values(mapping):
    allowed = {'Description', 'Scene', 'Shot', 'Take', 'Camera #', 'Roll Card #',
               'Production Name', 'Director', 'DOP', 'Shot Type', 'Move', 'Location'}
    values = {}
    for field in mapping['fields']:
        key = 'Roll Card #' if field.get('resolve') == 'Roll/Card' else field.get('resolve')
        if key in allowed and field.get('value'):
            values[key] = field['value']
    return values


def merge_metadata(before, entry):
    mapping = entry['mapping']
    changes = {key: value for key, value in mapped_values(mapping).items() if not before.get(key)}
    field = next((item for item in mapping['fields'] if item['key'] == 'keywords'), {})
    words = [word.strip() for word in before.get('Keywords', '').split(',') if word.strip()]
    for word in field.get('xmp_value', []):
        if word.casefold() not in {old.casefold() for old in words}:
            words.append(word)
    if words:
        changes['Keywords'] = ', '.join(words)
    comments = before.get('Comments', '')
    # Preserve human comments and existing snapshots; same package is idempotent.
    snapshot = json.dumps(mapping['record'], ensure_ascii=False, sort_keys=True)
    marker = '[Unfold selection ' + entry['id'] + ' ' + hashlib.sha256(snapshot.encode()).hexdigest()[:16] + ']'
    if marker not in comments:
        details = '\n'.join(field['label'] + ': ' + field['value'] for field in mapping['fields'])
        changes['Comments'] = (comments + '\n\n' + marker + '\n' + details + '\nComplete analysis JSON:\n' + snapshot).strip()
    if len(changes.get('Comments', '')) > 4000000:
        raise RuntimeError('Metadata exceeds the 4 MB import limit; use the JSON sidecar. No metadata was truncated.')
    return {key: value for key, value in changes.items() if value != before.get(key)}


def run_import(payload, resolve=None):
    if payload.get('schema') != 'unfold.resolve-import.v1' or not 1 <= len(payload.get('items', [])) <= 1000:
        raise RuntimeError('Invalid Unfold media package.')
    entries = payload['items']
    if len({path_key(entry['path']) for entry in entries}) != len(entries):
        raise RuntimeError('Duplicate original paths. Refine the selection before importing.')
    for entry in entries:
        check_file(entry)
    if resolve is None:
        try:
            import DaVinciResolveScript as dvr
            resolve = dvr.scriptapp('Resolve')
        except ImportError as exc:
            raise RuntimeError('Run this script in Resolve Workspace > Console > Python 3 with a project open.') from exc
    if resolve is None:
        raise RuntimeError('No running Resolve instance. Open the intended project first.')
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None:
        raise RuntimeError('Open the intended Resolve project first.')
    project_id = project.GetUniqueId()
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    prior = pool.GetCurrentFolder()
    digest = hashlib.sha256(json.dumps([entry['id'] for entry in entries]).encode()).hexdigest()[:8]
    name = payload['title'] + ' · ' + digest
    matches = [folder for folder in root.GetSubFolderList() if folder.GetName() == name]
    if len(matches) > 1:
        raise RuntimeError('Duplicate selection bins. Rename the duplicate before retrying.')
    def check_project():
        active = manager.GetCurrentProject()
        if active is None or active.GetUniqueId() != project_id:
            raise RuntimeError('Active project changed. Completed imports remain in the original project.')
    check_project()
    folder = matches[0] if matches else pool.AddSubFolder(root, name)
    if not folder:
        raise RuntimeError('Resolve could not create the selection bin.')
    folder_id = folder.GetUniqueId()
    def check_target():
        check_project()
        current = pool.GetCurrentFolder()
        if current is None or current.GetUniqueId() != folder_id:
            raise RuntimeError('Active bin changed; stop and retry from the intended project.')
    if pool.SetCurrentFolder(folder) is not True:
        raise RuntimeError('Resolve could not select the import bin.')
    summary = {'imported': 0, 'reused': 0, 'synced': 0, 'bin': name}
    try:
        existing = {}
        for clip in folder.GetClipList():
            path = clip.GetClipProperty('File Path')
            if path:
                existing.setdefault(path_key(path), []).append(clip)
        for entry in entries:
            check_file(entry)
            check_target()
            matches = existing.get(path_key(entry['path']), [])
            if len(matches) > 1:
                raise RuntimeError('Duplicate originals in selection bin: ' + entry['path'])
            if matches:
                clip = matches[0]
                summary['reused'] += 1
            else:
                imported = pool.ImportMedia([entry['path']])
                if not imported or len(imported) != 1:
                    raise RuntimeError('Resolve could not import this original. Check codec support: ' + entry['path'])
                clip = imported[0]
                summary['imported'] += 1
            check_target()
            if path_key(clip.GetClipProperty('File Path') or '') != path_key(entry['path']):
                raise RuntimeError('Resolve returned different media; no metadata was written.')
            if clip.GetUniqueId() not in {item.GetUniqueId() for item in folder.GetClipList()}:
                raise RuntimeError('Resolve did not confirm this clip in the selection bin.')
            before = clip.GetMetadata()
            if not isinstance(before, dict):
                raise RuntimeError('Resolve could not read clip metadata.')
            changes = merge_metadata(before, entry)
            check_target()
            if clip.GetMetadata() != before:
                raise RuntimeError('Metadata changed during import; editor changes were preserved.')
            if changes and clip.SetMetadata(changes) is not True:
                raise RuntimeError('Resolve rejected metadata. Completed media remains in the bin.')
            after = clip.GetMetadata()
            if any(after.get(key) != value for key, value in changes.items()):
                raise RuntimeError('Metadata readback failed. Inspect this clip before retrying.')
            summary['synced'] += 1
            print('Unfold: %s/%s originals with metadata' % (summary['synced'], len(entries)))
        check_target()
        print('Ready:', summary)
        return summary
    except Exception:
        print('Stopped:', summary, 'Completed clips remain; retry reuses this bin.')
        raise
    finally:
        active = manager.GetCurrentProject()
        if active and active.GetUniqueId() == project_id:
            current = pool.GetCurrentFolder()
            if prior and current and current.GetUniqueId() == folder_id:
                pool.SetCurrentFolder(prior)
