# Video Analyzer for Premiere — preview

An installable UXP panel for **Premiere Pro 26.3 or newer**. It connects to the local Video Analyzer companion, loads selected original clips or bins from the Project panel, analyzes on explicit request, searches saved clips and dialogue, shows the full app’s thumbnails, opens source moments, creates trimmed native subclips, inserts them at the timeline playhead, adds source markers, and exports SRT.

The panel and desktop companions are a development preview. Native API behavior is covered by injected host tests; installation, Source Monitor timing, markers and metadata sync still require acceptance inside Premiere.

Use **Sync metadata to Premiere** to populate project Description, Scene, Shot, Tape Name and Log Note where empty or previously written by this integration. Other values get named Video Analyzer fields; **Complete analysis JSON** preserves all analysis and timed dialogue. Human edits are preserved. Enable the custom fields with Project panel **Metadata Display**. This uses project metadata; Premiere's own metadata linking preferences can mirror linked standard fields into file XMP. See [the complete mapping and transcript limits](../../docs/EDITOR_METADATA.md). SRT and searchable dialogue do not substitute for a word-aligned native speech transcript.

## Install and connect on Windows

1. Install `VideoAnalyzer-0.1.0-preview-windows-x64.exe`. Keep its FFmpeg setup option enabled if media tools are not installed. This retrieves the separate FFmpeg package using Windows Package Manager.
2. Open **Video Analyzer** from the Start menu. It runs in the tray and opens its browser interface. Configure your Gemini key in **Settings**; it is stored in your local user data and used by the panel.
3. Right-click Video Analyzer's tray icon and choose **Install Premiere integration** (also available in the Windows portable app). Alternatively, use the Start menu's **Install Premiere panel** shortcut, or double-click `VideoAnalyzer-Premiere-0.1.14.ccx`. Creative Cloud Desktop handles the plugin install. Open **Window → UXP Plugins → Video Analyzer** in Premiere.
4. Start the companion and open the Premiere panel. It finds the private local connection automatically, with no token to copy. If Premiere opens first, the panel retries until the companion starts. **Connect** retries immediately; **Disconnect** pauses automatic reconnection.
5. Select original media clips or a bin in Premiere's **Project** panel. Click **Load Premiere selection**. Import does not start billable analysis.
6. Loading a previously analyzed source restores its saved results, including analysis made in the full app. **Reload saved results** reloads results without starting a billable request. Choose **Shots and tags** or **Full analysis**, then click **Analyze**. Shots and transcript lines appear as saved analysis sections finish. Closing the panel leaves the companion's analysis running.
7. Search, click a result to open it in the **Source Monitor**, or click **Add source markers** to write markers for the currently displayed results. Markers are one undoable operation; repeated identical exports skip existing Video Analyzer markers. **Export SRT** includes all available dialogue, regardless of search.

Imported sources are referenced by their original local paths. They are never copied, renamed, or deleted by this integration. Removing a project removes its library record and app-owned derived files. Project/item IDs and source paths are checked before native actions. Saved results remain readable when the source changes or goes offline. The picker identifies those entries, and native edits are disabled until the current source is analyzed; a matching filename alone does not establish that footage is unchanged.

## Install and connect on Apple Silicon Mac

