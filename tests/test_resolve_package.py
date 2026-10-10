"""Public Resolve ZIP guards, without loading Resolve or its vendor SDK."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('package_resolve', REPO / 'scripts' / 'package-resolve.py')
packager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packager)


class ResolvePackageTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix='video-analyzer-public-package-test-')
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.source = self.root / 'integrations' / 'resolve'
        self.support = self.root / 'packaging' / 'resolve'
        self.source.mkdir(parents=True)
        self.support.mkdir(parents=True)
        self.public_files = json.loads((REPO / 'packaging' / 'resolve' / 'public-files.json').read_text())
        for name in self.public_files:
            (self.source / name).write_text('public resource: ' + name, encoding='utf-8')
        (self.source / 'manifest.xml').write_text('''<?xml version="1.0"?>
<BlackmagicDesign><Plugin><Id>com.ijduncan.videoanalyzer</Id>
<Version>0.1.0</Version><FilePath>main.js</FilePath></Plugin></BlackmagicDesign>''', encoding='utf-8')
        self.metadata = {'name': 'video-analyzer-resolve', 'version': '0.1.0', 'main': 'main.js'}
        self.write_metadata()
        self.write_allowlist(self.public_files)
        self.helpers = {'install-resolve.ps1': 'install-resolve.ps1',
                        'install-resolve-macos.command': 'Install Resolve on Mac.command',
                        'public-files.json': 'public-files.json', 'Read Me.txt': 'Read Me.txt'}
        for name in self.helpers:
            if name != 'public-files.json':
                (self.support / name).write_text('public installation helper: ' + name, encoding='utf-8')

    def write_allowlist(self, value):
        (self.support / 'public-files.json').write_text(json.dumps(value), encoding='utf-8')

    def write_metadata(self):
        (self.source / 'package.json').write_text(json.dumps(self.metadata), encoding='utf-8')

    def manifest_change(self, field, value):
        path = self.source / 'manifest.xml'
        document = ET.parse(path)
        document.find('Plugin/' + field).text = value
        document.write(path, encoding='utf-8', xml_declaration=True)

    def assert_no_deliverable(self):
        self.assertEqual(list((self.root / 'dist' / 'installers').glob('*.zip')), [])

    def test_zip_contains_public_resources_and_install_helpers_only(self):
        destination = packager.package(self.root)
        self.assertEqual(destination.parent, self.root / 'dist' / 'installers')
        self.assertEqual(destination.name, 'VideoAnalyzer-Resolve-0.1.0.zip')
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            expected = {'VideoAnalyzer/' + name for name in self.public_files}
            expected.update(self.helpers.values())
            expected.add('Install Resolve on Windows.cmd')
            self.assertEqual(set(archive.namelist()), expected)
            for name in self.public_files:
                self.assertEqual(archive.read('VideoAnalyzer/' + name), (self.source / name).read_bytes())
            windows_helper = archive.read('Install Resolve on Windows.cmd').decode()
            self.assertIn('"%~dp0install-resolve.ps1"', windows_helper)
            mac_helper = archive.getinfo('Install Resolve on Mac.command')
            self.assertEqual(mac_helper.create_system, 3)
            self.assertEqual((mac_helper.external_attr >> 16) & 0o777, 0o755)
            self.assertEqual((archive.getinfo('VideoAnalyzer/main.js').external_attr >> 16) & 0o777, 0o644)

    def test_private_media_and_vendor_files_beside_sources_never_enter_zip(self):
        secret = b'not-for-distribution-test-secret'
        for name in ('.env', '.env.local', 'pairing-token.txt', 'Original film.mp4', 'WorkflowIntegration.node'):
            (self.source / name).write_bytes(secret)
        nested = self.source / 'node_modules' / 'vendor'
        nested.mkdir(parents=True)
        (nested / 'WorkflowIntegration.node').write_bytes(secret)
        (self.support / 'private-ca-key.pem').write_bytes(secret)
        destination = packager.package(self.root)
        with zipfile.ZipFile(destination) as archive:
            self.assertFalse(any(name.endswith(('.mp4', '.node', '.pem')) for name in archive.namelist()))
            self.assertFalse(any('token' in name or '.env' in name for name in archive.namelist()))
            for name in archive.namelist():
                self.assertNotIn(secret, archive.read(name))

    def test_allowlist_cannot_escape_source_or_distribute_dotfiles_and_vendor_binary(self):
        outside = self.root / 'private-secret.txt'
        outside.write_text('outside-source-secret')
        for extra in ('../private-secret.txt', '..\\private-secret.txt', str(outside),
                      '/private/secret.txt', 'C:\\Private\\secret.txt', '.', '.env', 'WorkflowIntegration.node'):
            with self.subTest(extra=extra):
                self.write_allowlist(self.public_files + [extra])
                with self.assertRaises(ValueError):
                    packager.package(self.root)
                self.assert_no_deliverable()

    def test_duplicate_empty_or_nonlist_allowlist_is_rejected(self):
        for value in ([], {}, 'main.js', [None], [{}], self.public_files + [self.public_files[0]]):
            with self.subTest(value=value):
                self.write_allowlist(value)
                with self.assertRaises(ValueError):
                    packager.package(self.root)
                self.assert_no_deliverable()

    def test_missing_public_resource_never_creates_an_installable_zip(self):
        (self.source / 'main.js').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or linked'):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_linked_public_resource_cannot_read_an_outside_private_file(self):
        resource = self.source / 'main.js'
        resource.unlink()
        outside = self.root / 'private-file.txt'
        outside.write_text('outside-private-resource')
        try:
            resource.symlink_to(outside)
        except OSError as exc:
            self.skipTest('Creating symbolic links is unavailable on this host: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'missing or linked'):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_linked_installation_helper_is_rejected(self):
        helper = self.support / 'install-resolve.ps1'
        helper.unlink()
        outside = self.root / 'private-helper.ps1'
        outside.write_text('outside-private-helper')
        try:
            helper.symlink_to(outside)
        except OSError as exc:
            self.skipTest('Creating symbolic links is unavailable on this host: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'missing or linked'):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_link_guard_runs_without_windows_symlink_privileges(self):
        original_is_symlink = Path.is_symlink
        for linked in (self.source / 'main.js', self.support / 'install-resolve.ps1'):
            with self.subTest(linked=linked.name):
                # Model a linked file on hosts where actually creating a link needs admin privileges.
                # Keep the resource present so a missing-file check cannot satisfy this test.
                linked.write_text('private file contents that a link must not redistribute')

                def is_symlink(path):
                    return path == linked or original_is_symlink(path)

                with patch.object(Path, 'is_symlink', is_symlink), self.assertRaisesRegex(ValueError, 'linked'):
                    packager.package(self.root)
                self.assert_no_deliverable()

    def test_manifest_identity_and_entry_point_must_match_plugin(self):
        for field, value in [('Id', 'different.plugin'), ('FilePath', '../private.js')]:
            with self.subTest(field=field):
                original = (self.source / 'manifest.xml').read_bytes()
                self.manifest_change(field, value)
                with self.assertRaisesRegex(ValueError, 'identity or entry point'):
                    packager.package(self.root)
                self.assert_no_deliverable()
                (self.source / 'manifest.xml').write_bytes(original)

    def test_version_mismatch_and_output_path_injection_are_rejected(self):
        for version in ('0.2.0', '../private', '0.1.0/../../other', ''):
            with self.subTest(version=version):
                self.metadata['version'] = version
                self.write_metadata()
                with self.assertRaisesRegex(ValueError, 'versions must match'):
                    packager.package(self.root)
                self.assert_no_deliverable()
        self.manifest_change('Version', '../private')
        self.metadata['version'] = '../private'
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'versions must match'):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_package_json_entry_point_mismatch_is_rejected(self):
        self.metadata['main'] = 'missing-or-private.js'
        self.write_metadata()
        with self.assertRaises(ValueError):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_entry_point_resources_cannot_be_omitted_from_public_allowlist(self):
        for required in ('manifest.xml', 'package.json', 'main.js'):
            with self.subTest(required=required):
                self.write_allowlist([name for name in self.public_files if name != required])
                with self.assertRaises(ValueError):
                    packager.package(self.root)
                self.assert_no_deliverable()

    def test_missing_installation_helper_leaves_no_partial_deliverable(self):
        (self.support / 'Read Me.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'missing or linked'):
            packager.package(self.root)
        self.assert_no_deliverable()

    def test_interrupted_archive_write_keeps_previous_package_and_cleans_temporary_file(self):
        destination = packager.package(self.root)
        previous = destination.read_bytes()
        write = zipfile.ZipFile.writestr
        calls = 0

        def interrupt(archive, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('Simulated interrupted filesystem write')
            return write(archive, *args, **kwargs)

        with patch.object(zipfile.ZipFile, 'writestr', interrupt), self.assertRaisesRegex(OSError, 'interrupted'):
            packager.package(self.root)
        self.assertEqual(destination.read_bytes(), previous)
        self.assertEqual(list(destination.parent.iterdir()), [destination])


if __name__ == '__main__':
    unittest.main()
