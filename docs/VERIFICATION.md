# Verification record

## Result

The rebuild passes its local automated suite and live Gemini compatibility checks. The browser workflow was exercised with actual synthetic media from import through analysis, review and export. Comparative accuracy on customer footage and installed-editor round trips remain unverified.

## Automated checks

| Check | Result |
|---|---|
| Backend/media/export tests | 144 tests passed, with 50 passing parameterized subtests |
| Frontend TypeScript and production build | Passed; Vite production bundle generated |
| Frontend ESLint | Passed |
| Diff whitespace check | Passed |
| Production frontend dependency audit | No vulnerabilities reported at verification time |

Tests use isolated temporary storage and mocked provider responses where appropriate. Actual ffmpeg tests cover local import, ffprobe facts, poster creation and HTTP range playback. Other regressions cover persistent metadata, filters, shot annotations, range validation, model usage, archived reviews, concurrent deletion claims, early cancellation, safe media cleanup and export structure. Two dependency deprecation warnings from Starlette/httpx remain; they do not fail the suite.

The GitHub Actions workflow repeats tests/build/lint on Ubuntu with ffmpeg. Publishing was blocked by GitHub: the configured Git identity `ijduncan-unfold` was denied write access to `ijduncan/video-analyzer` (HTTP 403). Its remote run must be checked after the branch is published; local results do not imply remote CI has already run.

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

## Remaining validation

- Recognition/retrieval quality on representative agency and filmmaking footage, including difficult audio, fast montage and multilingual material.
- True word-aligned transcription and exact cut/frame verification.
- Drop-frame and variable-frame-rate NLE adapters; broader fractional-rate editor compatibility.
- Round-trip imports in installed Premiere, Resolve and Final Cut Pro.
- Shared-workspace authentication/authorization, distributed queue recovery and archive-scale load testing.
- Hosted deployment validation; repository CI now runs application checks and the Apple Silicon preview build.
