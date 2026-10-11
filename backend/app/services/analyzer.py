"""Durable, evidence-bearing analysis stages; execution is owned by the job runner."""
import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import AsyncGenerator

from app.config import settings
from app.models.analysis import FlashAnalysis, Scene
from app.services.analysis_support import (
    format_time, processing_sections, validate_scenes, validate_shots, time_to_seconds,
)
from app.services.cost_estimator import estimate_cost
from app.services.custom_pass import run_custom_pass
from app.services.flash_pass import run_scene_detection, run_shot_detection
from app.services.job_store import Job, get_job, update_job
from app.services.pro_pass import run_pro_scene
from app.services.summary_pass import run_summary_pass
from app.services.thumbnail_service import extract_thumbnails
from app.services.notes_synthesis import synthesize_notes
from app.services.proxy_policy import proxy_decision
from app.services.camera_media import viewing_media
from app.utils.video_formats import get_mime_type

logger = logging.getLogger(__name__)


def _event(event_name: str, **data) -> dict:
    return {"event": event_name, "data": json.dumps(data)}


def _failure(stage: str, error: Exception) -> str:
    """Provider messages can contain credential-bearing URLs or private input."""
    logger.warning("%s failed (%s)", stage, type(error).__name__)
    if isinstance(error, (ValueError, TypeError)):
        return f"{stage} did not return valid analysis. Retry this asset or use another configured model."
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return f"{stage} timed out. Retry this asset."
    return f"{stage} could not complete. Check provider access, model availability and quota, then retry."


def _shot_spans(result: dict | None) -> dict:
    spans = {}
    for scene in (result or {}).get("scenes", []):
        for shot in scene.get("shots", []):
            try:
                spans[str(shot["shot_number"])] = (time_to_seconds(shot["start_time"]),
                                                   time_to_seconds(shot["end_time"]))
            except (KeyError, ValueError):
                continue
    return spans


