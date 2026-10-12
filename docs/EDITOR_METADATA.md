# Analysis metadata in editors

The **Metadata** tab shows the values prepared for editor handoff, their source, and the destination fields. Switch between whole-clip metadata and the currently previewed shot. **View all analysis metadata** exposes the complete shot/section analysis, summary, custom analysis, reviewed annotations, timed dialogue and rough cut. Your editable tags and notes remain separate from AI observations.

Both panels have **Sync metadata** for the currently loaded source. This is an explicit project edit and makes no additional Gemini requests. Resolve also maps metadata when saving individual shots or adding them to a timeline. Updated panel packages are required; existing saved shots gain the mapping when saved again.

| Analyzer value | Resolve | Premiere project metadata | Adobe XMP sidecar |
|---|---|---|---|
| Clip description / shot description | Description | Description | dc:description |
| Tags, colors, subjects, actions, framing, mood | Keywords | Video Analyzer Keywords | dc:subject |
| Human notes | Comments and structured record | Log Note | xmpDM:logComment |
| Slate scene / shot / take | Scene / Shot / Take | Scene / Shot / Video Analyzer Take | xmpDM:scene / shotNumber / takeNumber |
| Camera roll / camera identifier | Roll/Card # (SDK `Roll Card #`) / Camera # | Tape Name / Video Analyzer Camera | xmpDM:tapeName / cameraLabel |
| Production title | Production Name | Video Analyzer Production; native XMP Project Name | xmpDM:projectName |
| Director / cinematographer | Director / DOP | Video Analyzer Director / Cinematographer | xmpDM:director / directorPhotography |
| Framing / movement | Shot Type / Move | Video Analyzer Shot Type / Camera Movement | xmpDM:shotSize / cameraMove |
| Location | Location and Keywords | Video Analyzer Location; native XMP Shot Location | xmpDM:shotLocation |
| Colors / subjects | Keywords and structured record | Video Analyzer Colors / Subjects | va:colors / subjects |
| Dialogue | Dialog Notes, readable Comments and structured record; SRT export | Video Analyzer Dialogue and complete record; SRT export | va:transcript and complete record |
| Lighting, narrative, sound, evidence, section context, custom analysis, annotations | Complete Comments record and third-party metadata | Video Analyzer Detailed analysis and Complete analysis JSON | va:analysisRecord |
| Every clip description with its source-relative interval | Video Analyzer Clip descriptions and readable Comments | Video Analyzer Clip descriptions | va:analysisRecord |
| Every slate, including conflicting readings, raw text and ambiguous dates | Video Analyzer Slate readings and readable Comments | Video Analyzer Slate readings | va:analysisRecord |
| Original technical facts and source timecode | Video Analyzer Source inspection and complete record; native source interpretation is unchanged | Video Analyzer Source inspection and complete record; native source interpretation is unchanged | va:technical and sourceTimecode |

Names in the Resolve mapping use the native metadata API's keys. These can differ from clip properties: `Roll/Card` is displayed by `GetClipProperty`, while `SetMetadata` requires `Roll Card #`. The panel translates older companion mappings too. Every native metadata write is checked by reading the fields back. Host versions can differ: rejected fields are reported as errors, never silently counted as exported.

Scalar slate fields are only assigned when readings agree. Multiple scenes/takes remain per-shot observations in the complete record; processing section indexes are never used as production scene numbers. Slate values retain their AI provenance and are unreviewed. XMP `takeNumber` is an integer; labels such as `MOS` and leading zeros remain intact in the structured record. Subjects can include objects, so they are not incorrectly assigned to Resolve's People field. A review status is not a Good Take or copyright assertion.

## Cosplay metadata

With a saved **Cosplay** analysis, Description starts with the effective costume identifications: character, franchise, variant, Suggested / Confirmed by you / Unknown status, visible details, activity and any explicitly unconfirmed alternatives. The original scene description follows under **Scene description**, and asset notes remain included. Other analysis modes keep their existing Description behavior and expose costume information in the separate fields. Changing the analysis picker alone does not change a saved analysis's metadata.

Character, franchise and variant names also become Keywords. Uncertain alternative characters remain labeled in the description instead of becoming character keywords. Multiple costumes retain their own status and detail blocks, and whole-media descriptions label multiple clips by interval. Empty human overrides remove costumes; corrected names replace AI suggestions in active exported fields. Raw suggestions remain in the archival JSON record.

Premiere panel **0.1.20** adds **Video Analyzer · Cosplay characters**, **Cosplay franchises**, **Costume variants**, **Cosplay identification**, **Cosplay description**, and **Scene description** project fields. Enable these through Metadata Display to inspect or sort them. Standard Description and XMP Keywords carry the same effective information.

Resolve integration **0.1.3** uses Description and Keywords for both source sync and saved subclips. Read the labeled costume fields in Comments or SDK third-party metadata; these are not automatically installed as visible Resolve Custom columns. Repeated sync replaces keyword contributions owned by the integration while retaining editor keywords. Keywords written by older versions without ownership receipts cannot be safely distinguished from human keywords and remain preserved.

The Metadata tab previews this mapping without another analysis call. XMP sidecars include the same description, keyword array and separate `va:cosplay*` properties. Premiere XML includes the composed description and mapped fields in logging info; FCPXML includes the description in clip notes and names as keywords. For native source metadata, use **Sync metadata** in the updated editor panel. XML importer behavior varies by editor/version; XMP sidecar association with an already imported file is not guaranteed. Source files are not rewritten by the analyzer.

