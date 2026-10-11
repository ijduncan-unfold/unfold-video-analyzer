# Desktop companion preview

The installable companion runs the existing FastAPI engine on loopback, opens the
native desktop window, and keeps running in the system tray/menu bar. Premiere and
Resolve integrations connect to this same engine. The web interface runs in pywebview;
native file drops and the file picker pass original local/network paths without copying media. Users do not need to
install Python or Node.js to run the frozen app.

## Windows

The [Windows build workflow](https://github.com/ijduncan/video-analyzer/actions/workflows/windows-build.yml)
produces the companion installer and public integration packages. Download the
artifact from a successful completed run while signed into GitHub.

Desktop preview builds are **manual**: open the Windows or Apple Silicon workflow
in GitHub Actions and choose **Run workflow** for the branch to package. Ordinary
code pushes to `main` and pull requests run Application checks; pushing the same
commit to a feature branch does not start another set of installer builds.

Build on Windows with Python 3.11+, the frontend's Node dependencies, and
[Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
python -m venv .venv
npm --prefix frontend ci
powershell -ExecutionPolicy Bypass -File scripts/build-desktop.ps1
```

The script builds the frontend, freezes the app, smoke-tests it against an empty
isolated database on port 18741, and compiles an installer under `dist/installers`.
Use `-PythonPath` or `-InnoPath` for nonstandard build tool locations. No local
`.env`, database, uploaded media, or token is bundled; the spec has an explicit
resource allowlist. The build fails if the smoke test sees an inherited API key.

The per-user installer installs to `%LOCALAPPDATA%/Programs/Video Analyzer`, adds
Start menu shortcuts, and offers to install the required FFmpeg tools through
WinGet. Its checkbox explains the GPLv3 third-party package and license agreements.
Already available tools are detected. If Windows Package Manager is unavailable,
install [Microsoft App Installer](https://learn.microsoft.com/windows/package-manager/winget/)
and install `Gyan.FFmpeg` before importing video. FFmpeg is not redistributed in
this app bundle; WinGet retrieves the vendor package separately.

The tray offers **Open Video Analyzer**, **Pair with editor**, **Install Resolve
integration**, **Open data folder**, and **Quit**. Closing the desktop window does not stop the engine. The pairing
command opens the local token file for the user to copy into the Premiere integration;
the token is never printed in build logs. Port 8000 must be available: an existing
server is never killed or silently taken over. Uninstall removes the application,
and preserves the user's database/media and original source files.

The installer also includes the Premiere CCX plugin package. Its Start menu
**Install Premiere panel** shortcut opens Adobe Creative Cloud's plugin installer;
Premiere 26.3+ and Creative Cloud must already be installed. Plugin installation
is a separate user action. **Pair Video Analyzer with editor** opens the local
token for Premiere connection settings. Resolve discovers and connects to the local
companion automatically, without a pairing field. Browser Settings also offers
**Copy editor pairing code** when launched by the companion.

The **Install Resolve integration** shortcut starts the separate Resolve installer.
It requires **Resolve Studio 20.1+ on Windows x64**, with Resolve closed. It copies
the allowlisted public plugin resources and the matching x64 `WorkflowIntegration.node`
from the user's own installed SDK into Resolve's shared plugin folder. Administrator
approval may be required. The vendor module is excluded from the companion and
public integration ZIP. No Resolve software is installed by this helper.

## Apple Silicon Mac preview

The Mac preview targets native **arm64** on macOS 15 or newer. Intel and Rosetta builds are excluded.
The [Mac build workflow](https://github.com/ijduncan/video-analyzer/actions/workflows/macos-build.yml)
produces a ZIP and DMG containing `Video Analyzer.app`, the Premiere CCX, the public Resolve integration ZIP, and explicit
FFmpeg/HTTPS setup commands. Downloads require signing into GitHub. See the
[Mac handoff](../docs/MAC_HANDOFF.md) for installation and native acceptance.

From a checkout on an Apple Silicon Mac, use a native Terminal:

```sh
bash scripts/setup-desktop-macos.command
```

This installs native build dependencies, runs the checks, and produces the preview.
For subsequent builds, run `bash scripts/build-desktop-macos.sh`; its default Python
environment is `build/desktop/venv-arm64`.

The companion runs in the menu bar and uses **https://localhost:8443**. Its first
launch offers explicit HTTPS setup if certificates are missing. **Prepare Premiere
on Mac** uses mkcert to trust a local development CA and create this computer's
localhost certificate. This may require a macOS administrator password. The build
and CI never install a CA, change the Keychain, or package certificates/private keys.
The same trusted endpoint serves both editors; the setup command retains its
existing Premiere name. The app retains certificate chain and hostname verification. FFmpeg is a separate
Homebrew prerequisite, with its own supplied setup helper.

User data is in `~/Library/Application Support/VideoAnalyzer`; resources are inside
the app. Finder launches search Homebrew's tool locations. Removing the app retains
user data and original footage. The package includes the public setup helpers and
CCX and Resolve resources before signing, so post-build copying cannot invalidate the app signature.

For **Resolve Studio 20.1+**, close Resolve and choose **Install Resolve integration**
from the menu bar or open **Install Resolve on Mac.command** from the extracted
public integration ZIP. The helper copies the native bridge from the installed
Blackmagic SDK only if it contains arm64. Actual availability and compatibility of
that bridge remain to be checked on the user's Mac; Intel-only bridges and Rosetta
are refused. This installation action does not change Keychain trust.

Automated Mac builds perform a frozen HTTPS smoke test with an ephemeral certificate
and empty temporary database, check arm64/signature/resource structure, and produce
checksums. GUI/menu-bar behavior, Keychain trust, and native Premiere/Resolve operations still
need acceptance on the user's Mac. These development builds use ad hoc signing;
Developer ID signing and notarization remain required for public release. Local
signing configuration is supported without storing signing credentials in the repo.

## Public Resolve integration ZIP

Run `python scripts/package-resolve.py` from the repository root to create
`dist/installers/VideoAnalyzer-Resolve-0.1.0.zip`. Extract the entire ZIP before using
**Install Resolve on Windows.cmd**, its `install-resolve.ps1` helper, or
**Install Resolve on Mac.command**. The `VideoAnalyzer` folder must stay beside the
helpers. The package has an explicit public allowlist, validates identity/version/
entry points, rejects unsafe or linked inputs, and preserves the previous ZIP if
creation fails. Media, keys, pairing tokens, and the vendor SDK bridge are excluded.

The integration offers original-source registration, explicit analysis, shot/dialogue
search, Media Pool selection, copied file-relative ranges, SRT export, and guarded
source markers. It does not provide exact source-viewer seeking or a documented
native marker undo transaction. See [the Resolve handoff](../docs/RESOLVE_HANDOFF.md)
for paths, limitations, and native acceptance on Windows first, then Mac.

## Development and diagnostics

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe backend/app/desktop.py --smoke-test --no-browser --port 18741 --data-dir build/desktop/smoke-data
```

When running from source, set `PYTHONPATH=backend` or use
`python -m app.desktop` from the backend directory. `--no-tray` runs until
interrupted; `--no-browser` suppresses opening the desktop window during diagnostics.

Windows uses Edge WebView2 (normally present on current Windows installations); Mac uses WebKit. Source runs need `requirements-desktop.txt` for the native window. File access is limited to the local app origin, and external links open in the system browser. Vendor RAW decoders are separate from this window runtime.
Logs are rotated in the data folder as `desktop.log`. A successful smoke run
writes `smoke-test.json`, and never calls Gemini or analyzes private footage.

See [third-party notes](THIRD_PARTY.md) for bundled dependency and FFmpeg licensing
requirements. See the Premiere and Resolve integration guides for plugin installation
and host version requirements; this companion installer does not install either editor.
