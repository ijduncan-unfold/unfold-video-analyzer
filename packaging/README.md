# Desktop companion preview

The installable companion runs the existing FastAPI engine on loopback, opens the
browser interface, and keeps running in the system tray/menu bar. The Premiere
panel connects to this same engine. The browser is the UI; users do not need to
install Python or Node.js to run the frozen app.

## Windows

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

The tray offers **Open Video Analyzer**, **Pair with Premiere**, **Open data
folder**, and **Quit**. Closing the browser does not stop the engine. The pairing
command opens the local token file for the user to copy into Premiere's panel;
the token is never printed in build logs. Port 8000 must be available: an existing
server is never killed or silently taken over. Uninstall removes the application,
and preserves the user's database/media and original source files.

The installer also includes the Premiere CCX plugin package. Its Start menu
**Install Premiere panel** shortcut opens Adobe Creative Cloud's plugin installer;
Premiere 26.3+ and Creative Cloud must already be installed. Plugin installation
is a separate user action. **Pair Video Analyzer with Premiere** opens the local
token for the panel's connection settings.

## macOS build path

Build separately on an Apple Silicon or Intel Mac for that architecture:

```sh
python3 -m venv .venv
npm --prefix frontend ci
brew install ffmpeg
bash scripts/build-desktop-macos.sh
```

The script produces a development `Video Analyzer.app` and zip, with the same
engine and tray/menu-bar lifecycle. Install the app by copying it into Applications.
User data goes into `~/Library/Application Support/VideoAnalyzer`. The companion
also searches `/opt/homebrew/bin` and `/usr/local/bin` for FFmpeg because a Finder
launch does not inherit the terminal's PATH.

**macOS packaging has not been tested on this Windows workstation.** A Mac build
runner, Developer ID signing, notarization, and an installer dependency flow are
required for a public Mac release. The Windows preview is unsigned as well;
public distribution should wait for signing and a full dependency notice audit.

## Development and diagnostics

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe backend/app/desktop.py --smoke-test --no-browser --port 18741 --data-dir build/desktop/smoke-data
```

When running from source, set `PYTHONPATH=backend` or use
`python -m app.desktop` from the backend directory. `--no-tray` runs until
interrupted; `--no-browser` suppresses opening a browser during diagnostics.
Logs are rotated in the data folder as `desktop.log`. A successful smoke run
writes `smoke-test.json`, and never calls Gemini or analyzes private footage.

See [third-party notes](THIRD_PARTY.md) for bundled dependency and FFmpeg licensing
requirements. See the Premiere integration guide for UXP installation and host
version requirements; this companion installer does not install Adobe software.
