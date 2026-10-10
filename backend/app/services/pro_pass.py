import asyncio
import logging

from google.genai import types

from app.config import settings
from app.models.analysis import SceneDeepAnalysis, Scene
from app.prompts.pro_prompt import PRO_SCENE_ANALYSIS_PROMPT
from app.services.gemini_client import get_client
from app.services.analysis_support import (
    EVIDENCE_INSTRUCTION, clip_offset, extract_usage, response_text,
    time_to_seconds, validate_evidence, format_time,
)

logger = logging.getLogger(__name__)


def _parse_time_to_seconds(time_str: str) -> float:
    return time_to_seconds(time_str)


async def run_pro_scene(
    file_uri: str, mime_type: str, scene: Scene, api_key: str | None = None,
    source_offset: float = 0.0,
) -> tuple[SceneDeepAnalysis, dict]:
    """Run Pass 2 for a single scene: deep analysis with Gemini Pro.

    Returns (parsed_result, usage_metadata_dict).
    """
    client = get_client(api_key)

    source_offset = time_to_seconds(source_offset)
    start_secs = _parse_time_to_seconds(scene.start_time) - source_offset
    end_secs = _parse_time_to_seconds(scene.end_time) - source_offset
    if start_secs < 0 or end_secs <= start_secs:
        raise ValueError('Invalid uploaded-part interval')

    # Build content with video clip
    contents = types.Content(
        parts=[
            types.Part(
                file_data=types.FileData(
                    file_uri=file_uri,
                    mime_type=mime_type,
                ),
                video_metadata=types.VideoMetadata(
                    start_offset=clip_offset(start_secs),
                    end_offset=clip_offset(end_secs),
                ),
            ),
            types.Part(
                text=f"This is section {scene.scene_number}: \"{scene.scene_title}\" ({format_time(start_secs)} - {format_time(end_secs)} on the uploaded file timeline).\n\n{PRO_SCENE_ANALYSIS_PROMPT}"
            ),
        ]
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=settings.gemini_deep_model,
        contents=contents,
        config=types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
            system_instruction=EVIDENCE_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=SceneDeepAnalysis,
        ),
    )

    usage = extract_usage(response, settings.gemini_deep_model, "deep_analysis")
    parsed = SceneDeepAnalysis.model_validate_json(response_text(response))
    parsed.scene_number = scene.scene_number
    parsed.evidence, warnings = validate_evidence(parsed.evidence, start_secs, end_secs)
    for evidence in parsed.evidence:
        evidence.start_time = format_time(time_to_seconds(evidence.start_time) + source_offset)
        evidence.end_time = format_time(time_to_seconds(evidence.end_time) + source_offset)
    parsed.analysis_warnings.extend(warnings)
    parsed.review_status = "unreviewed"
    logger.info(f"Pro pass complete for scene {scene.scene_number}")
    return parsed, usage
