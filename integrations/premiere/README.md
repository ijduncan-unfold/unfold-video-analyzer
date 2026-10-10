# Video Analyzer for Premiere — preview

An installable UXP panel for **Premiere Pro 26.3 or newer**. It connects to the local Video Analyzer companion, loads selected original clips or bins from the Project panel, analyzes on explicit request, searches saved shots and dialogue, opens source moments, adds source markers, and exports SRT.

The panel and Windows companion are a development preview. Native API behavior is covered by injected host tests; installation, Source Monitor timing, and marker round trips have not yet been tested inside Premiere on this workstation. The Premiere application is not installed here. Do that acceptance pass before distributing to other editors.

## Install and pair on Windows

1. Install `VideoAnalyzer-0.1.0-preview-windows-x64.exe`. Keep its FFmpeg setup option enabled if media tools are not installed. This retrieves the separate FFmpeg package using Windows Package Manager.
2. Open **Video Analyzer** from the Start menu. It runs in the tray and opens its browser interface. Configure your Gemini key in **Settings**; it is stored in your local user data and used by the panel.
3. Use the Start menu's **Install Premiere panel** shortcut, or double-click `VideoAnalyzer-Premiere-0.1.0.ccx`. Creative Cloud Desktop handles the plugin install. Open **Window → UXP Plugins → Video Analyzer** in Premiere.
4. In the companion browser's Settings, click **Copy Premiere pairing code**. Paste it into the panel and click **Connect**. The tray's **Pair with Premiere** also opens the local code file if clipboard access is unavailable.
5. Select original media clips or a bin in Premiere's **Project** panel. Click **Load selected Premiere clips**. Import does not start billable analysis.
6. Choose **Shots and tags** or **Full analysis**, then click **Analyze**. Shots and transcript lines appear as saved analysis sections finish. Closing the panel leaves the companion's analysis running.
7. Search, click a result to open it in the **Source Monitor**, or click **Add markers** to write markers for the currently displayed results. Markers are one undoable operation; repeated identical exports skip existing Video Analyzer markers. **Export SRT** includes all available dialogue, regardless of search.

Imported sources are referenced by their original local paths. They are never copied, renamed, or deleted by this integration. Removing a project removes its library record and app-owned derived files. Project/item IDs and source paths are checked before native actions. Changed/offline media must be loaded again.

## Run from a checkout

Start the normal local server on `127.0.0.1:8000`, with Gemini configured in the ignored `.env.local`. To show the pairing code intentionally:

```powershell
$env:PYTHONPATH = 'backend'
./.venv/Scripts/python.exe -m app.premiere_setup
```

Add `manifest.json` to UXP Developer Tool 2.2+, enable Premiere's developer mode, and load the panel. Generate a private-distribution package with:

```powershell
./.venv/Scripts/python.exe scripts/package-premiere.py
node --test integrations/premiere/tests/*.test.cjs
```

The package contains only five allowlisted public panel resources. It contains no Gemini keys, pairing tokens, databases, footage, or user preferences. The Adobe UXP Developer Tool is the recommended packager for a release; this preview uses the documented ZIP-based CCX format.

## Scope and limits

- This preview supports Windows localhost on port 8000. The companion must run separately; the UXP panel does not bundle Python or run the engine inside Premiere. A hosted browser service would require a separate deployment and access model.
- macOS companion build scripts exist, but macOS panel networking and installation need validation. Adobe documents restrictions on plain HTTP there; a trusted local HTTPS setup is still needed before claiming Mac plugin support.
- Premiere 26.3 is required because Source Monitor seeking was added in that release. The adapter feature-detects required APIs and reports failures instead of claiming a successful edit.
- Original Project media only: timeline clips, nested sequences, merged clips, multicam items, and changed frame-rate interpretation are not supported in this first pass. Source-media seconds are used, not timeline offsets. Proxy/original alignment and nonzero source timecode must be checked in Premiere acceptance testing.
- Shot boundaries and Gemini dialogue timing remain approximate. Native navigation does not make the analysis frame accurate. Review source markers/subtitles before delivery.
- Up to 100 clips can be loaded per selection. Results show 100 at a time, expanding to 500. Marker export applies only to visible results, up to 500 per operation. Search can cover saved metadata and dialogue; it is keyword search.
- Analysis and the saved database persist in the companion. Clip, tab, searches, and analysis type persist in the panel per Premiere project. Pairing tokens use UXP secure storage when available, otherwise only panel memory.
- The current native feature set does not assemble sequences, modify an existing timeline, create caption tracks, or expose the visual match-cut workspace in the panel. Use **Open full analyzer** for visual matching and broader analysis/export views. Those native editing actions are subsequent integration work.

## Acceptance before release

On an installed Premiere 26.3+ build, install the CCX, pair, load a small local Project clip, choose an analysis type, and verify progressive results. Check source jumps at zero and known times with embedded source timecode and fractional frame rates; confirm marker start/duration, undo, duplicate skipping, offline/relinked media, and project switching. Export SRT and inspect/import it in Premiere. Verify reopening both apps restores saved results and uninstall preserves original footage.

## Official API references

- [Premiere UXP](https://developer.adobe.com/premiere-pro/uxp/introduction/)
- [Source Monitor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sourcemonitor)
- [Source markers](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers)
- [UXP package format](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/package/)
- [Plugin installation](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install/)
- [Networking and macOS restrictions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/)
