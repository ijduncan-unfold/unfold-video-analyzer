"""Check the native public bundle and isolated smoke result before distribution."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess


def verify(app: Path, frontend: Path, smoke: Path) -> None:
    report = json.loads(smoke.read_text(encoding="utf-8"))
    expected = {"status": "ok", "frontend": "ok", "local_media": True,
                "google_configured": False, "transport": "https",
                "certificate_verified": True}
    if any(report.get(key) != value for key, value in expected.items()):
        raise ValueError("Isolated HTTPS smoke verification failed")
    if 'resolve' not in report.get('editor_hosts', []):
        raise ValueError('The bundled Resolve editor API is missing')
    with (app / "Contents/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    if info.get("CFBundleIdentifier") != "com.ijduncan.videoanalyzer" or info.get("LSMinimumSystemVersion") != "15.0":
        raise ValueError("Unexpected app identity or minimum macOS version")
    architecture = subprocess.check_output(
        ["/usr/bin/lipo", "-archs", str(app / "Contents/MacOS/VideoAnalyzer")],
        text=True,
    ).strip().split()
    if architecture != ["arm64"]:
        raise ValueError("The app must be built natively for Apple Silicon")
    resources = app / "Contents/Resources"
    for relative in ("desktop-docs/setup-premiere-macos.command", "desktop-docs/macos-common.sh",
                     "plugins/VideoAnalyzer-Premiere-0.1.19.ccx", "desktop-docs/install-resolve-macos.command",
                     "desktop-docs/public-files.json", "resolve-panel/manifest.xml"):
        if not (resources / relative).is_file():
            raise ValueError("A required public setup resource is missing")
    for source in frontend.rglob("*"):
        if source.is_file():
            bundled = resources / "frontend/dist" / source.relative_to(frontend)
            if not bundled.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(bundled.read_bytes()).digest():
                raise ValueError("The bundled frontend does not match the build")
    forbidden_names = {"premiere-token.txt", "editor-service.json", "editor-service.lock", "desktop-settings.json", "localhost.pem",
                       "localhost-key.pem", "rootCA.pem", "rootCA-key.pem", "WorkflowIntegration.node"}
    forbidden_extensions = {".db", ".sqlite", ".sqlite3", ".mp4", ".mov", ".mkv", ".avi", ".p12"}
    for source in app.rglob("*"):
        if source.is_file() and (source.name.startswith((".env", ".editor-service.json.")) or source.name in forbidden_names or source.suffix.lower() in forbidden_extensions):
            raise ValueError("A private settings, certificate, database or media file entered the bundle")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--smoke", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.app, arguments.frontend, arguments.smoke)
    print("Verified native arm64 bundle, matching frontend, public setup resources and isolated verified HTTPS smoke; no private settings or media found.")


if __name__ == "__main__":
    main()
