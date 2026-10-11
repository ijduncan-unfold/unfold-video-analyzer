from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# --- Pass 1: Flash Scene Detection (intermediate models) ---

class SceneOutline(BaseModel):
    """Stage 1 output: scene boundary only, no shots yet."""
    scene_number: int
    scene_title: str
    scene_description: str
    start_time: str  # MM:SS
    end_time: str    # MM:SS


class SceneDetectionResult(BaseModel):
    """Full Stage 1 output."""
    total_duration: str
    total_scenes: int
    scenes: list[SceneOutline]
    analysis_warnings: list[str] = Field(default_factory=list)


class ShotDetectionResult(BaseModel):
    """Stage 2 output for a single scene."""
    shots: list[Shot]


# --- Pass 1: Flash Scene Detection ---

class Evidence(BaseModel):
    """A model observation linked to a source interval, not a verification claim."""
    modality: Literal["visual", "audio", "both"] = "visual"
    description: str
    start_time: str
    end_time: str


class Shot(BaseModel):
    shot_number: int
    start_time: str  # MM:SS.mmm or HH:MM:SS.mmm on the source timeline
    end_time: str
    shot_type: str
    camera_movement: str
    visual_description: str
    audio_notes: str
    subjects: list[str] = Field(default_factory=list)
    dominant_colors: list[str] = Field(default_factory=list)
    mood: str = ""
    tags: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    logos: list[str] = Field(default_factory=list)
    location: str = ""
    transcript: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_basis: str = "model_estimate_uncalibrated"
    review_status: str = "unreviewed"
    timestamp_accuracy: str = "approximate"
    analysis_warnings: list[str] = Field(default_factory=list)
    start_boundary: Literal['cut', 'continuation', 'unknown'] = 'unknown'
    section_numbers: list[int] = Field(default_factory=list)


class Scene(BaseModel):
    scene_number: int
    scene_title: str
    scene_description: str
    start_time: str  # MM:SS
    end_time: str  # MM:SS
    shots: list[Shot] = Field(default_factory=list)


class FlashAnalysis(BaseModel):
    total_duration: str  # MM:SS
    total_shots: int
    total_scenes: int
    scenes: list[Scene]
    analysis_warnings: list[str] = Field(default_factory=list)
    provider: str = "gemini"
    model: str = ""
    schema_version: str = "2.0"
    timestamp_accuracy: str = "approximate"


# --- Pass 2: Pro Deep Analysis ---

class CinematographyDetail(BaseModel):
    lighting: str = ""
    color_palette: str = ""
    composition: str = ""
    production_design: str = ""
    visual_effects: str = ""


class MotionEditingDetail(BaseModel):
    pacing: str = ""
    transitions: str = ""
    camera_technique: str = ""


class AudioDetail(BaseModel):
    dialogue: str = ""
    music: str = ""
    sound_design: str = ""


class NarrativeDetail(BaseModel):
    story_beat: str = ""
    emotional_tone: str = ""
    text_graphics: str = ""
    brands_products: str = ""
    people: str = ""


class SceneDeepAnalysis(BaseModel):
    scene_number: int
    visual_analysis: CinematographyDetail = Field(default_factory=CinematographyDetail)
    motion_editing: MotionEditingDetail = Field(default_factory=MotionEditingDetail)
    audio_analysis: AudioDetail = Field(default_factory=AudioDetail)
    narrative_context: NarrativeDetail = Field(default_factory=NarrativeDetail)
    evidence: list[Evidence] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    review_status: str = "unreviewed"


# --- Pass 3: Full Summary ---

class VideoSummary(BaseModel):
    executive_summary: str = ""
    genre_category: str = ""
    production_value: str = ""
    target_audience: str = ""
    visual_style: str = ""
    key_themes: list[str] = Field(default_factory=list)
    total_runtime: str = ""
    total_scenes: int = 0
    total_shots: int = 0
    notable_observations: str = ""
