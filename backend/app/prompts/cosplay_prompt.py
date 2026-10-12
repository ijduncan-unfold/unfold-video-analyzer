COSPLAY_PROMPT = """
COSPLAY CATALOG MODE. Focus descriptions and tags on visible costumes, props and activity.
For each shot, fill cosplay with one entry for each distinct visible costume wearer.
Return [] when no cosplay is visible. Do not label every booth attendee as a cosplayer.
Identify the FICTIONAL CHARACTER depicted by a costume, never the real person's identity.
Use these fields:
- character: conventional character name when the costume is distinctive enough; otherwise empty.
  Use canonical spellings, e.g. Spider-Man (not Spiderman), Iron Man, Darth Vader.
- franchise: recognizable franchise, e.g. Marvel, DC, Star Wars, Naruto; empty if unclear.
- costume_variant: a recognizable variant, alternate identity or mashup. For a Miles Morales
  Spider-Man costume use character Spider-Man, franchise Marvel, costume_variant Miles Morales.
  Do not infer a variant from the wearer's skin, gender, age or other personal attributes.
- visible_details: direct costume evidence such as colors, mask, emblem, armor and props.
  Describe where the wearer is in the shot so several costumes can be distinguished.
- activity: observable actions, e.g. posing, dancing, waving, speaking.
- identification: suggested if a character is supported; unknown for ambiguous, original,
  obscured or unfamiliar costumes. NEVER confirmed: only a human can confirm an identification.
- possible_characters: plausible alternatives for an unknown identification, or [] if none.
  Keep these separate from character; do not force a guess or treat candidates as matches.
Avoid duplicated entries for the same wearer within a shot. Multiple people with the same
costume may each have an entry. Preserve uncertain visible descriptions even without a name.
Keep character/franchise guesses in cosplay, not in generic tags, subjects or descriptions;
those fields should describe observable costume details so corrections do not leave stale labels.
This mode needs no cinematography essay. Still preserve intelligible dialogue as transcript.
"""
