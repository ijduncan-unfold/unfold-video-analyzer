# Podcast and talking-head rough cuts

The Rough cut workspace builds a saved, reviewable edit of one local recording. It uses an explicit Gemini pass on video and audio, rather than guessing sentence timings from the existing shot-level transcript. A previous footage analysis is not required.

## Workflow

1. Open a recording and choose **Rough cut**.
2. Choose **Clean up the take**, or **Make it shorter** with a target in minutes. Add priorities in the editing brief, such as retaining the demonstration, keeping natural pauses, or removing repeated explanations.
3. Generate. The app saves speech excerpts as each two-minute source window finishes. Shorter utterances carry approximate times, verbatim intelligible dialogue, visual notes, a take classification, and an editorial reason.
4. Gemini selects complete thoughts in chronological source order, preferring clear delivery and useful visuals while preserving meaning. Large inputs use bounded batches, with cross-batch reconciliation when the selected evidence fits one context; otherwise a continuity warning remains.
5. Search dialogue or visual/edit notes, preview the sequence, adjust boundaries, reorder or remove excerpts, and restore omitted takes. Preview seeks through the original, so joins can pause during seeking. Save changes to persist the review.
6. Export FCPXML for an editable timeline or JSON for the draft. The timeline retains audio, uses original source ranges, and references the original filename. Place the recording beside the XML or relink it in Resolve/Final Cut Pro. Export follows the reviewed sequence order, even when it differs from source order.

## Source protection and persistence

One rough-cut generation runs at a time on the companion. Atomic asset claims prevent simultaneous reanalysis/deletion. The rough-cut worker does not overwrite shot analysis, human metadata, or the existing transcript. It saves separate provider usage and a cost estimate for each draft.

The shared Proxy control applies here too. Auto proxies long, large, heavy-camera, or high-data-rate sources; Always uses temporary proxies; Off uploads the original without encoding. The selected mode and decision reason are saved with the draft. Proxy runs prepare ten-minute H.264/AAC uploads lazily and reuse them across two-minute speech requests, displaying Preparing analysis proxy and the part number. Original-upload runs reuse one upload and keep every request on the original timeline. Only derived temporary files and app-owned provider parts are cleanup targets. File identity is verified before processing and export. Proxy offsets are added exactly once in code; later parts do not restart exported timing at zero.

The draft, speech evidence, sequence order, trims, and editorial reasons live in SQLite. Revision checks reject stale saves/exports; unknown IDs, duplicate excerpts, invalid source bounds, and overlapping source ranges are rejected. Browser polling protects unsaved edits and stops when inactive. Cancellation, failures, and restart retain discovered speech, identify incomplete generation, and block unfinished timeline exports. Generate again starts a new draft, including new provider calls.

## Limits

- Timings are AI estimates, not word alignment or verified edit boundaries. Review the beginnings/endings of words and thoughts.
- Selection is an editorial proposal, not guaranteed preservation of every relevant point. Check qualifications, context, and continuity before publishing.
- Condense targets are approximate; excess duration is flagged instead of cutting arbitrarily through a sentence.
- Silent B-roll is not independently selected by this speech-focused pass. Multicam switching, word-level filler deletion, retimed captions, and rendering a new MP4 remain future work.
- Analysis uses the recording's first audio stream. Additional separate microphone/audio streams are not mixed by this prototype; use a recording with the dialogue in that stream.
- Source timing/orientation must satisfy the existing FCPXML exporter. JSON remains available for unsupported source timing. The generated rough-cut XML was structurally tested and downloaded through the browser; native editor import of this particular draft is still pending.
- Additional Gemini API charges apply. Cost estimates use reported usage and configured model pricing, with the same uncertainty as other analysis costs.

## Verification, October 10, 2026

Fifteen automated rough-cut checks cover audio/key requirements, mutually exclusive claims, interrupted recovery, invalid/duplicate/overlapping excerpts, source changes, stale revisions, saved trim/reorder operations, chronological and manually reordered timeline offsets, original-file retention, cancellation, partial persistence, unknown model IDs, later-upload time translation, and provider-compatible schemas with local length validation retained.

An isolated live Gemini 3.8 Flash smoke test used 33.333 seconds of synthesized speech over a plain blue picture. The script contained a false-start introduction and two versions of the editing principle. Gemini returned nine speech moments and selected five excerpts totaling 17.1 seconds: the clean introduction, topic, visual test description, clean final explanation, and sign-off. It described the picture as a solid blue background. Successful-run usage estimated $0.01276; failed diagnostic requests are not included in that figure. The original recording's hash and modification time were unchanged, and temporary provider parts were cleaned up.

Browser acceptance used that separate library, not the user's footage database. Sequence playback, search highlighting, a boundary edit/save, tab navigation persistence, and XML download worked with no browser errors/warnings. The downloaded XML contained five contiguous sequence excerpts, the revised 4.9-second source in point rounded to source frames, and an audio-enabled source reference. The fixture establishes the mechanism, not recognition/editorial quality on a real 20-minute podcast.

The live test exposed a provider 400 rejection of the constrained response schema. A small schema subset is now sent to Gemini; Pydantic still enforces response limits locally. The same fix applies to the long-film editorial-note synthesis introduced previously.
