# Resolve integration handoff

This development preview targets **DaVinci Resolve Studio 20.1 or later**, starting with **Windows x64**, then **Apple Silicon Mac**. The free edition does not provide the Workflow Integration window used here. Resolve supplies the plugin's Electron runtime and native SDK bridge; users do not install a separate Node.js runtime for the plugin.

Blackmagic's [20.1 feature guide](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20.1_New_Features_Guide.pdf) documents the Electron and Promise API updates. The SDK for the installed version is available under **Help → Documentation → Developer** in Resolve; use that local documentation for native acceptance.

The integration loads original Media Pool clips into the local Video Analyzer companion, runs analysis only when requested, searches shots and dialogue, selects source clips, copies source-relative times, exports SRT, and adds guarded source markers. Visual match cuts and the full metadata/export workspace remain available through **Open full analyzer**.

## Windows installation

Use the verified [Windows preview artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625438/artifacts/11683988079) built from `833444a` with automatic connection. The companion and integration must both be updated. All five downloaded checksums and isolated frozen-engine checks passed. The artifact includes the installer, Resolve ZIP, Premiere CCX, smoke report, build metadata, and checksums, and requires signing into GitHub. The older `d355f09` preview verified the native source workflow but still required manual pairing.

1. Get the updated **Video Analyzer companion** and public integration ZIP from the [verified Windows artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625438/artifacts/11683988079), a newer successful [Windows build workflow run](https://github.com/ijduncan/video-analyzer/actions/workflows/windows-build.yml), or build them from source. Workflow artifacts require signing into GitHub. Install the companion and its FFmpeg prerequisite. Start Video Analyzer and configure your own Gemini key in browser **Settings**.
2. Install Resolve Studio 20.1+ and **close Resolve** before installing or updating the integration.
3. Use **Install Resolve integration** from the Video Analyzer Start menu or tray. For the standalone `VideoAnalyzer-Resolve-0.1.0.zip`, extract the entire ZIP and double-click **Install Resolve on Windows.cmd**. Keep the `VideoAnalyzer` folder beside the helper scripts. Administrator approval may be needed for Resolve's shared plugin folder.
4. Restart Resolve Studio and choose **Workspace → Workflow Integrations → Video Analyzer**.
5. The integration connects automatically to the running companion on this computer. Select original local clips in the Media Pool and click **Load selected Media Pool clips**. Loading makes no Gemini analysis call. Choose **Shots and tags** or **Full analysis**, then click **Analyze** for the active clip.

The installer copies these eleven public files into the plugin folder: `manifest.xml`, `package.json`, `main.js`, `preload.js`, `host.js`, `service.js`, `connection.js`, `controller.js`, `panel.js`, `index.html`, and `styles.css`. It copies `WorkflowIntegration.node` separately from **your own installed Resolve SDK**. The vendor bridge is excluded from the public ZIP and companion bundle.

| Windows location | Default |
|---|---|
| Resolve SDK | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer` |
| Installed plugin | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Workflow Integration Plugins\com.ijduncan.videoanalyzer` |
| Companion data and logs | `%LOCALAPPDATA%\VideoAnalyzer` |
| Private running-service discovery | `%USERPROFILE%\.video-analyzer\editor-service.json` |
| Plugin preferences | `%APPDATA%\VideoAnalyzer\Resolve\preferences.json` |

The helper looks for the matching x64 bridge in the SDK's `Workflow Integrations\Examples\SamplePromisePlugin` or `SamplePlugin` folder. It verifies the Windows x64 binary header, refuses linked installation paths and unrelated existing destinations, and requires Resolve to be closed. It updates only this plugin's known files, preserving other files and plugins. Missing or incompatible SDK resources stop installation with an explanation.

Extract the ZIP to an ordinary local folder if a cloud or linked source location is rejected. The installed companion's resources live in its local application folder.

For a nonstandard SDK location, run the extracted helper in PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-resolve.ps1 -DeveloperPath 'D:\Resolve SDK\Developer'
```

Use the SDK belonging to the Resolve installation that will run the integration. Copying a bridge from another version can fail even if its architecture is correct.

## Working with shots and dialogue

The searchable clip picker contains clips registered for the active Resolve project. Project and Media Pool identities are stored separately from Premiere identities. An unchanged original registered in both editors can reuse the same saved analysis.

Shots and transcript cues populate as the companion completes analysis sections. Use the **Shots** or **Transcript** tab to search; matching words are highlighted. **Select clip in Resolve** selects the original Media Pool clip. **Copy times** copies an in/out range measured from the beginning of the original file, such as `00:12.500 – 00:15.250`. It does not convert that range into embedded source timecode or timeline timecode. Exact source-viewer seeking is not available in this preview.

**Export SRT** saves all available timed dialogue through a native Save dialog. The search does not trim the subtitle export. SRT requires actual saved transcript text; shot descriptions are never turned into captions. Gemini dialogue and shot timing remain approximate and need editorial review.

**Add source markers** writes the currently displayed shot or dialogue results to the original Media Pool clip. It requires a verified constant frame rate, the complete original clip, and agreement between Resolve's frame rate/frame count and the inspected file. Existing marker positions are preserved, duplicate Video Analyzer markers are skipped, and subclips, altered timebases, stale projects, or changed sources are guarded. Batches are limited to 500 markers.

Resolve does not expose a documented marker transaction/undo API for this preview. On a failed batch, the integration attempts to remove only the additions it can still identify as its own unchanged writes; it reports incomplete rollback for manual inspection. Review results on a test project before writing a large batch.

Analysis continues in the companion while its process runs, even when the plugin closes. Saved analysis and nonsecret clip/search preferences persist. The panel reconnects automatically after reopening or a companion restart. If the companion is closed, start Video Analyzer; the panel retries automatically.

Local API authentication still runs behind the scenes. The companion publishes a private `editor-service.json` only after its authenticated endpoint is ready. Windows uses `%USERPROFILE%\.video-analyzer`; Mac uses `~/Library/Application Support/VideoAnalyzer`. The Windows profile location keeps discovery accessible across packaged development apps and the native editor. The plugin's native main process reads that file and verifies the running instance. Credentials never enter the interface or saved preferences, and credential changes invalidate old results rather than replaying actions. Keep this file and Gemini keys out of Git, screenshots, and support logs. Premiere still uses its existing manual pairing flow.

Original media is referenced without creating a full import copy. The companion can create thumbnails and derived analysis caches; it does not rename or delete original files. Removing a library project deletes its saved records and app-owned cache files. Uninstalling the companion preserves its user data folder.

## Apple Silicon Mac installation

The companion targets **native arm64 on macOS 15+**. The verified [Mac preview artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625515/artifacts/11684034232) was built from `833444a` with automatic connection; update both the companion and Resolve integration. All four downloaded checksums, arm64 resources, and verified HTTPS smoke checks passed. Earlier `d355f09` downloads still required manual pairing. Native Mac editor acceptance remains pending.

1. Install the updated `Video Analyzer.app` in Applications and prepare FFmpeg through **Install FFmpeg.command**.
2. Run **Prepare Premiere on Mac.command** if local HTTPS is not already configured. Despite its existing name, this setup prepares the companion's shared editor endpoint for both Premiere and Resolve. It uses mkcert and may request an administrator password. Do not disable certificate or hostname verification.
3. Close Resolve and run **Install Resolve on Mac.command** from the extracted integration ZIP, or choose **Install Resolve integration** in the companion's menu bar. Installation into `/Library` may require an administrator password.
4. Start the companion, then open the integration from Resolve Studio's Workspace menu. It automatically discovers the trusted local endpoint **https://localhost:8443** and connects without a pairing code.

| Mac location | Default |
|---|---|
| Resolve SDK | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer` |
| Installed plugin | `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.ijduncan.videoanalyzer` |
| Companion data and plugin preferences | `~/Library/Application Support/VideoAnalyzer` |

The Mac helper accepts only a Mach-O SDK bridge containing **arm64**. Actual availability and compatibility of that bridge in the installed Blackmagic SDK still need verification on the user's Mac. An Intel-only bridge is refused; there is no Rosetta fallback. The public integration ZIP contains no native vendor bridge.

For a nonstandard SDK location, run the extracted helper in a native Terminal:

```sh
bash "Install Resolve on Mac.command" --developer-path "/path/to/Resolve/Developer"
```

The companion preview is ad hoc signed. Developer ID signing and notarization remain public-release work. See [the Mac companion handoff](MAC_HANDOFF.md) for app setup and normal per-app Gatekeeper approval. The Resolve installer does not change certificate trust; HTTPS preparation is a separate explicit action.

## Build from source

From the repository root, package the public integration with:

```powershell
.\.venv\Scripts\python.exe scripts\package-resolve.py
```

On a Mac, use the Python environment from the native companion setup:

```sh
build/desktop/venv-arm64/bin/python scripts/package-resolve.py
```

The output is `dist/installers/VideoAnalyzer-Resolve-0.1.0.zip`. Packaging uses an explicit public resource allowlist, excludes local media/keys/vendor binaries, validates entry points and version agreement, and replaces the deliverable only after ZIP verification. See [companion build instructions](../packaging/README.md) for the Windows installer and native Mac app/DMG.

## Native acceptance

### Initial source workflow at `d355f09`

On October 10, the integration was installed in Resolve Studio 21.0.1's actual Windows plugin folder using its matching local SDK. All ten installed public resources and the native bridge matched their source hashes. Native desktop testing used a separate Resolve project, an isolated companion database, and a six-second, verified 24 fps synthetic original. Its shot metadata and dialogue were explicitly seeded test data; provider calls were disabled.

The Workspace menu opened the integration, pairing and Media Pool registration succeeded, and the original was referenced without a full media copy. Shot and dialogue searches highlighted matching words, clip selection was confirmed in Resolve, and copying the blue shot produced `00:02.000 – 00:04.000`. Native subtitle Save cancellation worked; the saved SRT contained all three expected cues even with a search active. Reopening required the pairing code again and restored the selected clip and search. Switching to a separate empty test project cleared prior results and disabled unavailable actions. Closing Resolve also closed the plugin automatically.

Three fresh source markers were added successfully. Native dialogs confirmed starts at 0, 2, and 4 seconds, each lasting 2 seconds. A repeat added none and skipped all three duplicates. A temporary cue aimed directly at the manual marker's 1-second position was skipped as an occupied frame; native inspection confirmed that marker's name, time, one-frame duration, and empty notes were unchanged. The temporary cue was then removed from the isolated test data.

Testing found and fixed Electron's ESM entry-point behavior, SDK initialization before timeout configuration, and shifted marker dictionary keys. Marker positions are now checked with direct frame queries; guarded cleanup identifies unchanged app-owned markers through custom data. The Resolve contract suite passes 83 checks. See [the verification record](VERIFICATION.md) for remaining coverage.

The normal localhost companion was restored after that test. Both existing footage projects, analysis cache revisions/counts, and configured key presence matched that test's original baseline. Test data was kept separate and no paid calls were made.

### Automatic connection at `833444a`

The updated integration was installed with the matching local SDK in Windows Resolve Studio 21.0.1. All eleven public resources matched pristine source. The Workspace window connected without entering a code or address; closing and reopening it connected automatically again. Stopping the companion changed the open panel to **Waiting for app** and hid unavailable media and analysis actions. Restarting the companion restored **Connected** and the correct QA project without clicking Retry. Resolve and the normal companion were left running and connected.

This round exercised connection behavior without loading media, starting analysis, or writing markers. A subsequent read-only check observed new library activity; the running job was preserved. The original source-workflow checks above remain separate from this connection acceptance.

Native testing also identified two Windows runtime differences: packaged development apps virtualized the usual AppData discovery location, and Resolve's Electron runtime reported different widths for path and handle volume identifiers. Discovery now uses the shared private profile folder. File validation uses exact integer metadata, with Windows volume normalization limited to the path-to-handle comparison; inode, canonical-path, reparse, and before/after identity checks remain in place.

Broader Windows acceptance and native Mac acceptance still include:

1. Verify installation, the Workspace menu entry, connection, close/reopen behavior, and Resolve shutdown. Confirm that the local SDK matches the running Resolve version.
2. Load a few original clips, including dialogue and a common fractional frame rate. Confirm that loading starts no analysis and preserves the originals' bytes, paths, and modification times.
3. Run both analysis presets on short representative material. Confirm progressive shots/transcript, search highlighting, clip selection, copied file-relative ranges, saved results, and the full analyzer link.
4. Export SRT, cancel a Save dialog, and inspect timing/text in Resolve. Confirm that a silent clip does not produce invented subtitles.
5. Add a small marker batch to verified constant-rate footage. Check first/end-frame positions, preserve an existing marker at an occupied frame, repeat the batch to verify duplicate handling, and inspect any reported rollback issue.
6. Change projects during a load or native action. Relink, remove, take offline, or reinterpret a clip; the old results must be blocked until the selection is loaded again. Confirm that subclips and unverified variable-rate clips cannot receive source markers.
7. Close/reopen the integration and companion. Search/clip preferences and analysis should return, and the panel should reconnect automatically without a pairing code. Remove a test library asset and confirm that the original media remains.

For troubleshooting, inspect the companion's `desktop.log` in its data folder. Share error messages and versions with credentials removed. Do not copy a Windows database to a Mac and expect its original-media paths to resolve; register the Mac's local originals and configure that computer's own HTTPS certificates and key.