Use the Apple Silicon ZIP/DMG from the [Mac build workflow](https://github.com/ijduncan/video-analyzer/actions/workflows/macos-build.yml). The companion defaults to **https://localhost:8443**. Run the supplied FFmpeg setup and **Prepare Premiere on Mac** commands, which create a locally trusted certificate on your computer, then open the companion, configure Gemini, install the CCX, and open the Premiere panel. It discovers the running companion automatically.

If first launch finds missing certificates, the app offers to open the explicit setup helper in Terminal. macOS controls the trust prompt. The app never disables certificate verification, falls back to insecure HTTP for Premiere, or ships another computer's certificates. Intel builds are outside this preview.

The complete install, source-build, and native acceptance steps are in [the Mac handoff](../../docs/MAC_HANDOFF.md).

## Run from a checkout

Start the local companion (or normal server on `127.0.0.1:8000`), with Gemini configured in the ignored `.env.local`. Server startup publishes the private same-user connection descriptor used by both editor panels.

Add `manifest.json` to UXP Developer Tool 2.2+, enable Premiere's developer mode, and load the panel. Generate a private-distribution package with:

```powershell
./.venv/Scripts/python.exe scripts/package-premiere.py
node --test integrations/premiere/tests/*.test.cjs
```

The package contains only six allowlisted public panel resources. It contains no Gemini keys, pairing tokens, databases, footage, or user preferences. The Adobe UXP Developer Tool is the recommended packager for a release; this preview uses the documented ZIP-based CCX format.

## Scope and limits

- Windows uses HTTP loopback port 8000; Apple Silicon Mac uses trusted HTTPS loopback port 8443. The companion must run separately; the UXP panel does not bundle Python or run the engine inside Premiere. A hosted browser service would require a separate deployment and access model.
- Discovery uses `localhost` for Premiere's network permission matcher, including when the companion advertises `127.0.0.1`. The manifest allows only the local HTTP/HTTPS services; endpoint validation also enforces the two configured ports.
- Mac HTTPS setup is included using mkcert and an explicit local trust action. Mac panel networking, Creative Cloud installation, and native editing behavior still require acceptance on the user's Mac. Developer ID signing and notarization remain release work.
- Premiere 26.3 is required because Source Monitor seeking was added in that release. The adapter feature-detects required APIs and reports failures instead of claiming a successful edit.
- Original Project media only: timeline clips, nested sequences, merged clips, multicam items, and changed frame-rate interpretation are not supported in this first pass. Source-media seconds are used, not timeline offsets. Proxy/original alignment and nonzero source timecode must be checked in Premiere acceptance testing.
- Shot boundaries and Gemini dialogue timing remain approximate. Native navigation does not make the analysis frame accurate. Review source markers/subtitles before delivery.
- Up to 100 clips can be loaded per selection. Results show 100 at a time, expanding to 500. Marker export applies only to visible results, up to 500 per operation. Search can cover saved metadata and dialogue; it is keyword search.
- Analysis and the saved database persist in the companion. Clip, tab, searches, and analysis type persist in the panel per Premiere project. The panel reads the companion's private connection descriptor using UXP filesystem access. Credentials stay in panel memory, are never saved in preferences, and are refreshed when reconnecting. Previously saved pairing credentials are removed. The manifest requests filesystem access to read the fixed same-user discovery file; connection never uploads or copies original media.
- **Add to timeline** creates a frame-aligned native subclip with video/audio and performs an undoable insert on V1/A1 of the open timeline, shifting later footage. **Prepare to drag** creates/reuses that subclip and opens it in the Source Monitor; drag it from Premiere’s Project panel or Source Monitor to the desired track. Adobe’s documented third-party panel drag payload imports whole files and has no trimmed-range fields, so direct dragging of a trimmed result card is not enabled. Master clip marks and original media are preserved. The current native feature set does not create caption tracks or expose the visual match-cut workspace in the panel. Use **Open full analyzer** for visual matching and broader analysis/export views. Direct card-to-timeline drag requires Premiere 27.0; the documented payload accepts whole source files and does not carry trimmed ranges. The current panel uses native subclips for accurate range transfer.

## Acceptance before release

On an installed Premiere 26.3+ build, install the CCX, verify automatic connection, load a small local Project clip, choose an analysis type, and verify progressive results. Check source jumps at zero and known times with embedded source timecode and fractional frame rates; confirm marker start/duration, undo, duplicate skipping, offline/relinked media, and project switching. Export SRT and inspect/import it in Premiere. Verify reopening both apps restores saved results and uninstall preserves original footage.

Windows connection acceptance: installed in Premiere 26.5.2 and verified automatic discovery, authenticated connection, disconnect, and reconnect against the running portable companion. This check used Premiere's home screen without an open project; native clip editing and Mac acceptance remain separate checks.

Windows saved-analysis acceptance: loaded the previously analyzed C0002.MP4 into an isolated test project in Premiere 26.5.2 and confirmed its saved clip and shared thumbnail appeared without a new analysis request. Timeline insertion reproduced a concrete failure: newly created subclips returned `-400000` for unset user In/Out marks. Panel 0.1.13 checks hard media start/duration in that case (requires the Media APIs in 26.5), keeps errors visible beside the result and uses Spectrum controls for readable search fields. Reload re-registers the original source to discover saved results and reports empty/running/changed-source states explicitly. Polling does not replace results while native timeline actions are running.

Validation: 89 Premiere JavaScript tests and 70 relevant backend/desktop/Mac packaging tests pass; the rebuilt Windows portable app passes an isolated startup smoke test, and all 371 portable file hashes match its manifest. CCX 0.1.13 installation succeeded. The installed panel connected automatically on the home screen, and the new search field was readable at a narrow 300-pixel panel width. Native acceptance of corrected timeline insertion is still pending: Premiere froze while reopening the saved test project before the panel rendered. The test project was saved before restarting. Do not treat injected API tests as proof that the installed editor accepts the new media-bound readback.

## Official API references

- [Native subclips](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/clipprojectitem)
- [Timeline insert actions](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sequenceeditor)
- [Adobe drag example and its file-only payload](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/dragAndDrop.ts)

- [Premiere UXP](https://developer.adobe.com/premiere-pro/uxp/introduction/)
- [Source Monitor](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/sourcemonitor)
- [Source markers](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/markers)
- [UXP package format](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/package/)
- [Plugin installation](https://developer.adobe.com/premiere-pro/uxp/plugins/distribution/install/)
- [Networking and macOS restrictions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/network/)
- [UXP filesystem permissions](https://developer.adobe.com/premiere-pro/uxp/resources/recipes/filesystem-operations/)

Panel 0.1.14 fixes the changed-source recovery loop: explicit source loads activate the newly registered version, and Analyze current source can re-register and analyze the current original while older saved results remain readable. Older results stay blocked from timeline actions. A missing or relinked original produces an actionable error rather than sending an analysis request for the wrong source.
