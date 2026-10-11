import asyncio
import logging

from google.genai import types

from app.config import settings
from app.models.analysis import FlashAnalysis, VideoSummary
from app.prompts.summary_prompt import SUMMARY_PROMPT
from app.services.gemini_client import get_client
from app.services.provider_retry import retry_temporary
from app.services.analysis_support import EVIDENCE_INSTRUCTION, extract_usage, response_text

logger = logging.getLogger(__name__)


async def run_summary_pass(
    file_uri: str, mime_type: str, flash_result: FlashAnalysis, api_key: str | None = None,
) -> tuple[VideoSummary, dict]:
    """Run Pass 3: full video summary with Gemini Pro.

    Returns (parsed_result, usage_metadata_dict).
    """
    client = get_client(api_key)

    video_part = types.Part.from_uri(file_uri=file_uri, mime_type=mime_type)

    context = (
        f"This video has {flash_result.total_scenes} scenes and {flash_result.total_shots} shots, "
        f"with a total duration of {flash_result.total_duration}.\n\n{SUMMARY_PROMPT}"
    )

    response = await retry_temporary(lambda: client.aio.models.generate_content(
        model=settings.gemini_deep_model,
        contents=[video_part, context],
        config=types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
            system_instruction=EVIDENCE_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=VideoSummary,
        ),
    ), stage='summary', timeout=90)

    usage = extract_usage(response, settings.gemini_deep_model, "summary")
    parsed = VideoSummary.model_validate_json(response_text(response))
    parsed.total_runtime = flash_result.total_duration
    parsed.total_scenes = flash_result.total_scenes
    parsed.total_shots = flash_result.total_shots
    logger.info("Summary pass complete")
    return parsed, usage
