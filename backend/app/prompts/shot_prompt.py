SHOT_DETECTION_PROMPT = """Create a useful editorial shot log with evidence for this video segment.
Scene {scene_number}: "{scene_title}" covers {start_time} to {end_time} on the ORIGINAL source timeline.
Use source timestamps within that range; do not reset the time origin to zero.

Identify visible camera cuts and transitions. A shot is a continuous view between edits; a change in subject
within one continuous recording does not create another shot. For dissolves, estimate the transition midpoint.
Sampling limits what can be seen. Do not invent cuts or claim that boundaries are frame accurate.
A person entering/leaving, a new action, dialogue, a flashlight sweep, lighting change,
camera movement or a change of framing during the SAME take is not an edit. Describe
the development of the entire continuous take in one shot description. Create a new
shot only for a visible edit/transition to another camera view.

For each shot, beginning with shot number {shot_number_offset}, return:
- start_time/end_time: source-relative MM:SS.mmm or HH:MM:SS.mmm, end strictly after start.
- shot_type: e.g. wide, medium, close-up, extreme close-up, over-the-shoulder, POV, aerial, insert, two-shot.
- camera_movement: visible movement (static, pan, tilt, tracking, handheld, zoom); avoid guessing equipment.
- visual_description: precise visible subjects, composition, activity and setting.
- subjects, actions: specific observable objects/people and what happens.
- tags: 5-12 concise searchable concepts including subject, action, setting, style and editorial qualities.
- dominant_colors and mood: visible palette and tentative creative tone, not a person's hidden mental state.
- location: descriptive setting; name a place only when explicit evidence identifies it.
- visible_text: exact readable on-screen words; omit illegible text instead of guessing.
- logos: clearly identifiable brand/logo names only; leave empty when uncertain.
- audio_notes: actually audible music, dialogue, ambient sound or silence.
- transcript: only intelligible words actually SPOKEN, verbatim; never copy visible captions as speech.
- evidence: short directly observable supporting details with modality (visual/audio/both),
  description, start_time and end_time within the shot. These are observations, not reasoning.
- confidence: an optional 0-1 uncalibrated estimate; null when unsure.
- analysis_warnings: ambiguous details, unexamined intervals or uncertain cuts.
- start_boundary: "cut" only for an observed editorial cut/transition, "continuation"
  when the provided look-back footage confirms the same continuous take, otherwise "unknown".
- section_numbers: leave empty; the application assigns processing-section membership.
- slates: read any visible film slate/clapperboard, including head and tail slates.
  Record its visible start_time/end_time within this shot, production_title, scene,
  take, slate_shot (the board's shot identifier, NOT the application's shot_number),
  camera_roll, camera identifier, director, cinematographer (also labeled DP/DOP),
  date, sound_roll, displayed timecode, and written frame_rate. Preserve letters,
  leading zeroes, punctuation and circled take markings as written. Keep raw_text
  as a transcription of readable board text, and notes for ambiguity or partial
  legibility. Read each field independently: empty strings for unreadable/missing
  values. Never infer slate fields from filenames, actors, dialogue, or prior
  context. Readable words without a clear field label belong in raw_text only.
  A slate appearing/disappearing or clapping does not create a camera cut. Keep
  multiple different slates as separate observations. Never treat the board's
  date, timecode or frame rate as verified source-file technical metadata.
  Return [] if no slate is visible; slate text is visual metadata, not dialogue.
  Preserve a combined scene identifier like "9A" in scene; do not split its letter
  into slate_shot. Set slate_shot only when a separate SHOT field is readable.
  ROLL may list more than one camera's roll: retain all readable values together.
  A person's name beside CAMERA identifies the credited cinematographer; it is
  not a camera letter/model. Record bounding_box as [x,y,width,height], normalized
  0–1 against the complete frame, around the board; [] if unsure.

Keep shots in chronological order without overlap. Do not manufacture observations to fill a coverage gap.
Empty values are appropriate for absent or unknown attributes. All generated metadata remains unreviewed.
Return the provided JSON schema.
"""
