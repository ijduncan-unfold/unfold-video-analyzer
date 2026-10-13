from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

ReviewStatus = Literal['unreviewed', 'reviewed', 'needs_changes']
RightsStatus = Literal['unknown', 'cleared', 'restricted']


class AssetMetadata(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='', max_length=250)
    client: str = Field(default='', max_length=200)
    project: str = Field(default='', max_length=200)
    campaign: str = Field(default='', max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default='', max_length=10000)
    rights_status: RightsStatus = 'unknown'
    review_status: ReviewStatus = 'unreviewed'
    collections: list[str] = Field(default_factory=list, max_length=100)

    @field_validator('tags', 'collections')
    @classmethod
    def clean_terms(cls, values):
        result = []
        for value in values:
            value = value.strip()
            if len(value) > 100:
                raise ValueError('Tags and collection names must be under 100 characters')
            if value and value.casefold() not in {v.casefold() for v in result}:
                result.append(value)
        return result


class ShotAnnotation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tags: list[str] = Field(default_factory=list, max_length=100)
    notes: str = Field(default='', max_length=10000)
    review_status: ReviewStatus = 'unreviewed'

    @field_validator('tags')
    @classmethod
    def clean_terms(cls, value):
        return AssetMetadata.clean_terms(value)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['flash_only', 'flash_pro'] = 'flash_only'
    fps: float = Field(default=1, ge=0.5, le=5)
    custom_prompt: str = Field(default='', max_length=10000)
    proxy_mode: Literal['auto', 'always', 'off'] = 'auto'
    shot_mode: Literal['auto', 'single_take', 'detect_cuts'] = 'auto'


class FolderName(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Enter a folder name')
        return value


class FolderMove(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_ids: list[str] = Field(min_length=1, max_length=500)
    folder_id: str | None = Field(default=None, max_length=100)


class SourceStatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_ids: list[str] = Field(min_length=1, max_length=100)


class MediaExportSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_ids: list[str] = Field(min_length=1, max_length=1000)


class MediaExportRequest(MediaExportSelection):
    title: str = Field(default='Video Analyzer selects', min_length=1, max_length=200)
    layout: Literal['timeline', 'bin'] = 'timeline'
    include_metadata: bool = True
    analysis_versions: dict[str, str] = Field(max_length=1000)
