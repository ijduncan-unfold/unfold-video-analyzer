"""Keep FFmpeg and FFprobe from flashing console windows in the desktop app."""
import os
import subprocess


def hidden_process_kwargs() -> dict[str, int]:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
