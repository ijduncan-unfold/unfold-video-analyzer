import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.provider_retry import retry_temporary


class ProviderError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('private payload?key=DO_NOT_LOG')


@pytest.mark.parametrize('error', [ProviderError(500), ProviderError(503), ProviderError(429), TimeoutError()])
def test_temporary_failures_recover_without_logging_payload(error, caplog):
    operation = AsyncMock(side_effect=[error, error, 'valid result'])
    with patch('app.services.provider_retry.asyncio.sleep', AsyncMock()) as sleep:
        assert asyncio.run(retry_temporary(operation, stage='analysis')) == 'valid result'
    assert operation.await_count == 3 and sleep.await_count == 2
    assert 'DO_NOT_LOG' not in caplog.text


def test_retries_are_bounded_and_permanent_failures_are_not_retried():
    for error, count in [(ProviderError(503), 3), (ProviderError(403), 1), (ValueError('bad data'), 1)]:
        operation = AsyncMock(side_effect=error)
        with patch('app.services.provider_retry.asyncio.sleep', AsyncMock()), pytest.raises(type(error)):
            asyncio.run(retry_temporary(operation, stage='analysis'))
        assert operation.await_count == count


def test_cancellation_is_immediate():
    operation = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(retry_temporary(operation, stage='analysis'))
    assert operation.await_count == 1
