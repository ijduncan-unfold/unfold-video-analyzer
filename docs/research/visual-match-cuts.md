# Visual match cuts: feature-film indexing

Research checked September 9, 2026. This is a proposed direction, with illustrative costs; no feature-film corpus benchmark has been run.

The useful product is a reusable visual index that finds promising outgoing and incoming frames across a library. Gemini can describe shots and judge candidate transitions, while local geometry and motion measurements make the matching more precise.

## What should match?

| Axis | Index and compare |
| --- | --- |
| Shape | Circles, ellipses, silhouettes, contours, negative space, orientation |
| Position | Subject or shape center in normalized screen coordinates |
| Scale | Fraction of the image occupied by the matching shape |
| Motion | Direction, speed, expansion, rotation, and camera versus subject movement |
| Color | Dominant colors and their spatial arrangement |
| Texture | Grain, surface detail, repeated patterns, visual density |
| Semantic contrast | Visually compatible images whose meanings create a useful transition |

A generic circle example: a close-up of an eye could match a tunnel opening or a moon when position and apparent size align. A wheel could transition into a spinning fan when shape, rotation direction, and motion phase align. The images need not depict the same object. These are generic examples: the actual Unfold reference asset was not supplied or analyzed.

Matching should be directional: the **end of shot A into the beginning of shot B** can work differently from the reverse. A whole-shot resemblance score cannot fully describe that transition.

## Recommended implementation

1. **Establish local timing.** Decode the source and detect candidate cuts at the source frame rate. Keep source timestamps and frame-rate metadata. Review dissolves and ambiguous transitions; a cut detector's candidates are not ground truth.
2. **Extract visual measurements.** Store representative frames plus dense windows around candidate boundaries. Compare contours, masks, shape position and size, color layout, and motion trajectories. A circle detector alone will miss perspective, occlusion, and irregular shapes, so combine geometric measurements with visual embeddings.
3. **Build a persistent index.** Use embeddings to retrieve a manageable candidate set across films, then rank with separately weighted shape, composition, and motion features. Retain the individual scores so users can prioritize a centered circle, matching movement, or a deliberate semantic contrast. Search this index without resending entire films.
4. **Use Gemini on the shortlist.** Send candidate frame pairs or short boundary clips for an editorial explanation and reranking. Show a playable A-to-B preview, suggested cut frames, and the matching reasons. Let the editor adjust the cut and save the result.

Exact cut-frame suggestions require decoded local frames and dense temporal comparison. Gemini's default static sampling is 1 fps and can lose fast action detail; 4 fps is still only one sample every quarter second. Higher media resolution helps small visual details, but increases tokens. [Google video processing guide](https://ai.google.dev/gemini-api/docs/video-understanding)

## Illustrative cost for a 120-minute film

Gemini 3.8 Flash standard pricing is **$0.75 per million input tokens and $3.75 per million output tokens, including thinking**, through December 31, 2026. Batch rates are half; published January 2027 rates double. [Official pricing](https://ai.google.dev/gemini-api/docs/pricing)

Google estimates about 100 video tokens/second at default low resolution, or 300 at high resolution, at 1 fps. Its token guide describes proportional scaling with custom FPS. [Token guide](https://ai.google.dev/gemini-api/docs/tokens)

| One video input pass; 7,200 seconds | Calculation | Approximate input cost |
| --- | --- | ---: |
| Low resolution, 1 fps | 7,200 × 100 = 720,000 tokens | $0.54 |
| Low resolution, 4 fps, coarse planning estimate | 7,200 × 100 × 4 = 2,880,000 tokens | $2.16 |
| High resolution, 1 fps | 7,200 × 300 = 2,160,000 tokens | $1.62 |

These are **input-only estimates**, before prompts, descriptions, thinking, retries, or further passes.

For example, assume 1,500 shots, 300 returned metadata tokens plus 200 thinking tokens per shot, and 100 grouped requests with 1,500 prompt/schema tokens each:

- Output: 1,500 × 500 tokens × $3.75/million = **$2.8125**.
- Prompts: 100 × 1,500 tokens × $0.75/million = **$0.1125**.
- One semantic indexing pass: approximately **$3.47 at 1 fps** or **$5.09 at 4 fps**.

These shot and token counts are assumptions. A preliminary **$5–15 inference allowance per film** permits selective rechecks, but is not a production quote. Storage, decoding, embeddings, and search infrastructure are additional.

The low-resolution visual frame floor is 66 tokens/frame. Removing audio therefore gives roughly 475,200 visual tokens at 1 fps or 1,900,800 at 4 fps, before timestamp/metadata overhead. With audio present, Google's component calculation adds 32 tokens/second; at 4 fps this is lower than the table's coarse proportional estimate. Actual provider usage is authoritative. Prompting the model to ignore dialogue does not remove the audio track. [Video token calculation](https://ai.google.dev/gemini-api/docs/video-understanding)

## Limits of the current application

- Update: the app now has an experimental local **Match cuts** workspace with per-project frame indexes, local color ranking and cached Gemini silhouettes/framing profiles for shape and composition, source regions, A/B previews, and cut-pair JSON export. It samples five points per shot and has no motion scoring or learned embedding retrieval yet. The implementation is separate from the Gemini pass below; see [current workflow and verification](../VISUAL_MATCH_CUTS.md).

- The current related-shot pass returns up to 20 model-suggested pairs within one film. It does not implement the geometric, motion, embedding, or cross-library index proposed here.
- Shot indexing now publishes results in 30-second processing sections, skipping the initial whole-film scene-detection request when source duration is known. Full analysis adds detailed section passes and a whole-video summary. The old analyzer's automatic related-shot matching pass has been removed; visual match-cut discovery runs separately when requested. The one-pass estimates above are not the cost of that entire pipeline. See [progressive analysis](../PROGRESSIVE_ANALYSIS.md) for measured first-result latency and remaining limits.
- Chunking is necessary for a detailed film index. Gemini 3.8 Flash allows 1,048,576 input tokens and 65,536 output tokens: default-low 120-minute input can fit, but the illustrative per-shot output cannot fit one response, and a 4 fps input also exceeds the input limit. [Model limits](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- Validate quality and invoices on a small representative film set before scaling. Measure shot coverage, boundary error, visual retrieval relevance, editor acceptance of transitions, and cost per accepted match. The existing synthetic provider smoke verifies API compatibility, not these editorial outcomes.

See the broader [model landscape](model-landscape.md) for provider alternatives and research sources.
