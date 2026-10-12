"""Read-only availability checks kept off the library request's event loop."""
import asyncio
from pathlib import Path
import time

_cache = {}


async def source_status(jobs):
    paths = {job.local_path for job in jobs if job and job.local_path}
    semaphore = asyncio.Semaphore(4)

    def offline(path):
        try:
            return not Path(path).is_file()
        except OSError:
            return True

    async def check(path):
        async with semaphore:
            cached = _cache.get(path)
            if cached and cached[0] > time.monotonic():
                return path, cached[1]
            result = await asyncio.to_thread(offline, path)
            if len(_cache) >= 1024:
                _cache.clear()
            _cache[path] = (time.monotonic() + 2, result)
            return path, result

    states = dict(await asyncio.gather(*(check(path) for path in paths)))
    return {job.job_id: states.get(job.local_path) for job in jobs if job}
