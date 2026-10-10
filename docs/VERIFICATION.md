# Verification record

## Result

The rebuild passes its local automated suite and live Gemini compatibility checks. The browser workflow was exercised with actual synthetic media from import through analysis, review and export. Resolve Studio on Windows has also been exercised through its native plugin using an isolated synthetic fixture, as recorded below. Comparative accuracy on customer footage and broader editor round trips remain unverified.

## Initial rebuild checks

| Check | Result |
|---|---|
| Backend/media/export tests | 144 tests passed, with 50 passing parameterized subtests |
| Frontend TypeScript and production build | Passed; Vite production bundle generated |
| Frontend ESLint | Passed |
| Diff whitespace check | Passed |
| Production frontend dependency audit | No vulnerabilities reported at verification time |

Tests use isolated temporary storage and mocked provider responses where appropriate. Actual ffmpeg tests cover local import, ffprobe facts, poster creation and HTTP range playback. Other regressions cover persistent metadata, filters, shot annotations, range validation, model usage, archived reviews, concurrent deletion claims, early cancellation, safe media cleanup and export structure. Two dependency deprecation warnings from Starlette/httpx remain; they do not fail the suite.

The GitHub Actions workflow repeats tests/build/lint on Ubuntu with ffmpeg. An initial publishing attempt using `ijduncan-unfold` was denied access to the personal repository. Publishing subsequently used the authorized personal account; successful application and desktop CI runs are recorded in the later verification sections below.

## Live Gemini compatibility

Both presets ran through the real Files API and `generate_content` with `gemini-3.8-flash`, using the replacement key in ignored `.env.local`. The controlled fixture was a six-second 640×360, 24fps silent video: red with `RED SAMPLE` for three seconds, then blue with `BLUE SAMPLE` for three seconds.

| Measure | Fast index | Full creative analysis |
|---|---:|---:|
| Completion | Passed | Passed |
| Logged shots | 2 | 2 |
| Reported intervals | 0–3s, 3–6s | 0–3s, 3–6s |
| Correct overlay strings | 2 / 2 | 2 / 2 |
| Invented spoken transcript | None | None |
| Input tokens | 3,806 | 6,957 |
| Billable output tokens, including thinking | 1,940 | 3,729 |
| Thinking tokens included above | 945 | 1,611 |
| Estimated API cost | $0.010129 | $0.019202 |

Full creative analysis also produced two scene insights, a summary and the related-shot pass. Both runs left generated observations unreviewed. Synthetic provider uploads were deleted after verification. The earlier key's interrupted smoke run was superseded; the evidence above comes from the replacement key.

These results establish the current SDK/request/schema compatibility and expected behavior on an intentionally simple fixture. They do not measure recognition accuracy on real productions, prove exhaustive cut detection, or rank Gemini above competitors. Costs are dated estimates from recorded token usage, not billing guarantees.

Sanitized local evidence is saved under `artifacts/qa/provider-smoke.json` and is excluded from Git together with test media.

## Browser and API workflow

The production frontend was served by FastAPI on loopback. Browser inspection found meaningful content, no framework error overlay and no uncaught page errors. Desktop and 390px mobile inspector layouts were visually inspected.

1. Imported a real six-second 25fps synthetic test-pattern MP4 through the file input. Verified its actual poster, codec facts, source timecode and playable preview.
2. Entered client/project/collection/tags, set review and rights status, and saved. Reloaded the browser and confirmed persisted metadata and project/filter facets.
3. Searched a saved tag and applied cleared-rights filtering. The correct asset remained in results.
4. Downloaded JSON from the inspector; the observed HTTP request returned 200.
5. Started fast indexing from the UI. The observed POST returned 202, the background job completed, and the indexed-shot count and inspector updated through polling.
6. Opened the actual model-generated shot and saved a human tag/review decision. The observed annotation PATCH returned 200.
7. Retrieved JSON, CSV, XMP, EDL and FCPXML exports through the live API; all returned 200. SRT correctly returned 422 because the source has no transcript, rather than turning visual descriptions into captions.
8. Found the saved human tag through shot search. Removed the test asset through the inspector confirmation; DELETE returned 200. Verified the library record, uploaded copy and thumbnails were removed while the separate source fixture remained intact. The working library was left empty for real footage.

