# Selected-media handoffs

Select original recordings in the library, then choose **Export options**. Search and character/franchise filters also feed the existing selection tools. All formats reference originals in place; none render proxies or bundle video bytes. Preserve camera sidecars and folder structure and keep the source drives mounted. Handoffs contain the original filesystem paths.

| Choice | Best use | Required source facts | Metadata and limitations |
|---|---|---|---|
| Final Cut Pro XML → Media event only | Whole files in a Final Cut event | Absolute original file references | Notes, keywords and complete analysis metadata. The editor discovers timing, dimensions, rotation and pixel aspect from the original. No Premiere XML validation. |
| Final Cut Pro XML → Timeline + media event | Ordered originals for Final Cut or Resolve | Known dimensions, reliable frame timing, supported orientation | Rational source timing and explicit `paspH`/`paspV` for known pixel aspect; notes and keywords. No legacy Premiere pixel-aspect table. |
| DaVinci Resolve native media import | A Resolve bin containing editable originals and searchable metadata | Accessible unchanged originals; working Resolve Python scripting runtime | A standalone Python script calls native media import and writes Description, Keywords, production fields and full analysis in Comments. No timeline, XML, frame-rate or pixel-aspect mapping. |
| CMX3600 EDL | Simple video conform | One supported verified rate across the selection; known non-drop embedded timecode | Video only, up to 999 events. Import originals separately; assign/map the generated reel IDs using the packaged reel CSV. Full analysis stays in the metadata package. |
| Premiere / Resolve legacy XML | Existing FCP7 timeline/bin workflow | Existing timing, dimension, orientation, pixel-aspect and audio-layout checks | Logging, markers and original audio links. Existing safeguards remain. |

The dialog checks **the chosen format and layout**. An unavailable legacy XML or timeline does not disable a whole-file media event or native Resolve import. EDL remains a conform list, not a way to carry full video/audio plus searchable metadata. Use the native Premiere selection panel or native Resolve import for that workflow.

## BRAW, ARRI MXF and other camera originals

There is no BRAW/MXF extension block in the new handoffs. They reference the camera original, never substitute its analysis/viewing proxy, and leave native decoding to the receiving editor. Resolve supports Blackmagic RAW; ARRI publishes a Resolve workflow for its original camera files. Specific codecs, RAW plugins, platform and editor version still determine whether the original can be decoded. An FCPXML, EDL or Python package cannot install a decoder or turn an unsupported codec into supported media.

FCPXML **media event** intentionally omits optional technical attributes rather than inventing them. Its format, duration and clip duration attributes are optional in Apple's FCPXML DTD. For timelines, known non-square sample aspect ratios are represented directly as rational `paspH` and `paspV`. Unknown rotation mapping and unreliable frame cadence still require whole-file import rather than a fabricated timed edit.

## Resolve import

1. Choose **DaVinci Resolve native media import** and download the editor package.
2. Extract it and open the intended Resolve project.
3. Open **Workspace → Console**, select **Python 3**, and run `exec(open('/absolute/path/Import into Resolve.py', encoding='utf-8').read())`, substituting the extracted script path. Windows paths may use forward slashes. This requires Resolve's configured Python scripting runtime; the existing integration is another native workflow.
4. The script creates a named Media Pool bin, imports each original and verifies its file path and bin membership. It merges Keywords, fills empty mapped fields, and appends labeled analysis to Comments, preserving existing descriptions and human comments. It does not add clips to a timeline or save the project automatically.
5. Check the Console's counts and the bin. A failure stops with completed counts; retry reuses that bin and its existing originals. No source is silently skipped. Files changed since export require a new package. Existing populated fields are preserved rather than replaced on later exports; the new snapshot remains in Comments.

The script is self-contained and includes the saved metadata at export time; it needs neither an analyzer connection nor an API key. It executes only when you run it. The ZIP also includes JSON, CSV, XMP, available SRT, and instructions. It is a native import helper, **not** a generated `.drp` or `.drb` project/bin file; those are owned by Resolve.

## Validation and acceptance

Automated coverage exercises BRAW/MXF references, anamorphic FCPXML, unknown timing in whole-file events, mixed-rate timelines, single-rate EDL constraints, package contents, source changes, native import readback, retries and preservation of editor metadata. The generated Resolve script is executed against an injected host, not merely checked as text. FCPXML media-event and anamorphic timeline samples validate against Apple's published 1.10 DTD. These checks do not establish acceptance inside installed Final Cut or Resolve; that remains pending.

Primary references: [Apple FCPXML DTD](https://developer.apple.com/documentation/professional-video-applications/document-type-definition), [creating FCPXML documents](https://developer.apple.com/documentation/professional-video-applications/creating-fcpxml-documents), [Blackmagic RAW](https://www.blackmagicdesign.com/products/blackmagicraw), [ARRI original-camera-files workflow in Resolve](https://www.arri.com/resource/blob/31938/003ed1931084419a4aa40b09187b2b9d/arri-rendering-dailies-in-davinci-resolve-workflow-guideline-data.pdf). Resolve API names follow the existing integration and the scripting reference used to validate that integration (`MediaPool.ImportMedia`, `AddSubFolder`, `MediaPoolItem.GetClipProperty`, `GetMetadata`, `SetMetadata`).