Cosplay mapping and editor-host writes have automated coverage, including corrections, unknown alternatives, selected exports and preservation of editor edits. Native acceptance of these cosplay fields inside Premiere and Resolve remains to be performed.

## Preserve information and editor edits

Native fields provide convenient search and sorting; editor schemas cannot express every analysis detail. The complete versioned record retains the rest, including source-relative dialogue intervals, deep section analysis, custom results, shot annotations, rough-cut drafts and provenance. XMP now embeds that record alongside its standard fields. Exporting selected shots keeps the established selection scope and overlapping dialogue; section notes remain identified as section-wide context.

Sync fills empty native fields and updates values previously written by this integration. Existing editor values and subsequent manual edits are preserved. Resolve merges keywords and keeps human Comments around the analyzer record. Premiere stores ownership in a project metadata field and keeps unrelated properties. Premiere's custom columns can be enabled using **Metadata Display** in the Project panel.

Resolve 0.1.2 also writes separate SDK third-party metadata fields: **Video Analyzer Clip descriptions**, **Dialogue**, **Detailed analysis**, **Slate readings**, **Source inspection**, and **Complete analysis JSON**, along with labeled copies of all mapped values. These SDK fields are distinct from Resolve 21's visible Custom field definitions; writing them does not automatically create UI columns. Open Resolve's Metadata panel and select **All Groups** to read **Comments** for the labeled complete analysis and **Dialog Notes** for timed dialogue. Existing native dialogue notes, third-party fields and edits to the readable Comments block are preserved; generated fields carry ownership receipts and every third-party write is read back. Saved subclips receive only their selected range's descriptions, dialogue and analysis context, referencing the original media.

The panels cap a complete source metadata record at 4 million JavaScript characters (approximately 4 MB for ASCII); Resolve's individual shot export retains its existing 200,000-character limit. Oversized records produce an explicit error with JSON/XMP as alternatives, rather than truncating a transcript. Provider credentials and original directory paths are excluded from portable records.

Premiere sync writes both project metadata and the source item's standard XMP packet through Adobe's Metadata API. Director of Photography, Director, Scene, Take, Tape Name, Camera Label, Project Name, Shot Size, Camera Move and Shot Location use the Dynamic Media schema. Description uses localized Dublin Core text and Keywords use an unordered array. Premiere manages persistence to embedded metadata or sidecars according to its own settings; the analyzer does not directly open or rewrite source files. Resolve edits Media Pool metadata. Sync does not render or duplicate media, change frame-rate interpretation or alter source timecode.

## Dialogue and After Effects limits

Adobe's UXP API supports native transcript import using a documented JSON schema with word-level timestamps and confidence. Our Gemini dialogue currently has cue-level timestamps. Native text-based editing needs a word-alignment step; we do not invent word timings or confidence scores to populate that schema. Timed dialogue already travels in the complete metadata record, as searchable text in the panels/native metadata, as source markers on request and through SRT downloads. SRT captions are distinct from a native speech transcript.

Resolve's SDK offers native audio transcription, but no documented external transcript-import operation was found in the installed scripting reference. This integration does not pretend that a Comments record or SRT is a native Resolve transcript.

After Effects shares Adobe's XMP model and can turn source XMP markers into layer markers. We export compatible standard XMP fields plus the complete custom record; **there is no After Effects plugin or automatic sidecar ingestion implemented here**. File format, metadata import and marker preferences determine what After Effects reads. An AE bridge would need explicit application of the sidecar and conversion of timed records into its layer markers.

The sync paths have automated tests for mapping, source identity, complete-record preservation, human edits, stale sessions and failed/readback-inconsistent writes. Premiere 0.1.19 was checked in the running Windows editor with Sony F55 media: standard Director and Director of Photography fields populated, and project metadata retained the complete analysis, dialogue, clip descriptions, slate readings and source inspection. Premiere can briefly return stale packets after a transaction; sync verifies both packets with a bounded wait. Resolve 0.1.2 was checked in Windows Studio 21 with C0002 and Sony F55 A001C002: DOP, Director, Scene, Take, Production Name and the corrected `Roll Card #` key were accepted; both dialogue sections appeared in Dialog Notes. The complete JSON record, all clip descriptions, slates, detailed analysis and technical inspection matched SDK readback. Saving the Sony subclip to the Media Pool passed native and third-party metadata verification too. Sony's 1,412 frames and original `12:46:55:08` start timecode stayed unchanged.

## Primary references

- [Adobe UXP Metadata API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/metadata) and [official metadata sample](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/src/metadata.ts)
- [Adobe XMP Dynamic Media schema](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp-dm/)
- [Premiere clip versus file metadata](https://helpx.adobe.com/premiere/desktop/organize-media/edit-metadata/metadata-in-premiere.html)
- [Adobe native Transcript API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/transcript) and [transcript JSON specification](https://github.com/AdobeDocs/uxp-premiere-pro-samples/blob/main/sample-panels/premiere-api/assets/transcript_format_spec.json)
- [After Effects XMP and source markers](https://helpx.adobe.com/uk/after-effects/desktop/work-with-footage-items/work-with-xmp-metadata/xmp-metadata.html)
- Blackmagic's installed Resolve Studio scripting reference: `Support/Developer/Scripting/README.txt`, `MediaPoolItem.GetMetadata`, `SetMetadata`, `GetThirdPartyMetadata`, `SetThirdPartyMetadata`; [Blackmagic editor training](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-18-Editors-Guide.pdf?_v=1680159610000) describes native metadata organization.