This separate UI test used 640×360 synthetic test-pattern footage with embedded 25fps source timecode and produced one continuous shot. Its estimated model cost was $0.006981. Local screenshots and resulting sidecars are saved under `artifacts/qa/` and excluded from Git.

## Minimal library interface follow-up

Removed the FRAME branding, sidebar, dashboard totals, promotional copy, and onboarding panels. The library now opens with search, Videos/Shots, collapsed filters, Import, and Settings. Secondary metadata and analysis details use disclosure sections.

Production build and ESLint passed. Browser checks at desktop and 390px mobile width covered import, preview, tag saving, keyword search, filter disclosure, analysis controls, and settings. JSON export returned HTTP 200; the automated browser's download capture was canceled, so this check does not establish a saved browser download. The disposable synthetic asset was removed through the UI and the library was left empty. No additional provider calls were needed.

## Progressive analysis follow-up

Indexing now publishes shots and thumbnails after each 30-second processing section. The live provider test used a separate 42-second synthetic source and isolated database: its first ten shots were saved at 16.03 seconds, thumbnails appeared at 16.31 seconds, and all fourteen shots finished at 23.09 seconds. These are observed fixture timings, not a latency guarantee for production footage. The remote test upload was deleted. See [progressive analysis](PROGRESSIVE_ANALYSIS.md) for the implementation, evidence and remaining limits.

Browser verification replayed those saved partial/final snapshots through a separate local server and database, with the provider key disabled. Ten shots and their thumbnails were available while status remained analyzing; playing shot ten sought to 27 seconds and started playback. Unsaved tags, the selected shot and active tab survived the fourteen-shot refresh. With both a project filter excluding all assets and a query returning no shots, polling continued and a newly matching shot appeared automatically. A simulated error displayed saved partial shots with playable cards. The isolated fixture was restored and the QA server/browser stopped. Screenshots are retained under ignored `artifacts/qa/progressive-ui-*.png`. At short viewport heights, the inspector scrolls to reveal live shot cards below the progress controls.

The existing 152-second user asset completed under the previous pipeline. After the local server update, its saved three shots and metadata remained available; it was not reanalyzed during verification.

## Full-window workspace and midpoint thumbnails

Selected footage now occupies the full browser canvas. Desktop uses a large player beside a thumbnail shot grid, with Shots, Analysis and Metadata tabs; narrow screens stack these areas. The library remains mounted but hidden, preserving search/filter/page state and restoring focus and scroll on return. Generated analysis precedes its settings, and live progress stays beside the player.

The isolated browser fixture was inspected at desktop and 390px mobile width. The mobile page and workspace both measured 390px with no horizontal overflow. Captured desktop evidence covers partial shot cards, playback selection, an unsaved metadata draft and restored library filters; no browser page errors were reported. The disposable fixture was restored and its server/browser stopped. Final frontend build and lint passed.

Thumbnail extraction now chooses `start + (end - start) / 2`. Legacy invalid end timestamps fall back to a valid start; invalid starts are skipped. Images are replaced atomically, and URLs include file modification time so regenerated frames replace cached thumbnails. A real ffmpeg red-to-blue fixture verifies that extraction chooses the blue midpoint rather than the red opening. Additional tests cover fractional timing, fallbacks, extraction failures and thumbnail cache invalidation. The full automated suite passed 89 tests and 52 subtests.

After both real assets finished analysis, their 51 thumbnails were regenerated locally: 48 for the Half-Life teaser and three for Timeline 4. Their analysis and metadata were preserved, with no additional Gemini calls. The updated teaser thumbnails were visually checked in the full-window workspace.

## Analysis type at the primary action

The Analyze control now includes a native dropdown for Shots and tags or Full analysis. The selected type stays visible, and both controls are disabled while the video is processing. A new saved run revision updates the selected mode and sampling, while ordinary polling preserves unsent choices. The duplicate mode selector in the secondary settings was removed.

The isolated browser fixture captured `flash_only` for Shots and tags and `flash_pro` for Full analysis. Both used the existing request contract. The 390px layout had no horizontal overflow. Production build and lint passed.

