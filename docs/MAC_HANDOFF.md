# Apple Silicon Mac handoff

Continue from the personal repository's `feat/footage-library` branch. This preview targets native Apple Silicon (arm64) on macOS 15 or newer. Premiere Pro 26.3+ and Creative Cloud Desktop are required for the Premiere panel. Resolve's integration requires Resolve Studio 20.1+ and an arm64-compatible bridge from the installed SDK; see [the Windows-first Resolve handoff](RESOLVE_HANDOFF.md).

Download the verified [Apple Silicon preview artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625515/artifacts/11684034232) built from `833444a`, including the automatic Resolve connection update. Update both the companion and integration; the earlier `d355f09` artifacts still require manual pairing. The preview includes the app ZIP/DMG, Premiere CCX, public Resolve ZIP, build metadata, and checksums. All four download checksums, arm64 resources, and isolated verified HTTPS smoke checks passed. Mac GUI, the installed Mac SDK bridge, and native Mac editor acceptance remain pending.

## Install the preview

1. Download the **Apple Silicon Mac preview** [artifact from the verified build](https://github.com/ijduncan/video-analyzer/actions/runs/38090625515/artifacts/11684034232), or a newer successful run for `feat/footage-library` from the [Mac build workflow](https://github.com/ijduncan/video-analyzer/actions/workflows/macos-build.yml). GitHub artifact downloads require a signed-in account. Extract the artifact, then open its DMG.
2. Drag **Video Analyzer.app** into Applications. Keep the accompanying setup commands and Premiere CCX package available.
3. Run the supplied **Install FFmpeg** command if FFmpeg is missing. The helper uses native Homebrew; if Homebrew is missing, it explains how to install it first. FFmpeg is fetched separately and is not bundled in the app.
4. Run **Prepare Premiere on Mac**. It creates a certificate for this Mac and installs its local development CA into the macOS system trust store using mkcert. macOS may ask for your administrator password. If you start setup from the app, macOS may also ask to let Video Analyzer open Terminal. This is an explicit local setup action; the build never changes your Keychain. The certificate covers `localhost`, `127.0.0.1`, and `::1`.
5. Open **Video Analyzer**. Its menu bar icon owns the local engine. The browser UI opens at **https://localhost:8443**. If certificates are missing, the first-start dialog offers the same setup action. Once setup finishes, reopen the app.
6. In the browser UI's **Settings**, save your Gemini key.
7. For Premiere, click **Copy editor pairing code** in Settings. Open the accompanying `.ccx`, or use the app menu's **Install Premiere panel**. Creative Cloud handles installation. In Premiere, open **Window → UXP Plugins → Video Analyzer**. Confirm the panel's service is **https://localhost:8443**, paste the pairing code, and connect.

For Resolve, close Resolve and open **Install Resolve on Mac.command**, or choose **Install Resolve integration** in the app menu. The helper copies this Mac's own SDK bridge and refuses Intel-only modules. Start the companion, reopen Studio, and choose **Workspace → Workflow Integrations → Video Analyzer**. The panel discovers the companion and connects automatically without a pairing code. The existing **Prepare Premiere on Mac** command configures the companion's shared HTTPS endpoint for both editors.

The preview uses ad hoc code signing rather than a Developer ID certificate and is not notarized. If Gatekeeper blocks this build, use Apple's per-app **System Settings → Privacy & Security → Open Anyway** flow after the first launch attempt. [Apple's instructions](https://support.apple.com/en-us/102445). Do not disable Gatekeeper globally or certificate verification.

## Build and continue development on the Mac

Use a native Terminal session with Rosetta disabled:

```sh
git clone --branch feat/footage-library https://github.com/ijduncan/video-analyzer.git
cd video-analyzer
bash scripts/setup-desktop-macos.command
```

The setup command installs native dependencies, runs the checks, and builds the app. It creates a separate build environment under `build/desktop/venv-arm64`, exercises the frozen engine with a temporary TLS certificate and empty test database, and produces a ZIP/DMG and the CCX under `dist/installers`. To rebuild later:

```sh
bash scripts/build-desktop-macos.sh
```

Neither build needs a Gemini key. Certificates, private keys, pairing codes, environment files, analysis databases, and footage are excluded from the artifacts. For a manual source launch after preparing HTTPS:

```sh
PYTHONPATH=backend build/desktop/venv-arm64/bin/python -m app.desktop
```

The normal Mac app requires verified HTTPS. `--http --port 8000` is available for standalone development, but Premiere's Mac panel refuses HTTP. Do not use `curl -k` or alter Adobe's app to bypass trust.

## Acceptance on the Mac

Start with a short original local clip selected in Premiere's **Project** panel. Load the selection and confirm import does not start analysis. Select **Full analysis**, click **Analyze**, and confirm shots and dialogue appear progressively. Search both tabs, confirm highlighted matches, and click results to check source positions against known moments.

Export visible shot/dialogue markers. Confirm their positions and durations in the Source Monitor, undo the operation, repeat it, and check duplicate skipping. Export SRT, inspect its timing, and import it into Premiere. Include a clip with embedded nonzero source timecode and a fractional frame rate; these source timing semantics still need native acceptance.

Switch Premiere projects while a search is pending, relink or take footage offline, and verify stale results cannot navigate or add markers. Quit and reopen both apps; check saved analysis, selected clip, tab, search, and mode. Check the menu bar's Open, Install panel, Pair, Open data folder, and Quit actions. Confirm closing the browser leaves analysis running and Quit stops the engine. Removing a library project must retain the original media.

The panel supports original Project clips/bins. Timeline selections, retimed/interpreted clips, nested sequences, multicam, caption-track creation, and timeline assembly are outside this preview. Visual match cuts remain in **Open full analyzer**.

## Data and troubleshooting

User data is in `~/Library/Application Support/VideoAnalyzer`: `library.sqlite3`, `media/` (app-owned previews/cache), `desktop-settings.json`, `premiere-token.txt`, the private running-service `editor-service.json`, `tls/`, and `desktop.log`. The app's resource bundle is separate. Do not transfer editor credentials or certificates between computers. Re-enter your Gemini key on the Mac and load the Mac source files; Windows media paths will not resolve there.

If the service cannot start, inspect `desktop.log` and check whether port 8443 is already used. The app never terminates another service. Certificate errors require rerunning the explicit setup and restarting both the companion and Premiere. If FFmpeg is not detected, quit and reopen the app after installing it. Check macOS file-access prompts when importing from Documents, Desktop, Downloads, or external storage.

For a continuing Codex session on the Mac: **Read docs/MAC_HANDOFF.md and docs/RESOLVE_HANDOFF.md and continue Apple Silicon acceptance for the desktop companion and editor integrations. Preserve originals, credentials, and the user's existing library. Validate the installed SDK, app, and native editor behavior before describing the preview as ready for public release.**
