# Progressive analysis

The library saves and exposes results during an analysis run. The former whole-video scene-detection gate delayed the first shot result, and shot thumbnails were generated only after all scenes had been indexed.

For local footage with a measured duration, indexing now starts with the first 30 seconds. Each section receives a bounded Gemini request, validates source-relative timestamps, saves its shot metadata, and extracts its thumbnails before continuing. A 152-second video produces six processing sections. Sections are explicitly labeled as processing groups; their edges are not detected editorial cuts. A shot touching a section boundary is flagged because it may continue into the neighboring section.

Completed sections are immediately available through asset details, shot search, and portable exports. The library polls every four seconds while any job is active, even if the current query/filter returns no assets. The Analysis tab shows actual successful sections, returned shots and source time processed, plus recent playable shots. Draft metadata and playback selection are preserved when later results arrive. Prior-run results remain identified as previous results until a new run supplies its first replacement snapshot.

Full analysis continues with detailed section notes and a summary while indexed shots remain usable. Visual match-cut discovery runs separately when requested. Cancel, provider failure, and restart keep completed data. A failed indexing section does not count toward successful processing time, and a run with missing sections finishes in an error state with its partial results available. Retry currently starts another run; it is not checkpoint-only resume. Existing human annotations are reused only when shot identifiers and source intervals match, and prior results/reviews are archived at replacement. UI shot saves include the displayed interval, so a stale edit is rejected if reanalysis has reassigned that shot number. Prior model/FPS provenance and thumbnails remain valid until the first successful replacement.

Indexing uses asynchronous provider requests with a 90-second deadline, one transport attempt and low thinking on supported Gemini 3 models. Optional analysis passes have 120-second limits. Stage timing logs avoid provider payloads and credentials. Thumbnails are withheld when their file predates the current run and use versioned URLs to prevent an old frame illustrating a newly defined shot.

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

Automated checks hold a second provider request open while asserting the first section is already durable and thumbnail extraction has run. Additional checks cover cancellation, failed middle sections, fractional final windows, unchanged human reviews, partial API/search/export access, hidden active jobs, restart recovery, request deadlines, stale edits and thumbnail provenance. The full local suite passed 84 tests and 49 subtests; frontend build and lint passed.

## Remaining limits

- The original video still has to upload and become ready in Google's Files API before the first indexing request. Proxy-first ingestion and independent chunk uploads remain future work.
- Section processing is sequential. Bounded parallel workers and checkpoint-only retries remain future work.
- Model sampling is not frame-accurate shot detection. Local cut detection and cross-section stitching are needed before treating every returned interval as a verified edit boundary.
- Full-video summary and custom analysis still have whole-source context limits. The progressive index does not establish feature-film readiness for every optional pass.
