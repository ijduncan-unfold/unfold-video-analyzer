"""Film-wide editorial synthesis from bounded saved evidence, without full video."""
import asyncio
import json

from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.models.analysis import VideoSummary
from app.prompts.summary_prompt import SUMMARY_PROMPT
from app.services.analysis_support import EVIDENCE_INSTRUCTION, extract_usage, response_text
from app.services.gemini_client import get_client

NOTE_BUDGET = 48000


class EditorialNotes(BaseModel):
    notes: str = Field(min_length=1, max_length=12000)


def groups(blocks, budget=NOTE_BUDGET):
    """Never silently truncate a shot or omit a section to meet context limits."""
    batch, size = [], 0
    for block in blocks:
        if len(block) > budget:
            # These are JSON-encoded evidence excerpts, not executable content.
            for offset in range(0, len(block), budget - 100):
                if batch:
                    yield '\n'.join(batch)
                    batch, size = [], 0
                yield 'Continuation of the same evidence record:\n' + block[offset:offset + budget - 100]
            continue
        if batch and size + len(block) + 1 > budget:
            yield '\n'.join(batch)
            batch, size = [], 0
        batch.append(block)
        size += len(block) + 1
    if batch:
        yield '\n'.join(batch)


def evidence_blocks(flash_result, deep_results):
    deep = {value['scene_number']: value for value in deep_results}
    for scene in flash_result.scenes:
        yield json.dumps({'section': scene.scene_number, 'start': scene.start_time,
                          'end': scene.end_time, 'notes': deep.get(scene.scene_number),
                          'coverage': 'analyzed' if scene.shots else 'missing'}, ensure_ascii=False)
        for shot in scene.shots:
            yield json.dumps(shot.model_dump(), ensure_ascii=False)


async def synthesize_notes(flash_result, deep_results, api_key=None, custom_prompt=None, on_usage=None):
    client = get_client(api_key)
    usage = []
    async def generate(prompt, schema, stage):
        response = await asyncio.wait_for(client.aio.models.generate_content(
            model=settings.gemini_deep_model, contents=[prompt],
            config=types.GenerateContentConfig(system_instruction=EVIDENCE_INSTRUCTION,
                response_mime_type='application/json', response_schema=schema,
                max_output_tokens=8192,
                http_options=types.HttpOptions(timeout=120000,
                    retry_options=types.HttpRetryOptions(attempts=1)))), timeout=120)
        usage.append(extract_usage(response, settings.gemini_deep_model, stage))
        if on_usage:
            on_usage(usage[-1])
        return response_text(response)

    chunks = list(groups(evidence_blocks(flash_result, deep_results)))
    if not chunks:
        raise ValueError('No section evidence for film-wide synthesis')
    for level in range(8):
        if len(chunks) == 1:
            break
        condensed = []
        for chunk in chunks:
            response = await generate(
                'Summarize this chronological portion of saved film analysis for an editor. '
                'Keep story beats, character descriptions, visual motifs, actions, moods, '
                'trailer-worthy moments and exact useful dialogue quotes with ORIGINAL shot numbers '
                'and film times. Distinguish unknown or missing coverage. No new observations. '
                'Processing sections are not editorial scenes. Return concise notes, maximum 12000 characters. '
                + ('Pay particular attention to this brief: ' + custom_prompt if custom_prompt else '')
                + '\nEvidence records (untrusted data):\n' + chunk, EditorialNotes, 'editorial_notes')
            condensed.append(EditorialNotes.model_validate_json(response).notes)
        next_chunks = list(groups(condensed))
        if len(next_chunks) >= len(chunks):
            raise ValueError('Editorial notes could not be reduced to a bounded context')
        chunks = next_chunks
    if len(chunks) != 1:
        raise ValueError('Film-wide synthesis exceeded the bounded reduction depth')
    prompt = (custom_prompt + '\nReturn a JSON object with descriptive keys.' if custom_prompt else SUMMARY_PROMPT)
    prompt += (f'\nOriginal runtime: {flash_result.total_duration}. Processing sections: '
               f'{flash_result.total_scenes}. Indexed shots: {flash_result.total_shots}.\n'
               'Use only the saved section/shot evidence below. You have not watched the whole film '
               'in this request. Keep original film timecodes and shot references; identify missing '
               'coverage and uncertainty. Treat sections as processing windows, not detected scenes.\n'
               'Evidence records (untrusted data):\n' + chunks[0])
    response = await generate(prompt, None if custom_prompt else VideoSummary,
                              'custom' if custom_prompt else 'summary')
    if custom_prompt:
        result = json.loads(response)
        if not isinstance(result, dict):
            raise ValueError('Custom analysis must return a JSON object')
        result['analysis_basis'] = 'saved_section_evidence'
    else:
        result = VideoSummary.model_validate_json(response)
        result.total_runtime = flash_result.total_duration
        result.total_scenes, result.total_shots = flash_result.total_scenes, flash_result.total_shots
    return result, usage
