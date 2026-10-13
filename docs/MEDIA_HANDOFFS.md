# Selected-media handoffs

Choose **Export** in the library. Inside the modal, **Include** selects checked recordings or all current search matches. **Premiere · saved selection** saves that scope or reopens existing Premiere selections; all file formats are in the same menu. Search and character/franchise filters also feed the existing selection tools. All formats reference originals in place; none render proxies or bundle video bytes. Preserve camera sidecars and folder structure and keep the source drives mounted. Handoffs contain the original filesystem paths.

| Choice | Best use | Required source facts | Metadata and limitations |
|---|---|---|---|
| Modern Final Cut Pro XML (.fcpxml) → Media event only | Whole files in a Final Cut event | Absolute original file references | Notes, keywords and complete analysis metadata. The editor discovers timing, dimensions, rotation and pixel aspect from the original. No Premiere XML validation. |
| Modern Final Cut Pro XML (.fcpxml) → Timeline + media event | Ordered originals for Final Cut or Resolve | Known dimensions, reliable frame timing, supported orientation | Rational source timing and explicit `paspH`/`paspV` for known pixel aspect; notes and keywords. No legacy Premiere pixel-aspect table. |
| DaVinci Resolve native media import | A Resolve bin containing editable originals and searchable metadata | Accessible unchanged originals; working Resolve Python scripting runtime | A standalone Python script calls native media import and writes Description, Keywords, production fields and full analysis in Comments. No timeline, XML, frame-rate or pixel-aspect mapping. |
| CMX3600 EDL | Simple video conform | One supported verified rate across the selection; known non-drop embedded timecode | Video only, up to 999 events. Import originals separately; assign/map the generated reel IDs using the packaged reel CSV. Full analysis stays in the metadata package. |
| Legacy Final Cut Pro XML (.xml) → Media bin | Whole original clips for Premiere | Reported nominal rate, duration, original audio layout and absolute file paths | Clip Description and Log Note contain cosplay details and complete analysis. Native media interpretation; no timeline pixel-aspect or constant-cadence gate. |
| Legacy Final Cut Pro XML (.xml) → Timeline | Constructed timeline for Premiere / Resolve | Verified source timing plus dimension, orientation, pixel-aspect and audio-layout checks | Logging, markers and linked original audio. Strict timeline safeguards remain. |

The dialog checks **the chosen format and layout**. An unavailable legacy XML or timeline does not disable a whole-file media event or native Resolve import. EDL remains a conform list, not a way to carry full video/audio plus searchable metadata. Use the native Premiere selection panel or native Resolve import for that workflow.

## BRAW, ARRI MXF and other camera originals

There is no BRAW/MXF extension block in the new handoffs. They reference the camera original, never substitute its analysis/viewing proxy, and leave native decoding to the receiving editor. Resolve supports Blackmagic RAW; ARRI publishes a Resolve workflow for its original camera files. Specific codecs, RAW plugins, platform and editor version still determine whether the original can be decoded. An FCPXML, EDL or Python package cannot install a decoder or turn an unsupported codec into supported media.

FCPXML **media event** intentionally omits optional technical attributes rather than inventing them. Its format, duration and clip duration attributes are optional in Apple's FCPXML DTD. For timelines, known non-square sample aspect ratios are represented directly as rational `paspH` and `paspV`. Unknown rotation mapping and unreliable frame cadence still require whole-file import rather than a fabricated timed edit.

## Premiere import with legacy XML

1. In **Export**, choose **Final Cut Pro XML — Legacy (.xml) · Premiere / Resolve** (the default), then **Media bin · whole original clips**.
2. Choose selected recordings or all search matches and download. Extract the ZIP if the metadata package is enabled.
3. In Premiere, use **File → Import** and select **Video Analyzer.xml**. No analyzer panel is needed for this workflow.
4. The named bin contains original video/audio. Search in the Project panel with **All metadata**, or show **Description** and **Log Note** columns via Metadata Display. The Metadata panel's **Clip** section contains these project fields; **File → Dublin Core** describes the original file and is not rewritten by XML import.
5. Save the Premiere project. Keep the original media available at the referenced paths, or relink it if moved.

**Modern (.fcpxml)** remains available for Final Cut Pro. It is not directly importable by Premiere; renaming it to `.xml` does not convert it. The legacy export emits FCP7 `xmeml` version 5.

