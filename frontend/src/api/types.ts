// --- Pass 1: Flash Analysis ---

export interface Slate {
  bounding_box?: number[]
  start_time: string
  end_time: string
  production_title?: string
  scene?: string
  take?: string
  slate_shot?: string
  camera_roll?: string
  camera?: string
  director?: string
  cinematographer?: string
  date?: string
  sound_roll?: string
  timecode?: string
  frame_rate?: string
  raw_text?: string
  notes?: string
  review_status?: string
}

export interface CosplayIdentification {
  character: string
  franchise: string
  costume_variant: string
  visible_details: string
  activity: string
  possible_characters: string[]
  identification: 'suggested' | 'confirmed' | 'unknown'
}

export interface Shot {
  cosplay?: CosplayIdentification[]
  shot_number: number
  thumbnail_url?: string | null
  start_time: string
  end_time: string
  shot_type: string
  camera_movement: string
  visual_description: string
  audio_notes: string
  subjects: string[]
  dominant_colors: string[]
  mood: string
  tags?: string[]
  actions?: string[]
  visible_text?: string[]
  logos?: string[]
  location?: string
  transcript?: string
  evidence?: { modality: 'visual' | 'audio' | 'both'; description: string; start_time: string; end_time: string }[]
  confidence?: number | null
  confidence_basis?: string
  timestamp_accuracy?: string
  analysis_warnings?: string[]
  slates?: Slate[]
}

export interface Scene {
  scene_number: number
  scene_title: string
  scene_description: string
  start_time: string
  end_time: string
  shots: Shot[]
}

export interface FlashAnalysis {
  total_duration: string
  total_shots: number
  total_scenes: number
  scenes: Scene[]
  model?: string
  schema_version?: string
  timestamp_accuracy?: string
  analysis_warnings?: string[]
}

// --- Pass 2: Deep Analysis ---

export interface CinematographyDetail {
  lighting: string
  color_palette: string
  composition: string
  production_design: string
  visual_effects: string
}

export interface MotionEditingDetail {
  pacing: string
  transitions: string
  camera_technique: string
}

export interface AudioDetail {
  dialogue: string
  music: string
  sound_design: string
}

export interface NarrativeDetail {
  story_beat: string
  emotional_tone: string
  text_graphics: string
  brands_products: string
  people: string
}

export interface SceneDeepAnalysis {
  scene_number: number
  visual_analysis: CinematographyDetail
  motion_editing: MotionEditingDetail
  audio_analysis: AudioDetail
  narrative_context: NarrativeDetail
}

// --- Pass 3: Summary ---

export interface VideoSummary {
  executive_summary: string
  genre_category: string
  production_value: string
  target_audience: string
  visual_style: string
  key_themes: string[]
  total_runtime: string
  total_scenes: number
  total_shots: number
  notable_observations: string
}

// --- API Responses ---

export interface UploadResponse {
  job_id: string
  file_id: string
  filename: string
  size_bytes: number
  mime_type: string
  status: string
}