async def run_analysis(
    job: Job, fps: float = 1.0, mode: str = "flash_pro", custom_prompt: str | None = None,
    api_key: str | None = None,
    media=None,
    proxy_mode='auto',
) -> AsyncGenerator[dict, None]:
    """Yield compatible progress events while persisting every completed stage.

    The runner prepares a remote file or provides a lazy, bounded upload manager.
    Model proposals are stored separately from human annotations and metadata.
    """
    usage_records: list[dict] = []
    warnings = ["AI-generated timestamps are approximate; review metadata before editorial use."]
    if job.technical.get('viewing_proxy', {}).get('decoder') == 'sony_f55_raw':
        warnings.append('Sony RAW analysis uses an SDK-developed viewing proxy with an approximate display transform; color results describe the proxy, not calibrated RAW color.')
    elif job.technical.get('viewing_proxy', {}).get('generated'):
        warnings.append('Analysis uses a generated camera viewing proxy; color results describe its development settings, not a final RAW grade.')
    flash_dict = None
    deep_results = []
    summary_dict = custom_result_dict = None
    transcript = []
    previous_config = dict(job.analysis_config)
    upload_mime = get_mime_type(job.viewing_path) if job.viewing_path else job.mime_type
    analysis_progress = {
        "stage": "indexing", "completed_sections": 0, "total_sections": 0,
        "failed_sections": 0, "completed_shots": 0,
        "processed_seconds": 0.0, "total_seconds": None,
    }
    archived = False
    previous_annotations, previous_spans = {}, {}

    def persist(**changes):
        # update_job reloads the durable record, so unrelated human edits survive.
        analysis_progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        update_job(job.job_id, warnings=list(dict.fromkeys(warnings)),
                   cost_estimate=estimate_cost(usage_records=usage_records),
                   analysis_progress=dict(analysis_progress), **changes)

    def note_usage(record):
        # Intermediate synthesis calls remain accounted even if a later call fails.
        usage_records.append(record)
        persist()

    def persist_replacement(result: dict):
        """Archive the prior run once and bind reviews only to identical intervals."""
        nonlocal archived, previous_annotations, previous_spans
        latest = get_job(job.job_id)
        if latest is None:
            raise ValueError("Asset no longer exists")
        changes = {}
        if not archived:
            previous_annotations = dict(latest.shot_annotations)
            previous_spans = _shot_spans(latest.flash_result)
            # Empty checkpoints from this attempt are not a historical run.
            if job.flash_result and latest.flash_result:
                snapshot = {
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "analysis_config": previous_config, "flash_result": latest.flash_result,
                    "deep_results": latest.deep_results, "summary": latest.summary,
                    "shot_annotations": latest.shot_annotations, "shot_matches": latest.shot_matches,
                    "transcript": latest.transcript, "custom_result": latest.custom_result,
                    "cost_estimate": job.cost_estimate,
                }
                changes["analysis_history"] = (list(latest.analysis_history) + [snapshot])[-10:]
            changes.update(deep_results=[], summary=None, shot_matches=[], custom_result=None,
                           analysis_config=config)
            archived = True
        new_spans = _shot_spans(result)
        annotations = {key: value for key, value in previous_annotations.items()
                       if key in new_spans and previous_spans.get(key) == new_spans[key]}
        latest_spans = _shot_spans(latest.flash_result)
        annotations.update({key: value for key, value in latest.shot_annotations.items()
                            if key in new_spans and latest_spans.get(key) == new_spans[key]})
        persist(flash_result=result, transcript=transcript, shot_annotations=annotations, **changes)

    try:
        if mode not in {"flash_only", "flash_pro"}:
            raise ValueError("Unknown analysis mode")
        if not math.isfinite(fps) or not 0.1 <= fps <= 30:
            raise ValueError("Sampling FPS must be between 0.1 and 30")
        duration = job.technical.get("duration_seconds")
        if duration is not None:
            duration = time_to_seconds(duration)
            if duration <= 0:
                raise ValueError("Source duration must be positive")
        config = dict(job.analysis_config)
        config.update(proxy_decision(job, proxy_mode))
        config['proxy_used'] = media is not None
        for key in ('upload_part_seconds', 'proxy_max_resolution', 'summary_input'):
            config.pop(key, None)
        if media is None:
            config['upload_strategy'] = ('youtube_url' if job.youtube_url else
                                         'viewing_proxy' if job.viewing_path else 'original')
        config.update({"provider": "gemini", "analysis_model": settings.gemini_analysis_model,
                       "deep_model": settings.gemini_deep_model, "fps": fps, "mode": mode,
                       "schema_version": "2.0", "processing": "static",
                       "timestamp_accuracy": "approximate", "grouping": "processing_sections",
                       "section_duration_seconds": 30,
                       "started_at": datetime.now(timezone.utc).isoformat()})
        if media:
            config.update(upload_strategy='segmented_proxy', upload_part_seconds=600,
                          proxy_max_resolution='1280x720', summary_input='saved_section_evidence')
            warnings.append('Proxy analysis uses temporary up-to-720p parts. Film-wide summary and custom '
                            'briefs synthesize saved section evidence; omissions in that evidence may carry through.')
        analysis_progress["total_seconds"] = duration
        analysis_progress["started_at"] = config["started_at"]
        persist(status="analyzing", error=None, current_pass=1, current_scene=None,
                progress="Indexing shots")
        yield _event("pass_start", **{"pass": 1, "name": "Indexing shots"})
        if duration is None:
            # Probe-less legacy assets need a safe fallback before choosing bounds.
            persist(progress="Estimating source duration before indexing")
            scenes_result, usage = await run_scene_detection(
                job.file_uri, upload_mime, api_key=api_key, duration_seconds=None,
            )
            usage_records.append(usage)
            scenes_result = validate_scenes(scenes_result)
            duration = time_to_seconds(scenes_result.total_duration)
            warnings.extend(scenes_result.analysis_warnings)
            warnings.append("Source duration was unavailable; processing bounds use an AI duration estimate.")
            config["duration_source"] = "model_estimate"
        else:
            config["duration_source"] = "media_probe"
        sections = processing_sections(duration)
        total_sections = len(sections)
        analysis_progress.update(total_sections=total_sections, total_seconds=duration)
        persist(total_scenes=total_sections)
        yield _event("sections_planned", total_sections=total_sections,
                     total_duration=format_time(duration), grouping="processing_sections")
        assembled_scenes = []
        shot_counter = 1
        for outline in sections:
            start, end = time_to_seconds(outline.start_time), time_to_seconds(outline.end_time)
            persist(current_scene=outline.scene_number,
                    progress=f"Indexing section {outline.scene_number} of {total_sections}")
            yield _event("shot_detection_progress", scene=outline.scene_number,
                         section=outline.scene_number, total=total_sections,
                         scene_title=outline.scene_title, grouping="processing_sections")
            try:
                uri, mime, offset = (await media.get(start, end) if media
                                     else (job.file_uri, upload_mime, 0))
                shots, usage = await run_shot_detection(
                    uri, mime, outline, shot_counter, fps, api_key=api_key,
                    **({'source_offset': offset} if media else {}),
                )
                usage_records.append(usage)
                shots = validate_shots(shots, start, end, shot_counter)
                for shot in shots:
                    if (start > 0 and time_to_seconds(shot.start_time) == start) or (
                        outline.scene_number < total_sections and time_to_seconds(shot.end_time) == end
                    ):
                        shot.analysis_warnings.append(
                            "This shot touches a processing boundary and may continue in another section; "
                            "the boundary is not a detected editorial cut."
                        )
                    if job.technical.get("has_audio") is False:
                        if shot.transcript or any(e.modality in {"audio", "both"} for e in shot.evidence):
                            shot.analysis_warnings.append("Audio claims removed: source has no audio stream.")
                        shot.transcript = ""
                        shot.audio_notes = "No audio stream in source."
                        shot.evidence = [e for e in shot.evidence if e.modality == "visual"]
                    if shot.transcript.strip():
                        transcript.append({"shot_number": shot.shot_number,
                                           "start_time": shot.start_time, "end_time": shot.end_time,
                                           "text": shot.transcript, "source": "model_transcription",
                                           "review_status": "unreviewed", "timestamp_accuracy": "approximate"})
                    warnings.extend(f"Shot {shot.shot_number}: {w}" for w in shot.analysis_warnings)
                analysis_progress["completed_sections"] += 1
                analysis_progress["processed_seconds"] = round(
                    analysis_progress["processed_seconds"] + end - start, 3,
                )
            except Exception as error:
                message = _failure(f"Shot analysis for section {outline.scene_number}", error)
                warnings.append(message)
                analysis_progress["failed_sections"] += 1
                shots = []
                yield _event("error_event", message=message, recoverable=True)
            assembled_scenes.append(Scene(
                scene_number=outline.scene_number, scene_title=outline.scene_title,
                scene_description=outline.scene_description, start_time=outline.start_time,
                end_time=outline.end_time, shots=shots,
            ))
            shot_counter += len(shots)
            analysis_progress["completed_shots"] = shot_counter - 1
            flash_result = FlashAnalysis(
                total_duration=format_time(duration), total_scenes=len(assembled_scenes),
                total_shots=shot_counter - 1, scenes=assembled_scenes,
                analysis_warnings=list(dict.fromkeys(warnings)), model=settings.gemini_analysis_model,
            )
            flash_dict = flash_result.model_dump()
            flash_dict["grouping"] = "processing_sections"
            flash_dict["section_duration_seconds"] = 30
            if shot_counter > 1:
                persist_replacement(flash_dict)
            elif not job.flash_result:
                persist(flash_result=flash_dict, transcript=transcript)
            else:
                persist()
            yield _event("section_complete", section=outline.scene_number,
                         successful=bool(shots), result=flash_dict,
                         analysis_progress=dict(analysis_progress))
            if shots and job.local_path:
                # Publish the durable shot snapshot before any thumbnail work.
                # Finish this section's thumbnails before processing another one.
                try:
                    await asyncio.wait_for(extract_thumbnails(
                        viewing_media(job), job.job_id, [assembled_scenes[-1].model_dump()], settings.upload_dir,
                    ), timeout=45)
                    persist()
                    yield _event("thumbnails_ready", job_id=job.job_id, section=outline.scene_number)
                except Exception as error:
                    warnings.append(_failure(f"Thumbnails for section {outline.scene_number}", error))
                    persist()
        if shot_counter == 1:
            raise ValueError("No valid shot metadata was generated")
        persist(flash_result=flash_dict, total_scenes=len(assembled_scenes))
        yield _event("pass_complete", **{"pass": 1, "result": flash_dict})

        if mode == "flash_pro":
            analysis_progress["stage"] = "enriching"
            persist(current_pass=2, progress="Adding detailed notes to indexed sections")
            yield _event("pass_start", **{"pass": 2, "name": "Detailed section analysis"})
            for scene in flash_result.scenes:
                if not scene.shots:
                    continue
                persist(current_scene=scene.scene_number,
                        progress=f"Detailed notes for section {scene.scene_number} of {total_sections}")
                yield _event("scene_start", scene=scene.scene_number, total=total_sections,
                             section=scene.scene_number, grouping="processing_sections")
                try:
                    uri, mime, offset = (await media.get(time_to_seconds(scene.start_time),
                                                       time_to_seconds(scene.end_time)) if media
                                         else (job.file_uri, upload_mime, 0))
                    deep, usage = await asyncio.wait_for(
                        run_pro_scene(uri, mime, scene, api_key=api_key,
                                      **({'source_offset': offset} if media else {})), timeout=120,
                    )
                    usage_records.append(usage)
                    if job.technical.get("has_audio") is False:
                        deep.audio_analysis.dialogue = ""
                        deep.audio_analysis.music = ""
                        deep.audio_analysis.sound_design = "No audio stream in source."
                        deep.evidence = [e for e in deep.evidence if e.modality == "visual"]
                    deep_dict = deep.model_dump()
                    deep_results.append(deep_dict)
                    warnings.extend(deep.analysis_warnings)
                    persist(deep_results=deep_results)
                    yield _event("scene_complete", scene=scene.scene_number, result=deep_dict)
                except Exception as error:
                    message = _failure(f"Detailed analysis for section {scene.scene_number}", error)
                    warnings.append(message)
                    persist()
                    yield _event("error_event", message=message, recoverable=True)
            yield _event("pass_complete", **{"pass": 2})
            analysis_progress["stage"] = "summarizing"
            persist(current_pass=3, progress="Writing the editorial summary")
            yield _event("pass_start", **{"pass": 3, "name": "Editorial summary"})
            try:
                if media:
                    summary, _ = await synthesize_notes(flash_result, deep_results, api_key,
                                                       on_usage=note_usage)
                else:
                    summary, usage = await asyncio.wait_for(
                        run_summary_pass(job.file_uri, upload_mime, flash_result, api_key=api_key), timeout=120,
                    )
                    usage_records.append(usage)
                summary_dict = summary.model_dump()
                persist(summary=summary_dict)
                yield _event("pass_complete", **{"pass": 3, "result": summary_dict})
            except Exception as error:
                message = _failure("Summary generation", error)
                warnings.append(message)
                persist()
                yield _event("error_event", message=message, recoverable=True)

        if custom_prompt:
            analysis_progress["stage"] = "custom"
            persist(current_pass=4, custom_prompt=custom_prompt, progress="Running the custom analysis brief")
            yield _event("pass_start", **{"pass": 4, "name": "Custom analysis"})
            try:
                if media:
                    custom_result_dict, _ = await synthesize_notes(
                        flash_result, deep_results, api_key, custom_prompt=custom_prompt,
                        on_usage=note_usage)
                else:
                    custom_result_dict, usage = await asyncio.wait_for(
                        run_custom_pass(job.file_uri, upload_mime, custom_prompt, api_key=api_key), timeout=120,
                    )
                    usage_records.append(usage)
                persist(custom_result=custom_result_dict)
                yield _event("custom_complete", result=custom_result_dict)
            except Exception as error:
                message = _failure("Custom analysis", error)
                warnings.append(message)
                persist()
                yield _event("error_event", message=message, recoverable=True)
        if analysis_progress["failed_sections"]:
            message = (f"{analysis_progress['failed_sections']} sections could not be analyzed. "
                       "Completed shots are saved; retry to fill the missing coverage.")
            analysis_progress["stage"] = "error"
            persist(status="error", error=message, current_pass=None, current_scene=None,
                    progress="Indexing finished with gaps; completed shots saved")
            yield _event("error_event", message=message, recoverable=False)
            return
        analysis_progress["stage"] = "complete"
        persist(status="complete", current_pass=None, current_scene=None,
                deep_results=deep_results, summary=summary_dict, custom_result=custom_result_dict,
                progress="Analysis ready for review")
        yield _event("analysis_complete", flash=flash_dict, deep=deep_results, summary=summary_dict,
                     custom=custom_result_dict, warnings=list(dict.fromkeys(warnings)),
                     cost_estimate=estimate_cost(usage_records=usage_records))
    except Exception as error:
        message = _failure("Analysis", error)
        warnings.append(message)
        analysis_progress["stage"] = "error"
        persist(status="error", error=message, progress="Analysis needs attention; completed stages saved")
        yield _event("error_event", message=message, recoverable=False)
    except asyncio.CancelledError:
        analysis_progress["stage"] = "cancelled"
        persist(status="error", error="Analysis cancelled. Completed shots are saved.",
                current_pass=None, current_scene=None, progress="Cancelled; completed shots saved")
        raise
