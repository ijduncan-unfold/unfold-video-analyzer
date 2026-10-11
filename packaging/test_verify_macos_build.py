"""Exercise packaging guards without a Mac, user credentials or media."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("verify_macos_build", Path(__file__).with_name("verify_macos_build.py"))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class MacBuildGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.app = root / "Video Analyzer.app"
        resources = self.app / "Contents/Resources"
        for relative in ("desktop-docs/setup-premiere-macos.command", "desktop-docs/macos-common.sh",
                         "plugins/VideoAnalyzer-Premiere-0.1.14.ccx", "desktop-docs/install-resolve-macos.command",
                         "desktop-docs/public-files.json", "resolve-panel/manifest.xml"):
            resource = resources / relative
            resource.parent.mkdir(parents=True, exist_ok=True)
            resource.write_text("public fixture", encoding="utf-8")
        with (self.app / "Contents/Info.plist").open("wb") as destination:
            plistlib.dump({"CFBundleIdentifier": "com.ijduncan.videoanalyzer", "LSMinimumSystemVersion": "15.0"}, destination)
        self.frontend = root / "frontend"
        self.frontend.mkdir()
        (self.frontend / "index.html").write_text("fixture", encoding="utf-8")
        (resources / "frontend/dist").mkdir(parents=True)
        (resources / "frontend/dist/index.html").write_text("fixture", encoding="utf-8")
        self.smoke = root / "smoke.json"
        self.report = {"status": "ok", "frontend": "ok", "local_media": True,
                       "google_configured": False, "transport": "https", "certificate_verified": True,
                       "editor_hosts": ['premiere', 'resolve']}
        self.write_report()

    def write_report(self):
        self.smoke.write_text(json.dumps(self.report), encoding="utf-8")

    def verify(self, architecture="arm64\n"):
        with patch.object(verifier.subprocess, "check_output", return_value=architecture):
            verifier.verify(self.app, self.frontend, self.smoke)

    def test_public_arm64_bundle_passes(self):
        self.verify()

    def test_http_or_configured_credentials_fail(self):
        for change in ({"transport": "http"}, {"certificate_verified": False}, {"google_configured": True}):
            with self.subTest(change=change):
                self.report.update(change)
                self.write_report()
                with self.assertRaisesRegex(ValueError, "HTTPS"):
                    self.verify()
                self.report.update(transport="https", certificate_verified=True, google_configured=False)

    def test_intel_bundle_fails(self):
        with self.assertRaisesRegex(ValueError, "Apple Silicon"):
            self.verify("x86_64\n")

    def test_private_files_and_media_fail(self):
        for name in (".env.local", "rootCA-key.pem", "premiere-token.txt", "editor-service.json", "editor-service.lock", ".editor-service.json.lock", ".editor-service.json.test.tmp", "library.sqlite3", "source.mp4", "WorkflowIntegration.node"):
            with self.subTest(name=name):
                fixture = self.app / "Contents/Resources" / name
                fixture.write_text("private fixture", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "private"):
                    self.verify()
                fixture.unlink()

    def test_stale_frontend_fails(self):
        (self.frontend / "index.html").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frontend"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
