import json
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_api_key: str = ""
    gemini_analysis_model: str = "gemini-3.8-flash"
    gemini_deep_model: str = "gemini-3.8-flash"
    database_path: str = str(Path(__file__).parent.parent / "data" / "library.sqlite3")
    editor_token_path: str = str(Path(__file__).parent.parent / "data" / "premiere-token.txt")
    max_concurrent_analyses: int = 2
    shape_index_concurrency: int = Field(default=20, ge=1, le=20)
    composition_index_concurrency: int = Field(default=20, ge=1, le=20)
    upload_dir: str = str(Path(__file__).parent.parent / "uploads")
    max_file_size_mb: int = 2000
    allowed_origins: list[str] = ["http://localhost:5173"]

    model_config = {
        "env_file": () if os.environ.get("VIDEO_ANALYZER_DESKTOP") == "1" else [
            str(Path(__file__).parent.parent.parent / ".env"),  # project root
            str(Path(__file__).parent.parent.parent / ".env.local"),
            ".env",  # cwd fallback
            ".env.local",
        ],
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

# Installed builds keep user configuration beside the user's database, never in the bundle.
if os.environ.get('VIDEO_ANALYZER_DESKTOP') == '1' and not settings.google_api_key:
    try:
        saved = json.loads((Path(settings.database_path).parent / 'desktop-settings.json').read_text(encoding='utf-8'))
        key = saved.get('google_api_key') if isinstance(saved, dict) else None
        if isinstance(key, str) and 0 < len(key) <= 200:
            settings.google_api_key = key
    except (OSError, ValueError):
        pass  # First run or damaged settings: the UI lets the user configure the key again.