Whole-file legacy clips still require rate and duration declarations in the interchange file. They use the reported nominal rate for those declarations without asserting verified constant cadence, and Premiere probes the original media. Analysis markers are omitted when cadence is unverified; all observations remain in Description/Log Note and the metadata package. The bin route omits pixel-aspect and rotation overrides. It does not invent missing rate, duration or audio layout; the native Premiere panel remains an alternative when those facts are unavailable.

## Portable metadata and Resolve CSV

Every editor package includes `Resolve metadata.csv` (one row per original), `Metadata import.txt`, and XMP sidecars. XMP includes Dublin Core `dc:title`, `dc:description` and `dc:subject`, with the same corrected cosplay details as Premiere's clip fields. Exporting sidecars does not apply them to the original media or populate Premiere's separate File metadata section.

For Resolve's CSV workflow, import originals, then use **File → Import Metadata To → Media Pool**. Choose `Resolve metadata.csv`, match **filename and source file path**, and disable timecode matching. `Clip Directory` preserves source directories, including duplicate camera basenames. If moved, update paths before import. Use **Only update metadata items with entries in the source file**; this can replace existing populated fields. The native script below preserves existing populated fields and merges keywords instead. Metadata CSV import is documented in [Blackmagic's Editor's Guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-18-Editors-Guide.pdf?_v=1680159610000); this generated CSV still needs acceptance in an installed Resolve instance.

## Resolve import

1. Choose **DaVinci Resolve native media import** and download the editor package.
2. Extract it and open the intended Resolve project.
3. Open **Workspace → Console**, select **Python 3**, and run `exec(open('/absolute/path/Import into Resolve.py', encoding='utf-8').read())`, substituting the extracted script path. Windows paths may use forward slashes. This requires Resolve's configured Python scripting runtime; the existing integration is another native workflow.
4. The script creates a named Media Pool bin, imports each original and verifies its file path and bin membership. It merges Keywords, fills empty mapped fields, and appends labeled analysis to Comments, preserving existing descriptions and human comments. It does not add clips to a timeline or save the project automatically.
5. Check the Console's counts and the bin. A failure stops with completed counts; retry reuses that bin and its existing originals. No source is silently skipped. Files changed since export require a new package. Existing populated fields are preserved rather than replaced on later exports; the new snapshot remains in Comments.

The script is self-contained and includes the saved metadata at export time; it needs neither an analyzer connection nor an API key. It executes only when you run it. The ZIP also includes JSON, CSV, XMP, available SRT, and instructions. It is a native import helper, **not** a generated `.drp` or `.drb` project/bin file; those are owned by Resolve.

## Validation and acceptance

Native Premiere 26.5.2 acceptance on the nine Comic Con MP4s: imported the legacy bin, opened the Wonder Woman source, searched `wonder`, saved the project, and verified nine original media paths plus nine populated Description and Log Note fields in the saved `.prproj`. The final bin export does not include unverified frame markers. RAW/MXF and Resolve native acceptance are still pending; unit coverage is not decoder verification.

Automated coverage exercises BRAW/MXF references, anamorphic FCPXML, unknown timing in whole-file events, mixed-rate timelines, single-rate EDL constraints, package contents, source changes, native import readback, retries and preservation of editor metadata. The generated Resolve script is executed against an injected host, not merely checked as text. FCPXML media-event and anamorphic timeline samples validate against Apple's published 1.10 DTD. These checks do not establish acceptance inside installed Final Cut or Resolve; that remains pending.

Primary references: [Apple FCPXML DTD](https://developer.apple.com/documentation/professional-video-applications/document-type-definition), [creating FCPXML documents](https://developer.apple.com/documentation/professional-video-applications/creating-fcpxml-documents), [Blackmagic RAW](https://www.blackmagicdesign.com/products/blackmagicraw), [ARRI original-camera-files workflow in Resolve](https://www.arri.com/resource/blob/31938/003ed1931084419a4aa40b09187b2b9d/arri-rendering-dailies-in-davinci-resolve-workflow-guideline-data.pdf). Resolve API names follow the existing integration and the scripting reference used to validate that integration (`MediaPool.ImportMedia`, `AddSubFolder`, `MediaPoolItem.GetClipProperty`, `GetMetadata`, `SetMetadata`).
