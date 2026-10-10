"""Exercise the installer against isolated public files and inert PE header fixtures.

No test installs a plugin into Resolve, launches Resolve, or executes a native bridge.
"""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "packaging" / "resolve" / "install-resolve.ps1"
MAC_INSTALLER = ROOT / "packaging" / "resolve" / "install-resolve-macos.command"
PUBLIC_FILES = json.loads((ROOT / "packaging" / "resolve" / "public-files.json").read_text())
PLUGIN_ID = "com.ijduncan.videoanalyzer"
WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer needs Windows")


def pe_header(machine=0x8664):
    data = bytearray(128)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    return bytes(data)


def manifest(plugin_id=PLUGIN_ID):
    return f"<BlackmagicDesign><Plugin><Id>{plugin_id}</Id><Name>Video Analyzer</Name></Plugin></BlackmagicDesign>"


@pytest.fixture
def installer_fixture(tmp_path):
    source = tmp_path / "public plugin"
    source.mkdir()
    for filename in PUBLIC_FILES:
        (source / filename).write_text(f"public fixture for {filename}", encoding="utf-8")
    (source / "manifest.xml").write_text(manifest(), encoding="utf-8")
    developer = tmp_path / "official SDK fixture"
    bridge = developer / "Workflow Integrations" / "Examples" / "SamplePromisePlugin" / "WorkflowIntegration.node"
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(pe_header())
    destination = tmp_path / "isolated installation" / PLUGIN_ID
    return source, developer, bridge, destination


def powershell_error_text(stderr):
    """Read captured diagnostics independently of console width and CLIXML."""
    if stderr.startswith("#< CLIXML\n"):
        try:
            document = ET.fromstring(stderr.partition("\n")[2])
        except ET.ParseError:
            pass
        else:
            errors = [item.text or "" for item in document.iter()
                      if item.tag.rsplit("}", 1)[-1] == "S" and item.get("S") == "Error"]
            if errors:
                stderr = "".join(errors)
    # PowerShell serializes line endings as _x000D__x000A_ in CLIXML.
    stderr = re.sub(r"_x([0-9a-fA-F]{4})_", lambda match: chr(int(match.group(1), 16)), stderr)
    return " ".join(stderr.split())


def run_install(fixture, extra=(), env=None, prefix=""):
    source, developer, _, destination = fixture
    args = ["-PluginPath", str(source), "-DeveloperPath", str(developer), "-Destination", str(destination), "-NoUI", *extra]
    executable = shutil.which("powershell") or shutil.which("pwsh")
    assert executable
    if prefix:
        def quote(value):
            return "'" + str(value).replace("'", "''") + "'"
        script = prefix + "\n& " + quote(INSTALLER) + " " + " ".join(value if value.startswith("-") else quote(value) for value in args)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        command = [executable, "-NoProfile", "-EncodedCommand", encoded]
    else:
        command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALLER), *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
    result.stderr = powershell_error_text(result.stderr)
    return result


@pytest.mark.parametrize("stderr, expected", [
    ('#< CLIXML\n<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04">'
     '<Obj S="progress"><S>Preparing modules.</S></Obj>'
     '<S S="Error">The plugin folder is not writable._x000D__x000A_</S>'
     '<S S="Error">Run the Windows installer as Administrator._x000D__x000A_</S>'
     '</Objs>', "The plugin folder is not writable. Run the Windows installer as Administrator."),
    ("D:\\a\\video-analyzer\\packaging\\resolve\\install-resolve.ps1 : "
     "The Resolve SDK bridge has an invalid\r\nPE header.\r\n",
     "D:\\a\\video-analyzer\\packaging\\resolve\\install-resolve.ps1 : "
     "The Resolve SDK bridge has an invalid PE header."),
])
def test_powershell_diagnostics_ignore_clixml_and_console_wrapping(stderr, expected):
    assert powershell_error_text(stderr) == expected


@WINDOWS_ONLY
def test_install_copies_only_public_files_and_local_sdk_bridge(installer_fixture):
    source, developer, bridge, destination = installer_fixture
    original = source.parent / "Original film.mov"
    original.write_bytes(b"untouched source footage")
    private = {".env.local": b"fake-private-key", "library.sqlite3": b"fake-db", "film.mov": b"media", "pairing-token.txt": b"fake-token"}
    for filename, value in private.items():
        (source / filename).write_bytes(value)
    (source / "WorkflowIntegration.node").write_bytes(b"a downloadable package must not supply the SDK bridge")
    sdk_private = developer / "private-sdk-file.txt"
    sdk_private.write_bytes(b"must never copy the entire SDK")
    result = run_install(installer_fixture)
    assert result.returncode == 0, result.stderr
    assert set(item.name for item in destination.iterdir()) == set(PUBLIC_FILES) | {"WorkflowIntegration.node"}
    assert (destination / "WorkflowIntegration.node").read_bytes() == bridge.read_bytes() == pe_header()
    for filename in PUBLIC_FILES:
        assert (destination / filename).read_bytes() == (source / filename).read_bytes()
    for filename, value in private.items():
        assert (source / filename).read_bytes() == value
    assert sdk_private.read_bytes() == b"must never copy the entire SDK"
    assert original.read_bytes() == b"untouched source footage"
    assert "Workspace > Workflow Integrations" in result.stdout