A preliminary browser interception rule did not block a real Timeline 4 test request. It was cancelled before any successful processing section or result replacement. The original three shots and completed-result status were retained; its prior cost estimate had been overwritten by the attempted run and is now shown as unavailable. The remaining request checks used an isolated server that captured requests without provider access. Sanitized incident evidence is in ignored `artifacts/qa/cancelled-mode-test.json`.

## Project search and export workspace

Each imported video is now a logical project in the persistent library. Shots view requires a project, and the full-window video workspace searches that import's shot metadata, overlapping transcript cues, section notes, summary, custom analysis and human metadata. Match labels distinguish shot observations from section- or video-wide context. Sections also provide a direct link to their shots. The optional agency Project label remains independent of the import ID.

The dedicated Export tab fills the workspace and offers whole-video or selected-shot JSON, CSV, XMP, SRT, EDL and Final Cut Pro XML. Format availability uses the same validators as downloads. Empty or invalid selections cannot silently export the whole asset. Changed analysis versions block stale selections; pagination and section filters reset appropriately on a new run. Source-relative in/out times and ranges can be copied individually or together. Selected JSON keeps applicable section context explicitly labeled, while selected subtitles retain complete overlapping cues at their original times.

Browser verification used two isolated eight-second local videos with separate project IDs. The fixture server had a blank provider key and rejected API writes and legacy analysis routes. `tungsten CU` found only the Amber project's second shot; switching to Cobalt produced no matches. A section search found its two related shots, and the section's View shots action cleared the query and opened exactly those shots. Multi-selection and the per-shot Export action were exercised.

Actual browser downloads saved a single-shot JSON and FCPXML file. Parsed JSON contained only shot 2 at 00:02.000–00:04.000; the XML contained exactly one asset-clip, a two-second duration and the correct embedded source-timecode offset. Copy range returned `00:02.000 - 00:04.000`. The silent fixture disabled SRT with an explanation, and an empty selected-shot scope disabled download. Initial automated download attempts were blocked by the headless browser's download setting; allowing downloads in that isolated browser resolved them. The application download code required no workaround.

The export layout was inspected at 1440px and 390px. At 390px, both the document and workspace measured 390px without horizontal overflow. No uncaught browser page errors were reported. Screenshots and downloaded fixture files remain under ignored `artifacts/qa/`.

An isolated progressive replay expanded 150 shots to 180 without moving page two or its selection. Replacing the analysis with five shots reset the browser to page one, cleared a removed section filter and marked the previous export selection stale. Download and copying were disabled for that stale selection. Growing the results again did not jump to an old page. The export table's page clamp and a metadata draft's round trip through Export and polling were also verified. Both fixture records were restored exactly; the QA browser and helper server were stopped.

Final automated validation passed 144 tests and 50 subtests, plus the frontend build and ESLint. Search regressions cover section/import isolation, transcript interval boundaries, custom analysis and private fields. Color terms use word boundaries so red does not match armored or inspired. A live read-only query against the teaser returned ten matches, led by the actual red-canyon shot, instead of matching all 46 shots through incidental substrings.

The existing teaser's average frame-rate fraction was `100755000/4202323`, slightly different from its declared nominal `24000/1001`. Local ffprobe inspection verified cumulative presentation timing for all 2,239 packets within one `1/90000`-second stream tick. The original average remains stored, with separate verified nominal-rate fields. Timeline 4 likewise verified 24 fps across 3,662 frames. Only inspected technical facts were refreshed; fingerprints confirmed that analysis, annotations and all other job fields stayed unchanged. The live teaser's single-shot FCPXML endpoint returned one clip and `1001/24000s` frame duration with explicitly declared file-zero timing. No Gemini calls were used for these changes.

New imports perform a local packet-timing check with a 60-second timeout. Incomplete counts, missing timestamps, drift or irregular timing remain unverified; the NLE adapter blocks them rather than rounding an arbitrary average rate. Fixture tests cover these failure modes and exact rational timing. Installed-editor round trips remain outstanding.

## Premiere panel and desktop companion preview

The UXP adapter and panel passed 48 Node checks covering Project selections, source seeks, undoable markers, duplicate skipping, search and transcript navigation, explicit analysis, progressive polling, persistence, source interpretation, relinks, and stale-response guards. The authenticated loopback API registers originals by reference and blocks changed/offline media before reading results, starting analysis, and entering the provider upload stage after queueing. Deletion tests confirm that original media is retained and editor bindings are removed.

