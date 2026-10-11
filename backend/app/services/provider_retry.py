"""Bounded retries for temporary provider failures; never log request payloads."""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def retry_temporary(operation, *, stage, timeout=90, attempts=3):
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(operation(), timeout=timeout)
        except Exception as error:
            code = getattr(error, 'code', None)
            temporary = isinstance(error, TimeoutError) or code in {408, 429, 500, 502, 503, 504}
            if not temporary or attempt + 1 == attempts:
                raise
            logger.warning('%s temporarily unavailable (%s, code=%s); retry %d of %d',
                           stage, type(error).__name__, code if isinstance(code, int) else None,
                           attempt + 2, attempts)
            await asyncio.sleep(2 ** (attempt + 1))
