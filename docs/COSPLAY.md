# Comic Con cosplay library

This fork adds costume identification to the existing local SQLite footage library. It retains linked originals, thumbnails/scrubbing, saved dialogue, and the bounded background analysis queue. It does not need a new database service or a database migration: new fields live in the existing versioned analysis and annotation records.

## Process the booth footage

1. Add a Gemini key in Settings and import a folder of videos. Create a Comic Con library folder to keep the collection together.
2. Begin with 20–30 varied clips, including group costumes, masks, obscure characters, mashups, and people without costumes. Select those media, choose **Cosplay**, then **Analyze selected**. An individual video's analysis menu also offers Cosplay.
3. Open each result. In Clips → Cosplay, review character, franchise, costume variant, visible details, activity, and possible alternatives. **Edit identifications** can correct fields, add/remove costumes, or mark an identification **Confirmed by you**. Unknown means no supported character name; possible alternatives remain separate.
4. Open library **Filters** and choose Character, Franchise, or Identification. Media results include group clips under every depicted character. Combined character/franchise/status filters must match one costume, not different people in a group. The Clips view can search **All media in this folder** or one source.
5. After validating the pilot and actual model usage/cost, select the remaining footage, or use **Select all N matches**, then queue Cosplay. Selection skips currently active/deleting media. Two jobs run concurrently by default. Retry failures explicitly; a server restart does not automatically resume paid calls.

Cosplay uses the existing primary Gemini model in a focused shot-analysis pass. It skips full cinematography and summary passes. Auto boundaries treats each recording as one take; Detect cuts remains available for edited footage. Spoken dialogue is preserved. Explicit custom instructions still run the existing optional additional pass. Model accuracy, token cost, and end-to-end processing time must be measured on actual footage before processing the full collection.

## Identification and corrections

- Costume recognition describes fictional characters, never the real wearer's identity or personal attributes. New model output is always suggested or unknown, never confirmed.
- Every visible costume can have its own entry. A character may occur more than once in a group; badges and filter choices are deduplicated.
- Common names normalize to conventional spellings (Spiderman / Spider Man → Spider-Man; Marvel Comics → Marvel; DC Comics → DC). Case and punctuation variations compare equally in filters. This is a starter alias list, not an exhaustive franchise ontology; additional aliases can be added in `backend/app/models/cosplay.py`.
- Ambiguous names move into possible characters when marked unknown. Candidates remain searchable text, but do not count as identified character-filter matches. Franchise can remain known when the specific character is unknown.
- Human corrections are separate from raw model results, survive reloads, and drive active search, facets, badges, and CSV export. An empty correction list removes every costume from the clip. **Restore model suggestions** removes the override. JSON exports retain both raw analysis and human corrections.
- Reanalysis follows the existing review-preservation rules: annotations survive identical shot intervals; changed intervals retain previous annotations in archived analysis. A stale editor is rejected when its analysis run or shot interval changed.
- The UI lists observations per shot. Longer single takes join observations across sections; this is not person tracking and may retain multiple descriptions of the same wearer.

## Premiere and Resolve

Cosplay analysis now leads the standard editor Description with costume details and appends the original scene description. Character, franchise and variant names become Keywords. The Metadata tab previews the effective values; review corrections before using Sync metadata in Premiere 0.1.20 or Resolve 0.1.3. Separate labeled cosplay fields retain the detailed identifications. See [the editor mapping](EDITOR_METADATA.md#cosplay-metadata) for native sync, XMP/XML exports and import limitations. No reanalysis is needed for existing saved costume results.

From the Media library, the legacy XML choice in **Export options** bundles up to 1,000 recordings into one editor XML and optional metadata ZIP. Combined descriptions, shot markers, CSV and XMP use the reviewed costume identifications; raw model suggestions remain in the archival JSON. The timeline references complete original recordings and requires verified source timing.

For a searchable Premiere bin of complete recordings, use **Find character** or the search and metadata filters, then **Save matches for Premiere**. Name the fixed selection (for example “Wonder Woman”), and in Premiere integration **0.1.21+** choose **Saved analyzer selections → Import clips + metadata**. This imports the originals and current corrected Description, Keywords and cosplay fields. Multiple matching segments share one source recording; all pages are included. This route does not require verified constant frame timing or another Gemini pass. The existing timeline/XML timing checks still apply to cut-based exports.

## Verification

A live Gemini Cosplay pass completed on one short booth clip after fixing provider schema compatibility. This verifies the integration, not identification accuracy across the collection; review the pilot before bulk processing. Footage and credentials are not committed.

- Request serialization tests exercise the actual Gemini SDK without network access. Generation uses a simplified native JSON schema; full Pydantic length, list-size, and extra-field validation remains local. Provider failures report actionable categories without logging raw messages.
- Backend regression tests cover aliases, same-costume group matching, unknown candidates, confirmation provenance, correction/search consistency, scoped facets, stale edits, CSV/JSON export, queue admission, focused single-take processing, continuity, and pagination of 1,000 synthetic clips.
- Frontend queue tests cover the Cosplay mode alongside existing presets; TypeScript production build and ESLint pass.
- Browser acceptance used 65 clearly labeled synthetic metadata fixtures in an isolated database. Checked all-pages selection, combined character/franchise filters, two costumes in one clip, editing/confirmation, canonicalized save, and confirmed results across all sources. Test fixtures are not included in the real library or committed.

Run focused backend verification with:

```sh
.venv/bin/python -m pytest backend/tests/test_provider_schema.py backend/tests/test_cosplay.py backend/tests/test_analysis.py backend/tests/test_library.py backend/tests/test_selected_exports.py backend/tests/test_shot_continuity.py backend/tests/test_progressive_analysis.py -q
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Alternative handoffs include FCPXML media events/timelines, a Resolve native import package and CMX3600 EDL. Use a whole-file event or Resolve import for original media whose timing or pixel aspect cannot be mapped by legacy XML. [Format choices and import instructions](MEDIA_HANDOFFS.md).