The frontend production build and lint passed. Browser checks with mocked UXP/native operations exercised highlighted shot searches, source jumps, visible-result markers, and transcript switching at widths of 300, 420, and 640 pixels without horizontal overflow or page errors. Separate browser checks exercised desktop key save success/failure, clearing, explicit clipboard pairing, launch-fragment removal, and session-only token storage. These checks made no Gemini calls and used no real credentials.

The Windows frozen executable passed an isolated startup/shutdown smoke check with an empty temporary database. It served the frontend and each referenced JS/CSS asset, detected FFmpeg, and did not inherit a configured developer key. The installer includes the allowlisted CCX package. Development data, environment files, API keys, tokens, and source media are excluded from the package resources.

Premiere is not installed on this workstation: CCX installation, native Source Monitor timing, and actual marker round trips still require acceptance in Premiere 26.3+. The Windows installer is an unsigned preview. Build and install guidance is in `integrations/premiere/README.md` and `packaging/README.md`.

The Apple Silicon Mac preparation adds verified HTTPS on loopback port 8443, an explicit machine-local mkcert setup action, first-start setup guidance, and Mac panel endpoint selection. Portable desktop tests include real TLS handshakes: valid localhost/127.0.0.1 connections succeed; an untrusted CA and wrong hostname are rejected. Tests also cover TLS validation before binding, missing/partial/invalid configuration, temporary certificate cleanup without modifying user certificates, and per-test environment restoration. POSIX path regressions preserve case, Unicode, and literal backslashes in Mac filenames while retaining Windows path normalization. These tests ran on Windows; Mac execution is separately reported by the native build workflow. GUI trust/setup behavior and actual Premiere round trips remain for the user on Mac. See `docs/MAC_HANDOFF.md`.

