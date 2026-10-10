"""Package public Resolve resources; the installed vendor bridge is never redistributed."""
import json
import os
from pathlib import Path
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ID = 'com.ijduncan.videoanalyzer'


def package(root=ROOT):
    root = Path(root)
    source = root / 'integrations' / 'resolve'
    support = root / 'packaging' / 'resolve'
    files = json.loads((support / 'public-files.json').read_text(encoding='utf-8'))
    if not isinstance(files, list) or not files or not all(isinstance(name, str) for name in files) or len(set(files)) != len(files):
        raise ValueError('Invalid public Resolve resource allowlist')
    if not {'manifest.xml', 'package.json', 'main.js'}.issubset(files):
        raise ValueError('The Resolve allowlist is missing required entry files')
    for name in files:
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name or ':' in name or name.startswith('.') or name.endswith('.node'):
            raise ValueError('Only public plugin filenames may be packaged')
        if (source / name).is_symlink() or not (source / name).is_file():
            raise ValueError('A public Resolve resource is missing or linked outside the source')
    manifest = ET.parse(source / 'manifest.xml').getroot()
    version = manifest.findtext('Plugin/Version')
    if manifest.findtext('Plugin/Id') != PLUGIN_ID or manifest.findtext('Plugin/FilePath') != 'main.js':
        raise ValueError('Unexpected Resolve plugin identity or entry point')
    metadata = json.loads((source / 'package.json').read_text(encoding='utf-8'))
    if metadata.get('version') != version or not version or not re.fullmatch(r'\d+\.\d+\.\d+', version) or metadata.get('main') != 'main.js':
        raise ValueError('Resolve package versions must match')
    destination = root / 'dist' / 'installers' / f'VideoAnalyzer-Resolve-{version}.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    helpers = {'install-resolve.ps1': 'install-resolve.ps1',
               'install-resolve-macos.command': 'Install Resolve on Mac.command',
               'public-files.json': 'public-files.json', 'Read Me.txt': 'Read Me.txt'}
    for name in helpers:
        path = support / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('A Resolve installation helper is missing or linked')
    temporary = destination.with_name('.resolve-' + uuid.uuid4().hex + '.tmp')
    def write_public(output, path, name):
        # Preserve executable Mac helpers even when this ZIP is built on Windows.
        info = zipfile.ZipInfo.from_file(path, arcname=name)
        info.create_system = 3
        info.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        output.writestr(info, path.read_bytes())

    try:
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for name in files:
                write_public(output, source / name, 'VideoAnalyzer/' + name)
            for name, packaged_name in helpers.items():
                write_public(output, support / name, packaged_name)
            output.writestr('Install Resolve on Windows.cmd',
                            '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-resolve.ps1"\r\n')
        with zipfile.ZipFile(temporary) as output:
            if output.testzip() is not None:
                raise ValueError('Resolve package verification failed')
            if any(name.endswith('.node') or name.startswith('.') for name in output.namelist()):
                raise ValueError('Private or vendor resources entered the Resolve package')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    print(package())
