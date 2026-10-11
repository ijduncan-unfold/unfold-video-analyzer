# Progressive analysis

The library saves and exposes results during an analysis run. The former whole-video scene-detection gate delayed the first shot result, and shot thumbnails were generated only after all scenes had been indexed.

For local footage with a measured duration, indexing starts with the first 30 seconds. Each section receives a bounded Gemini request, validates source-relative timestamps, saves its shot metadata, and extracts its thumbnails before continuing. A 152-second video produces six processing sections. Sections are processing groups; their edges are not editorial cuts.

When the preceding shot reaches a section boundary, the next request includes two seconds of look-back footage and that shot's descriptive metadata. Gemini distinguishes a visible cut from a continuous take. An explicit continuation joins the adjacent fragments into one canonical shot with the original shot number, an extended source interval, and one updated description of the entire take. Subject entrances, flashlight sweeps, action changes, and camera movement alone are not cuts. A confirmed cut, an uncertain boundary, or a gap in successful coverage keeps the shots separate. The first section's provisional shot is available immediately; its duration and midpoint thumbnail update as continuity is established.

Each shot records its contributing section numbers. Timed evidence and dialogue retain their original intervals; context-only look-back evidence is rejected. Search combines detailed notes from all contributing sections, and selected-shot exports include their section context. Detailed analysis still runs for each successfully covered section even if its only shot began in an earlier section.

Completed sections are immediately available through asset details, shot search, and portable exports. The library polls every four seconds while any job is active, even if the current query/filter returns no assets. The Analysis tab shows actual successful sections, returned shots and source time processed, plus recent playable shots. Draft metadata and playback selection are preserved when later results arrive. Prior-run results remain identified as previous results until a new run supplies its first replacement snapshot.

Full analysis continues with detailed section notes and a summary while indexed shots remain usable. Visual match-cut discovery runs separately when requested. Cancel, provider failure, and restart keep completed data. A failed indexing section does not count toward successful processing time, and a run with missing sections finishes in an error state with its partial results available. Retry currently starts another run; it is not checkpoint-only resume. Existing human annotations are reused only when shot identifiers and source intervals match, and prior results/reviews are archived at replacement. UI shot saves include the displayed interval, so a stale edit is rejected if reanalysis has reassigned that shot number. Prior model/FPS provenance and thumbnails remain valid until the first successful replacement.

Indexing uses asynchronous provider requests with a 90-second deadline, one transport attempt and low thinking on supported Gemini 3 models. Optional analysis requests have 120-second limits; a long-film synthesis may need multiple bounded requests. Stage timing logs avoid provider payloads and credentials. Thumbnails are withheld when their file predates the current run and use versioned URLs to prevent an old frame illustrating a newly defined shot.

## Long local films

The shared Proxy selector has Auto (default), Always, and Off, persisted in browser preferences and included in each requested run. Auto chooses proxies for measured local sources at least 30 minutes long, larger than 1 GiB, in heavy camera codecs/RAW extensions, or at an estimated file data rate of at least 50 Mb/s. Codec detection uses probed codecs, not a generic MOV extension. Always requires a local source and measured duration. Off bypasses transcoding, including for feature films; provider limits still apply. Proxy decisions are recorded in analysis_config with proxy_mode, proxy_used, proxy_reason, and upload_strategy. Native API clients default to Auto and may supply proxy_mode. Proxy runs use lazy 10-minute uploads. FFmpeg creates one temporary H.264/AAC part at a time, with a maximum 1280×720 output, bounded bitrate, and a 512 MiB size guard. The browser displays preparation, upload, and Google processing stages with the current part number. Short, lightweight sources use the original in Auto. The encoding stage is preparing_proxy, displayed as Preparing analysis proxy, with current/total part counts. Old preparing_part progress remains readable.

After the first part becomes ready, the analyzer publishes 30-second sections before preparing subsequent parts. Later upload parts include two seconds before their nominal ten-minute boundary so continuity can also be checked across uploads. Their maximum duration is therefore ten minutes plus two seconds. Parts are reused during the detailed pass. Provider requests and validation use each part's local timestamps; code adds the actual padded part offset exactly once to shot and evidence timestamps. Subtitles, thumbnails from the original, searches, and native editor exports therefore use the film timeline, including fractional final intervals.

The source's file identity is checked before use and after encoding. Originals are never edited or removed. Only the newly created temporary proxy is deleted after its upload; app-owned remote parts are deleted after success, failure, or cancellation. Remote cleanup is best effort if the provider is unavailable. No extra full-film copy is created. Editor imports reference originals; browser imports retain their existing managed-copy behavior and 2,000 MiB default upload limit.

Film-wide summaries and custom briefs use the saved shot and detailed section evidence instead of submitting the whole film again. Every evidence record enters bounded text batches, then hierarchical editorial notes reduce them into a final context. Notes preserve useful dialogue, visual motifs, story beats, shot numbers, and original times, and mark missing coverage. Usage is saved after each response, even if a later synthesis response is invalid. This is an evidence-based synthesis: recognition omissions or details lost in compression can carry through to the final report.

For trailer exploration, use **Full analysis** with a custom brief requesting visual hooks, character introductions, escalating stakes, quotable dialogue, and suggested moments identified by shot number and original film timecodes. Spoiler constraints belong in the brief. The tool supplies searchable editorial material; assembling a finished trailer remains an editing task.

## Verification

An isolated live Gemini 3.8 Flash test used 42 seconds of synthetic red/blue footage, containing fourteen three-second segments. It made two indexing requests:

| Checkpoint | Elapsed from test start | Available result |
| --- | ---: | --- |
| Provider upload ready | 6.61 s | Source ready for indexing |
| First section persisted | 16.03 s | 10 shots, status still analyzing |
| First section thumbnails | 16.31 s | 10 playable shot thumbnails |
| Second section persisted | 22.94 s | 14 shots, status still analyzing |
| Indexing finished | 23.09 s | 14 shots and thumbnails |

Estimated provider charge from reported usage was $0.020208. The remote test upload was deleted. This measures the progressive mechanism on a simple synthetic clip, not expected latency or recognition accuracy for all footage.

An October 2026 live Gemini check reanalyzed a 36.703-second Sony F55 continuous take that had previously been split at 30 seconds. It produced one shot spanning the complete take, one chronological description, both sections' detailed notes, and separately timed dialogue fragments associated with the same shot. The original recording remained referenced in place.

Automated checks hold a second provider request open while asserting the first section is already durable and thumbnail extraction has run. Additional checks cover continuous takes across multiple sections, real cuts at boundaries and within sections, uncertain continuity, failed middle sections, padded upload timestamps, preserved dialogue and export context, cancellation, fractional final windows, human reviews, partial API/search/export access, hidden active jobs, restart recovery, request deadlines, stale edits and thumbnail provenance. The full backend suite passed 476 tests and 19 subtests, with one skipped; the updated synthesis-coverage checks also passed.

## Remaining limits

- The first upload must become ready before indexing. Long local films need only the first 10-minute part; YouTube references and sources without measured duration keep the existing whole-source path.
- Section processing is sequential. Bounded parallel workers and checkpoint-only retries remain future work.
- Model sampling and continuity classification are not frame-accurate shot detection. Cuts can still be missed or misclassified; review boundaries before final editorial use.
- Long-film summaries/custom briefs use hierarchical saved evidence, with possible omissions. Actual feature-film provider performance and trailer-selection quality still need a representative footage test.
