"""Package only public UXP resources as a private-distribution CCX preview."""
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'integrations' / 'premiere'
FILES = ('manifest.json', 'index.html', 'styles.css', 'panel.js', 'host.js', 'connection.js')


def main():
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['host']['app'] != 'premierepro' or manifest['manifestVersion'] != 5:
        raise ValueError('Expected one Premiere UXP v5 host')
    destination = ROOT / 'dist' / 'installers' / f"VideoAnalyzer-Premiere-{manifest['version']}.ccx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as package:
        for name in FILES:
            package.write(SOURCE / name, arcname=name)
    with zipfile.ZipFile(destination) as package:
        if sorted(package.namelist()) != sorted(FILES) or package.testzip() is not None:
            raise ValueError('CCX resource verification failed')
    print(destination)


if __name__ == '__main__':
    main()
