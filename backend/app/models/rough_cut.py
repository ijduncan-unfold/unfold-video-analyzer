"""Reviewable, source-referenced spoken-video edits."""
from typing import Literal
from pydantic import BaseModel, Field


class RoughCutRequest(BaseModel):
    mode: Literal['cleanup', 'condense'] = 'cleanup'
    target_seconds: int = Field(default=600, ge=30, le=7200)
    brief: str = Field(default='', max_length=3000)
    proxy_mode: Literal['auto', 'always', 'off'] = 'auto'


class SpeechMoment(BaseModel):
    start_time: str
    end_time: str
    text: str = Field(min_length=1, max_length=4000)
    kind: Literal['usable', 'false_start', 'repeated_take', 'off_topic'] = 'usable'
    visual: str = Field(default='', max_length=1500)
    reason: str = Field(default='', max_length=1500)


class SpeechMoments(BaseModel):
    moments: list[SpeechMoment] = Field(max_length=100)


class CutSelection(BaseModel):
    moment_ids: list[str] = Field(max_length=2000)
    rationale: str = Field(default='', max_length=3000)


class CutClipEdit(BaseModel):
    moment_id: str = Field(max_length=100)
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)


class RoughCutEdit(BaseModel):
    revision: str = Field(max_length=100)
    clips: list[CutClipEdit] = Field(min_length=1, max_length=2000)