The [October 10 Apple Silicon CI run](https://github.com/ijduncan/video-analyzer/actions/runs/38069889259) built commit `1384ceb` natively on an arm64 macOS 15 runner. It passed 234 backend tests and 50 subtests, five packaging guard tests, 57 Premiere Node checks, frontend lint and production build. The frozen app served the frontend and assets over verified HTTPS with an isolated empty database, detected local media tools, and reported no configured Gemini key. Bundle checks confirmed arm64, required public helpers/CCX, matching frontend, absence of private settings/media, and strict deep code-signature verification. The app was packaged into a DMG and ZIP with the Premiere CCX. Downloaded package SHA-256 values matched the published checksums; the ZIP executable's Mach-O header independently confirmed arm64. This development preview is ad hoc signed and is not notarized. Interactive installation, Keychain/UXP trust, menu-bar actions and native Premiere timing/marker behavior remain untested.

## Resolve integration and Windows-first packaging

The Resolve preview adds authenticated host-scoped registration to the existing companion and a native Electron Workflow Integration window for Studio 20.1+. Originals are referenced rather than copied, identical source files can reuse analysis across editors, and saved host/project identities remain separate. Analysis is explicit, results populate progressively, and the window searches shots/dialogue, highlights matching words, selects Media Pool clips, copies file-relative times, exports SRT, and adds guarded source markers.

Local verification passed 306 Python tests and 75 subtests, with two real-symlink checks skipped because Windows could not create the required links. Portable link guards passed. Nine additional launcher tests passed in the 38-test desktop suite. Resolve passed 65 Node checks and Premiere passed 57; five portable Mac bundle checks, frontend lint/build, shell syntax, and diff checks also passed. The public ZIP tests cover resource poisoning, invalid paths/identities, executable Mac helper permissions even on Windows builds, and interrupted writes preserving the previous deliverable.

The Resolve browser preview used a mocked native bridge at 600 px and 360 px. Shot/transcript filters, highlighted terms, clip/time actions, and subtitle controls worked without page errors or horizontal overflow. These checks do not substitute for execution inside Resolve.

The Windows x64 frozen executable and Inno installer built locally. An isolated smoke run served the frontend/assets, detected media tools, authenticated both editor APIs, and reported no configured Gemini key. A separate real HTTP test registered a four-second synthetic 24 fps original through the frozen Resolve API, verified its inspected frame cadence, reused the asset from Premiere, rejected an unauthenticated request, and removed its library record while preserving the original bytes. No provider calls or actual user media were used.

Public packages contain only the allowlisted integration resources and helpers. The licensed native bridge is copied from the user's installed Resolve SDK during explicit installation and is never included in our artifacts. Windows fixture checks cover installation, updates, existing-file preservation, incompatible PE architectures, unsafe paths, and failure cleanup. Mac architecture/installation guards are portable syntax/contract checks here; actual SDK arm64 availability remains a native acceptance item.

Resolve 21.0.1 and its Developer SDK are installed on the Windows workstation. The vendor examples confirm the manifest entry, Promise APIs, callback/timeout interface, plugin paths, and Media Pool marker/selection API names. The real installer copied the installed x64 SDK bridge and ten public resources into an isolated temporary folder; its bridge hash matched the original. A source-folder attempt under OneDrive was refused by the reparse-point guard; the successful check used a normal temporary folder. No Resolve application/plugin folders changed. Interactive execution, the Workspace menu entry, selection confirmation, source marker frame semantics, fractional rates/nonzero embedded timecode, and rollback still require native Studio acceptance. There is no documented exact source-viewer seek or marker transaction/undo API in this preview. See [the Resolve handoff](RESOLVE_HANDOFF.md).

The [Windows CI build](https://github.com/ijduncan/video-analyzer/actions/runs/38072683172) passed at commit `b2be764`: 319 Python tests and 75 subtests, five portable packaging checks, 57 Premiere and 65 Resolve Node checks, frontend lint/build, isolated frozen startup for both authenticated editor APIs, and public bundle audit. It produced the Windows installer, Resolve ZIP, Premiere CCX, sanitized build metadata, and smoke report. All five downloaded checksums matched, the installer had a valid PE header, and the Resolve ZIP contained the fifteen expected public resources/helpers with no vendor binary and executable Mac helper permissions. PowerShell error assertions normalize wrapped and CLIXML diagnostics while retaining failure-status and filesystem checks.

The [Resolve-enabled Apple Silicon build](https://github.com/ijduncan/video-analyzer/actions/runs/38072683086) passed at the same commit, including 294 Python tests and 75 subtests (25 platform skips), native arm64, strict code-signature/resource checks, both editor contract suites, frontend checks, and frozen verified HTTPS smoke with both editor APIs. It produced the app ZIP, DMG, Premiere CCX, and public Resolve ZIP. All four downloaded checksums matched; the app ZIP's executable header independently confirmed arm64, and its resources contained the public Resolve panel without a vendor bridge. This verifies the companion and public resources on Mac; the installed Mac SDK bridge and interactive Resolve behavior still need acceptance.

The source companion was restarted only after its analysis and indexing jobs were idle. Its authenticated Resolve endpoint became available, while both saved footage projects, completed shape/composition caches, and configured Gemini key remained intact. These checks were read-only and made no provider calls.

## Initial Windows native Resolve acceptance, October 10 (`d355f09`)

Desktop testing with Astra installed the integration into the actual Windows Workflow Integration folder and opened it in Resolve Studio 21.0.1. The ten public resources matched source hashes and the native bridge matched the local SDK. A separate Resolve project and companion database contained one six-second, verified 24 fps synthetic original, three seeded color shots, and three explicitly synthetic dialogue cues. Provider calls were disabled. The source's SHA-256, size, and modification time remained unchanged.

The Workspace menu, pairing, Media Pool registration, highlighted shot/dialogue search, confirmed clip selection, and source-relative copying worked. Copying the blue shot produced `00:02.000 – 00:04.000`. Native subtitle cancellation worked, and the saved SRT matched all three expected cues while a search was active. Close/reopen required pairing again and restored the selected clip and query. Closing Resolve while the integration remained open also closed the plugin automatically, confirmed through fresh desktop window inspection after ten seconds.

Fresh markers were written at source frames 0, 48, and 96. Native dialogs confirmed starts of 0, 2, and 4 seconds and two-second durations. Repeating the batch added zero and skipped three duplicates. A cue aimed at the manual marker's exact frame 24 added zero and skipped one occupied frame; the manual marker retained its name, one-frame duration, time, and empty notes. The temporary collision cue was restored to the original fixture data. Switching to a separate empty QA project cleared the prior actionable results and disabled Analyze/marker export.

These tests exposed three runtime defects: Electron's ESM launcher did not satisfy the old CommonJS entry guard, SDK timeout setup required initialization first, and the Promise SDK's marker dictionary keys differed from the actual source frames. Commit `87e2e10` fixed startup/initialization; `d355f09` verifies marker occupancy and position through exact frame queries and rolls back only uniquely identified unchanged app-owned markers through custom data. All 83 Resolve Node checks pass, including regressions for the observed shifted keys, empty manual-marker identities, partial writes, concurrent edits, and incomplete cleanup.

After QA, the normal localhost companion was restored. A read-only comparison confirmed the same two original library assets and statuses, zero active jobs, unchanged visual/shape/composition cache counts and revisions, and configured-key presence. No test assets were registered into the user's original library. Credentials and native diagnostic captures were excluded from Git and public packages.

All workflows passed at `d355f09`: [application checks](https://github.com/ijduncan/video-analyzer/actions/runs/38076147028), [Windows preview](https://github.com/ijduncan/video-analyzer/actions/runs/38076147019), and [Apple Silicon preview](https://github.com/ijduncan/video-analyzer/actions/runs/38076147014). Windows passed 319 Python tests and 75 subtests; Mac passed 294 and 75 subtests with 25 platform skips. Both passed five packaging guards, 83 Resolve and 57 Premiere checks, frontend lint/build, and isolated frozen-engine checks. Windows verified HTTP and both editor APIs; Mac verified HTTPS, arm64, signatures, and public resources.

The [Windows artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38076147019/artifacts/11678780721) and [Apple Silicon artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38076147014/artifacts/11678469463) for `d355f09` were downloaded after success. All five Windows and four Mac file checksums matched. The installer had a valid PE header; the Mac executable header confirmed arm64. Both public Resolve ZIPs contained the fifteen expected entries, executable Mac helpers, and all ten resources matching that commit after line-ending normalization. The Mac app's embedded Resolve resources also matched source. Neither public package included the vendor bridge. Those builds superseded the earlier `b2be764` downloads above.

Native testing establishes this limited Windows 24 fps workflow. It does not establish fractional rates, nonzero embedded timecode, native failure-injection rollback, recognition accuracy, or Mac SDK compatibility. Exact source-viewer seeking remains unavailable. Earlier isolated installer and mocked-browser checks above predate this actual installation and execution.

## Automatic Resolve connection, October 10 (`833444a`)

Manual pairing was removed from Resolve's interface. The companion publishes a private per-user discovery file only after authenticated readiness, and the native plugin process verifies the running instance. Bearer authentication and loopback restrictions remain enabled; credentials never enter the renderer or saved preferences. Premiere's manual pairing flow is unchanged.

Astra exercised the pristine installed plugin in Windows Resolve Studio 21.0.1. Opening the Workspace integration connected without input. Close/reopen connected automatically. Stopping the companion changed the panel to **Waiting for app** and hid unavailable controls; restarting the companion restored **Connected** and the correct QA project without clicking Retry. All eleven installed public resources matched source, and the native bridge came from the matching local SDK.

The private Windows discovery folder is under `%USERPROFILE%/.video-analyzer`, which is shared between the packaged development app and native Resolve. Its ACL was checked: inheritance disabled and only the current user's SID granted access. Actual Electron testing also exposed differing path/handle volume identifier widths. Exact BigInt inode/device metadata and a Windows-only path-to-handle normalization fix preserve canonical-path, linked-path, and file-change checks. Tests reject wrong device/inode identities and POSIX mismatches.

Following restart, a read-only check confirmed the descriptor reader, authenticated endpoint, and instance identity agreed; unauthenticated Resolve health still returned HTTP 401. No media loading, analysis, or marker writes were initiated by this acceptance round. The initial library baseline was empty; a subsequent read-only check found an active synthetic-clip analysis, which was left running. Credentials and private diagnostic captures remain excluded from source and packages.

All workflows passed for the exact source commit: [application checks](https://github.com/ijduncan/video-analyzer/actions/runs/38090625313), [Windows preview](https://github.com/ijduncan/video-analyzer/actions/runs/38090625438), and [Apple Silicon preview](https://github.com/ijduncan/video-analyzer/actions/runs/38090625515). Windows passed 357 Python tests and 75 subtests; Mac and application checks passed 332 and 75 subtests with 25 Windows-only skips. Resolve passed all 108 Node checks on Mac/Linux and 107 with one POSIX-permission skip on Windows. Premiere's 57 checks passed. Desktop builds also passed five packaging guards, frontend lint/build, isolated frozen startup, and public resource audits.

The verified [Windows artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625438/artifacts/11683988079) and [Apple Silicon artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38090625515/artifacts/11684034232) supersede the earlier previews. All five Windows and four Mac downloaded file checksums matched. Both integration ZIPs contained sixteen entries with eleven public resources matching `833444a`, executable Mac helpers, and no vendor bridge. The installer had a valid PE header. The Mac app's executable confirmed native arm64, its embedded public resources matched source, and its isolated smoke report confirmed verified HTTPS. Neither isolated build contained a configured Gemini key. Native Mac editor acceptance remains pending.

## Original analyzer removal, October 10

The original analyzer entry in Settings, its separately loaded application shell, timeline/comparison/report views, hooks, stores, and old API client were removed. The current footage workspace is the only browser interface. Obsolete analysis/status/results/comparison/report-export and YouTube-import routes, unused prompts/schemas/services, and the ReportLab/SSE dependencies were removed too.

Current progressive Gemini analysis, full section notes/summary, custom briefs, transcripts, metadata search, shape/composition/color matching, library deletion, and all six workspace export formats remain. The unused automatic related-shot Gemini pass no longer runs; visual match-cut discovery uses its independent pipeline. Historical related-shot results remain readable in saved exports and archived analysis. Eight retained EDL/FCPXML functions moved unchanged into `nle_export.py` and their export regressions use the current library entry points.

Frontend lint/TypeScript/production build and all ten existing match-cut session/freehand tests passed. Every remaining frontend source file is reachable from the application entry point; no old analyzer chunk is built. The local Python regression run passed 324 tests and 75 subtests with two platform skips. Native installer tests were excluded locally because Resolve was open. Five portable packaging guards and thirteen subtests passed. Independent review found no broken imports or export initialization cycle.

The idle source companion was restarted and the browser reloaded. Settings displayed Gemini configuration and model settings with no **Open analyzer** link, and browser error/warning logs were empty. Current health/library/capability endpoints returned HTTP 200; all six retired endpoint groups returned HTTP 404. The existing imported asset and configured-key presence remained available. No analysis was started by this verification.

All workflows passed for `09f2cbb`: [application checks](https://github.com/ijduncan/video-analyzer/actions/runs/38091404564), [Windows preview](https://github.com/ijduncan/video-analyzer/actions/runs/38091404424), and [Apple Silicon preview](https://github.com/ijduncan/video-analyzer/actions/runs/38091404444). Windows passed 356 Python tests and 75 subtests; Mac and application checks passed 331 and 75 subtests with 25 Windows-only skips. Resolve passed all 108 Node checks on Mac/Linux and 107 with one POSIX-permission skip on Windows. Premiere's 57 checks passed. Desktop builds also passed five packaging guards, frontend lint/build, isolated frozen startup, and public resource audits.

The verified [Windows artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38091404424/artifacts/11684261687) and [Apple Silicon artifact](https://github.com/ijduncan/video-analyzer/actions/runs/38091404444/artifacts/11684561256) supersede the `833444a` previews. All five Windows and four Mac downloaded file checksums matched. Both public Resolve ZIPs contained sixteen entries with eleven resources matching source and no vendor bridge; the Mac app's embedded resources matched too. The Windows installer had a valid PE header, and the Mac executable confirmed native arm64 with a verified HTTPS smoke report. Both build dependency inventories exclude ReportLab and `sse-starlette`.

The Mac app's bundled JavaScript/CSS matched the current build byte for byte, with the same entry references and no old analyzer chunk. Its frozen Python module inventory includes `nle_export` and excludes the ten retired modules plus ReportLab and `sse_starlette`. Windows frontend removal was verified from the exact run's Inno compression file list, which matches the current build; the downloaded installer was not unpacked or executed. These packaging checks do not establish native Mac editor acceptance.

## Resolve shots into timelines, October 10

The panel now saves individual or selected shots as native Media Pool subclips, appends them to the current timeline, or creates a selects timeline. Each subclip retains public shot metadata, overlapping dialogue, section notes, and project context in standard searchable fields. Export requests validate a fresh shot snapshot and native source/project identity. Repeat exports reuse subclips and preserve human metadata edits; timeline additions are explicit and are never automatically replayed after uncertain native writes.

Windows Resolve Studio 21 native SDK acceptance used a separate project and six-second 24 fps synthetic original, with seeded metadata and no provider calls. Two 48-frame shots were created and verified at source frames 0–47 and 48–95. Repeat saves reused both items, a new timeline contained both shots, and a subsequent append used the same metadata-bearing subclip. Native desktop inspection showed three video and three audio clips. The original file's bytes/modification time and original clip metadata were unchanged; the prior project was restored by the SDK probe.

Native Media Pool **Metadata Search → All Fields** found the red shot using “circle” and the blue shot using “Uniform test lighting”, a section note retained in Comments. Selecting the blue result confirmed Shot 02, source frames 48–95, 48 frames, 24 fps and two audio channels. Direct dragging from the integration window is not implemented; saved items use Resolve's native Media Pool workflow.

The Resolve contract suite passed 144 checks with one POSIX permission skip on Windows, including multi-shot selection across polling and stale-analysis resets. Backend and public-package regressions passed 315 checks and 44 subtests with two platform skips. The separate installer/package run passed 42 checks and 25 subtests with two platform skips while Resolve was closed. All eleven installed public resources match pristine current source, and the idle companion was restarted with the new shot-package route while retaining the existing imported asset. Broader footage/timebase coverage and native Apple Silicon acceptance remain pending.

## Long-film processing, October 10

Measured local films now use temporary ten-minute uploads at 30 minutes or above, or above 1 GiB. Shot and detailed requests retain 30-second windows; film-wide summaries and custom briefs synthesize bounded saved evidence instead of attaching the whole film. The progress display identifies the current upload part.

Fourteen new checks passed without paid Gemini calls. They cover lazy uploads/reuse, short-video and YouTube routing, a fractional final part, original-file identity changes, failed-part deduplication, cancellation of provider preparation and FFmpeg, owned-file cleanup, original timestamps past one hour, durable results after the first section of a simulated 30-minute run, bounded hierarchical evidence inputs, and usage recording when synthesis parsing fails. A real FFmpeg fixture confirmed a two-second derived part with audio and zero-based timestamps while the original bytes and modification time remained unchanged.

The complete local application/package regression run passed **350 tests and 75 subtests**, with two platform skips; native Resolve installer tests were excluded because the editor was open and no installer changed. Frontend production build and lint passed. These tests establish routing, offsets, persistence, and resource handling, not full-feature provider latency, recognition accuracy, or trailer-selection quality. A paid representative feature-film run remains pending.

The idle localhost companion was restarted with the updated backend and frontend. Health, library, and workspace requests returned HTTP 200; the existing asset remained available and zero analyses were active. No paid analysis or editor write was started during this verification.

## Podcast rough cuts, October 10

Podcast/talking-head rough-cut generation now has separate video/audio analysis, progressive speech excerpts, a saved editable sequence, source playback, and FCPXML/JSON export. Fifteen new contract checks and an isolated live Gemini/browser smoke test passed; see [podcast rough-cut verification](PODCAST_ROUGH_CUT.md#verification-october-10-2026). A representative 20-minute recording and native editor import of this draft remain pending.

The full local regression suite passed 365 tests and 75 subtests, with two platform skips; native Resolve installer tests were excluded because the editor was open and no installer changed. Frontend production build, lint, and ten existing session/freehand checks passed. The private test database, provider diagnostic script, and fixture media stay outside Git.

## Remaining validation

- Recognition/retrieval quality on representative agency and filmmaking footage, including difficult audio, fast montage and multilingual material.
- True word-aligned transcription and exact cut/frame verification.
- Drop-frame and variable-frame-rate NLE adapters; broader fractional-rate editor compatibility.
- Round-trip imports in installed Premiere and Final Cut Pro, plus broader Resolve footage/timebase coverage and Mac native acceptance.
- Shared-workspace authentication/authorization, distributed queue recovery and archive-scale load testing.
- Hosted deployment validation; repository CI runs application checks plus Windows x64 and Apple Silicon preview builds.
