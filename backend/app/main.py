import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_logger = logging.getLogger('app')
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    os.makedirs(settings.upload_dir, exist_ok=True)
    from app.services.job_store import recover_interrupted_jobs
    from app.services.job_runner import shutdown_jobs
    recover_interrupted_jobs()
    from app.services.media_cleanup import cleanup_worker
    cleanup_task = asyncio.create_task(cleanup_worker())
    yield
    cleanup_task.cancel()
    await asyncio.gather(cleanup_task, return_exceptions=True)
    from app.services.visual_index import shutdown
    await shutdown()
    await shutdown_jobs()


app = FastAPI(title="Video Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
from app.routers import upload, analyze, status, results, export, files, thumbnails, compare, library, visual, premiere

app.include_router(upload.router)
app.include_router(analyze.router)
app.include_router(status.router)
app.include_router(results.router)
app.include_router(export.router)
app.include_router(files.router)
app.include_router(thumbnails.router)
app.include_router(compare.router)
app.include_router(library.router)
app.include_router(visual.router)
app.include_router(premiere.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/capabilities")
async def capabilities():
    import shutil
    return {"google_configured": bool(settings.google_api_key),
            "models": {"analysis": settings.gemini_analysis_model, "deep": settings.gemini_deep_model},
            "local_media": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "search_type": "keyword", "workspace_mode": "local_single_user"}


@app.post("/api/validate-key")
async def validate_key(body: dict):
    """Validate a Google API key by making a lightweight API call."""
    key = body.get("api_key", "").strip()
    if not key:
        return {"valid": False, "error": "No API key provided"}
    try:
        from google import genai
        client = genai.Client(api_key=key)
        # List models as a cheap validation call
        import asyncio
        await asyncio.to_thread(lambda: next(iter(client.models.list())))
        return {"valid": True}
    except Exception:
        return {"valid": False, "error": "Unable to validate this Gemini key. Check the key and its project permissions."}


# Serve frontend build in production (when frontend/dist exists)
_frontend_dist = Path(os.environ.get('VIDEO_ANALYZER_FRONTEND_DIR',
    str(Path(__file__).parent.parent.parent / "frontend" / "dist")))
if _frontend_dist.is_dir():
    from fastapi.responses import FileResponse

    # Serve static assets (JS, CSS, images)
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve the React SPA for any non-API route."""
        from fastapi import HTTPException
        if full_path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        file_path = (_frontend_dist / full_path).resolve()
        if not file_path.is_relative_to(_frontend_dist.resolve()):
            raise HTTPException(404, "Not found")
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_frontend_dist / "index.html")