@WINDOWS_ONLY
def test_update_preserves_unknown_files_in_own_plugin(installer_fixture):
    _, _, _, destination = installer_fixture
    destination.mkdir(parents=True)
    (destination / "manifest.xml").write_text(manifest(), encoding="utf-8")
    (destination / "main.js").write_bytes(b"old plugin")
    retained = destination / "user-notes.txt"
    retained.write_bytes(b"retained")
    result = run_install(installer_fixture)
    assert result.returncode == 0, result.stderr
    assert retained.read_bytes() == b"retained"
    assert (destination / "main.js").read_bytes() == (installer_fixture[0] / "main.js").read_bytes()
    assert not list(destination.glob(".video-analyzer-*"))


@WINDOWS_ONLY
def test_staging_failure_does_not_replace_existing_plugin_files(installer_fixture):
    _, _, _, destination = installer_fixture
    destination.mkdir(parents=True)
    original_manifest = manifest()
    (destination / "manifest.xml").write_text(original_manifest, encoding="utf-8")
    (destination / "main.js").mkdir()
    retained = destination / "main.js" / "retained.txt"
    retained.write_bytes(b"not a plugin file")
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert "It was not overwritten" in " ".join(result.stderr.split())
    assert (destination / "manifest.xml").read_text(encoding="utf-8") == original_manifest
    assert retained.read_bytes() == b"not a plugin file"
    assert not (destination / "package.json").exists()
    assert not list(destination.glob(".video-analyzer-*"))


@WINDOWS_ONLY
def test_no_ui_permission_failure_does_not_request_elevation(installer_fixture):
    result = run_install(installer_fixture, prefix="""
function Copy-Item { throw [System.UnauthorizedAccessException]::new('fixture denies writing') }
function Start-Process { throw 'UNEXPECTED_ELEVATION' }
""")
    assert result.returncode != 0
    assert "writable. Run the Windows installer" in result.stderr
    assert "UNEXPECTED_ELEVATION" not in result.stderr
    assert not list(installer_fixture[3].glob(".video-analyzer-*"))
    assert not installer_fixture[3].exists()
    assert installer_fixture[2].read_bytes() == pe_header()


@WINDOWS_ONLY
@pytest.mark.parametrize("existing_manifest", [None, "another.company.plugin"])
def test_unknown_existing_destination_is_not_changed(installer_fixture, existing_manifest):
    _, _, _, destination = installer_fixture
    destination.mkdir(parents=True)
    retained = destination / "user-file.txt"
    retained.write_bytes(b"retained")
    if existing_manifest:
        (destination / "manifest.xml").write_text(manifest(existing_manifest), encoding="utf-8")
    before = {item.name: item.read_bytes() for item in destination.iterdir()}
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert "existing destination" in result.stderr
    assert {item.name: item.read_bytes() for item in destination.iterdir()} == before


@WINDOWS_ONLY
def test_missing_public_file_does_not_create_destination(installer_fixture):
    (installer_fixture[0] / "host.js").unlink()
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert "host.js is missing" in result.stderr
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
@pytest.mark.parametrize("source_manifest", [
    manifest("another.company.plugin"),
    '<!DOCTYPE document [<!ENTITY private SYSTEM "file:///not-read">]><BlackmagicDesign><Plugin><Id>&private;</Id></Plugin></BlackmagicDesign>',
])
def test_source_identity_and_entity_checks_before_install(installer_fixture, source_manifest):
    (installer_fixture[0] / "manifest.xml").write_text(source_manifest, encoding="utf-8")
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
def test_missing_sdk_bridge_does_not_create_destination(installer_fixture):
    installer_fixture[2].unlink()
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert "bridge was not found" in " ".join(result.stderr.split())
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
@pytest.mark.parametrize("bridge_contents", [b"not a native module", pe_header(0xAA64), pe_header(0x014C)])
def test_non_windows_or_wrong_architecture_bridge_is_rejected(installer_fixture, bridge_contents):
    installer_fixture[2].write_bytes(bridge_contents)
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
def test_invalid_pe_header_offset_is_rejected(installer_fixture):
    contents = bytearray(pe_header())
    struct.pack_into("<I", contents, 0x3C, 0xFFFFFFF0)
    installer_fixture[2].write_bytes(contents)
    result = run_install(installer_fixture)
    assert result.returncode != 0
    assert "invalid PE header" in result.stderr
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
def test_sample_plugin_fallback_uses_user_sdk(installer_fixture):
    source, developer, bridge, destination = installer_fixture
    fallback = developer / "Workflow Integrations" / "Examples" / "SamplePlugin" / "WorkflowIntegration.node"
    fallback.parent.mkdir(parents=True)
    bridge.rename(fallback)
    result = run_install((source, developer, fallback, destination))
    assert result.returncode == 0, result.stderr
    assert (destination / "WorkflowIntegration.node").read_bytes() == fallback.read_bytes()


