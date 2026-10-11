# Analysis metadata in editors

The **Metadata** tab shows the values prepared for editor handoff, their source, and the destination fields. Switch between whole-clip metadata and the currently previewed shot. **View all analysis metadata** exposes the complete shot/section analysis, summary, custom analysis, reviewed annotations, timed dialogue and rough cut. Your editable tags and notes remain separate from AI observations.

Both panels have **Sync metadata** for the currently loaded source. This is an explicit project edit and makes no additional Gemini requests. Resolve also maps metadata when saving individual shots or adding them to a timeline. Updated panel packages are required; existing saved shots gain the mapping when saved again.

| Analyzer value | Resolve | Premiere project metadata | Adobe XMP sidecar |
|---|---|---|---|
| Clip description / shot description | Description | Description | dc:description |
| Tags, colors, subjects, actions, framing, mood | Keywords | Video Analyzer Keywords | dc:subject |
| Human notes | Comments and structured record | Log Note | xmpDM:logComment |
| Slate scene / shot / take | Scene / Shot / Take | Scene / Shot / Video Analyzer Take | xmpDM:scene / shotNumber / takeNumber |
| Camera roll / camera identifier | Roll/Card / Camera # | Tape Name / Video Analyzer Camera | xmpDM:tapeName / cameraLabel |
| Production title | Production Name | Video Analyzer Production | va:productionTitle |
| Director / cinematographer | Director / DOP | Video Analyzer Director / Cinematographer | xmpDM:director / directorPhotography |
| Framing / movement | Shot Type / Move | Video Analyzer Shot Type / Camera Movement | xmpDM:shotSize / cameraMove |
| Location | Location and Keywords | Video Analyzer Location | va:location |
| Colors / subjects | Keywords and structured record | Video Analyzer Colors / Subjects | va:colors / subjects |
| Dialogue | Timed text in Comments and structured record; SRT export | Video Analyzer Dialogue and complete record; SRT export | va:transcript and complete record |
| Lighting, narrative, sound, evidence, section context, custom analysis, annotations | Complete Comments record and third-party metadata | Video Analyzer Complete analysis JSON | va:analysisRecord |
| Original technical facts and source timecode | Retained in the record; native source interpretation is unchanged | Retained in the record; native source interpretation is unchanged | va:technical and sourceTimecode |

Names in the Resolve mapping use the native field names observed in the installed Studio SDK's clip-property snapshot. Every native metadata write is checked by reading the fields back. Host versions can differ: rejected fields are reported as errors, never silently counted as exported.

Scalar slate fields are only assigned when readings agree. Multiple scenes/takes remain per-shot observations in the complete record; processing section indexes are never used as production scene numbers. Slate values retain their AI provenance and are unreviewed. XMP `takeNumber` is an integer; labels such as `MOS` and leading zeros remain intact in the structured record. Subjects can include objects, so they are not incorrectly assigned to Resolve's People field. A review status is not a Good Take or copyright assertion.

## Preserve information and editor edits

Native fields provide convenient search and sorting; editor schemas cannot express every analysis detail. The complete versioned record retains the rest, including source-relative dialogue intervals, deep section analysis, custom results, shot annotations, rough-cut drafts and provenance. XMP now embeds that record alongside its standard fields. Exporting selected shots keeps the established selection scope and overlapping dialogue; section notes remain identified as section-wide context.

Sync fills empty native fields and updates values previously written by this integration. Existing editor values and subsequent manual edits are preserved. Resolve merges keywords and keeps human Comments around the analyzer record. Premiere stores ownership in a project metadata field and keeps unrelated properties. Premiere's custom columns can be enabled using **Metadata Display** in the Project panel.

The panels cap a complete source metadata record at 4 million JavaScript characters (approximately 4 MB for ASCII); Resolve's individual shot export retains its existing 200,000-character limit. Oversized records produce an explicit error with JSON/XMP as alternatives, rather than truncating a transcript. Provider credentials and original directory paths are excluded from portable records.

Premiere writes through the project-metadata API, not the file-XMP API. **Premiere's own metadata linking/writing preferences can mirror linked standard fields into file XMP**. Review those preferences when originals must remain read-only. Resolve edits Media Pool metadata. The analyzer does not render, duplicate, delete, change frame-rate interpretation or alter source timecode as part of sync.

## Dialogue and After Effects limits

Adobe's UXP API supports native transcript import using a documented JSON schema with word-level timestamps and confidence. Our Gemini dialogue currently has cue-level timestamps. Native text-based editing needs a word-alignment step; we do not invent word timings or confidence scores to populate that schema. Timed dialogue already travels in the complete metadata record, as searchable text in the panels/native metadata, as source markers on request and through SRT downloads. SRT captions are distinct from a native speech transcript.

Resolve's SDK offers native audio transcription, but no documented external transcript-import operation was found in the installed scripting reference. This integration does not pretend that a Comments record or SRT is a native Resolve transcript.

After Effects shares Adobe's XMP model and can turn source XMP markers into layer markers. We export compatible standard XMP fields plus the complete custom record; **there is no After Effects plugin or automatic sidecar ingestion implemented here**. File format, metadata import and marker preferences determine what After Effects reads. An AE bridge would need explicit application of the sidecar and conversion of timed records into its layer markers.

The new sync paths have automated tests for mapping, source identity, complete-record preservation, human edits, stale sessions and failed/readback-inconsistent writes. Native acceptance of these newly promoted fields still needs testing inside Premiere and Resolve; the earlier live Resolve shot/subclip checks do not validate these new sync actions.

## Primary references

- [Adobe UXP Metadata API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/metadata) and [official metadata sample](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/metadata.ts)
- [Adobe XMP Dynamic Media schema](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-dm/)
- [Premiere clip versus file metadata](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/metadata-in-premiere.html)
- [Adobe native Transcript API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transcript) and [transcript JSON specification](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/assets/transcript_format_spec.json)
- [After Effects XMP and source markers](https://helpx.adobe.com/uk/after-effects/desktop/work-with-footage-items/work-with-xmp-metadata/xmp-metadata.html)
- Blackmagic's installed Resolve Studio scripting reference: `Support/Developer/Scripting/README.txt`, `MediaPoolItem.GetMetadata`, `SetMetadata`, `GetThirdPartyMetadata`, `SetThirdPartyMetadata`; [Blackmagic editor training](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-18-Editors-Guide.pdf?_v=1680159610000) describes native metadata organization.