@WINDOWS_ONLY
@pytest.mark.parametrize("target", ["source", "source-child", "sdk", "sdk-child", "ancestor"])
def test_source_sdk_and_ancestor_destinations_are_rejected(installer_fixture, target):
    source, developer, bridge, destination = installer_fixture
    paths = {"source": source, "source-child": source / "nested", "sdk": developer,
             "sdk-child": developer / "nested", "ancestor": source.parent}
    result = run_install((source, developer, bridge, paths[target]))
    assert result.returncode != 0
    assert bridge.read_bytes() == pe_header()
    assert (source / "manifest.xml").read_text(encoding="utf-8") == manifest()
    assert not destination.exists()


@WINDOWS_ONLY
def test_running_resolve_is_a_clear_failure_without_install(installer_fixture):
    result = run_install(installer_fixture, prefix="function Get-Process { return [pscustomobject]@{ ProcessName = 'Resolve' } }")
    assert result.returncode != 0
    assert "Close DaVinci Resolve" in result.stderr
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
def test_wrong_platform_is_rejected_without_install(installer_fixture):
    env = os.environ.copy()
    env["OS"] = "not-windows"
    result = run_install(installer_fixture, env=env)
    assert result.returncode != 0
    assert "runs on Windows" in " ".join(result.stderr.split())
    assert not installer_fixture[3].exists()


@WINDOWS_ONLY
@pytest.mark.parametrize("linked_path", ["source", "destination", "sdk"])
def test_junctions_are_rejected_without_touching_target(installer_fixture, linked_path):
    source, developer, bridge, destination = installer_fixture
    junction = source.parent / ("linked-" + linked_path)
    actual = {"source": source, "destination": source, "sdk": developer}[linked_path]
    executable = shutil.which("powershell") or shutil.which("pwsh")
    code = "New-Item -ItemType Junction -Path '" + str(junction).replace("'", "''") + "' -Target '" + str(actual).replace("'", "''") + "' | Out-Null"
    created = subprocess.run([executable, "-NoProfile", "-Command", code], capture_output=True, text=True, timeout=30)
    if created.returncode:
        pytest.skip("This Windows filesystem cannot create a junction fixture")
    replacement = {"source": (junction, developer, bridge, destination),
                   "destination": (source, developer, bridge, junction),
                   "sdk": (source, junction, bridge, destination)}[linked_path]
    result = run_install(replacement)
    assert result.returncode != 0
    assert "reparse points" in result.stderr
    assert (source / "manifest.xml").read_text(encoding="utf-8") == manifest()
    assert bridge.read_bytes() == pe_header()
    # Removing this exact junction does not delete any directory contents.
    junction.rmdir()


def test_installer_public_allowlists_match_package_list():
    ps = INSTALLER.read_text(encoding="utf-8")
    mac = MAC_INSTALLER.read_text(encoding="utf-8")
    start = ps.index("$publicFiles = @(")
    end = ps.index("\n\n", start)
    import re
    ps_names = re.findall(r"'([^']+)'", ps[start:end])
    mac_list = re.search(r"^public_files=\(([^)]+)\)", mac, flags=re.MULTILINE)
    assert mac_list
    assert set(ps_names) == set(mac_list.group(1).split()) == set(PUBLIC_FILES)
    assert len(ps_names) == len(mac_list.group(1).split()) == len(PUBLIC_FILES)
    assert "WorkflowIntegration.node" not in PUBLIC_FILES


def test_mac_installer_requires_native_arm_and_preserves_trust_and_sources():
    script = MAC_INSTALLER.read_text(encoding="utf-8")
    assert '/usr/bin/lipo -archs "$bridge"' in script
    assert "arm64" in script and "sysctl.proc_translated" in script
    assert "/usr/bin/pgrep -x Resolve" in script
    assert "sudo /bin/bash" in script
    assert "--plugin-path" in script
    assert "mkcert" not in script and "security add-trusted-cert" not in script
    assert "rm -rf" not in script and "cp -R" not in script and "cp -r" not in script


def test_windows_installer_never_recursively_deletes_or_copies_or_disables_tls():
    script = INSTALLER.read_text(encoding="utf-8")
    assert "-Recurse" not in script
    assert "Stop-Process" not in script
    assert "-Verb RunAs -WindowStyle Hidden" in script
    assert "$NoUI -or $ElevationAttempted -or -not $defaultDestination" in script
    assert "DtdProcessing]::Prohibit" in script
    assert "0x8664" in script
    assert "WorkflowIntegration.node" in script
