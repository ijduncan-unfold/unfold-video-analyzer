"""Exercise the actual SDK serialization, not just a mocked analysis pass."""
import asyncio
from unittest.mock import patch

import pytest
from google import genai
from google.genai.errors import ClientError
from pydantic import ValidationError

from app.models.analysis import SceneDetectionResult, ShotDetectionResult
from app.services.analyzer import _failure
from app.models.cosplay import CosplayIdentification
from app.services.flash_pass import _generate, provider_schema


@pytest.mark.parametrize('schema', [SceneDetectionResult, ShotDetectionResult])
def test_request_uses_native_json_schema_without_sdk_field_rewriting(schema):
    captured = []

    class Captured(Exception):
        pass

    async def run():
        client = genai.Client(api_key='nonfunctional-test-placeholder')
        async def capture(method, path, request_dict, *args, **kwargs):
            captured.append(request_dict)
            raise Captured()  # Stop before any network traffic.
        try:
            with patch.object(client._api_client, 'async_request', side_effect=capture):
                with pytest.raises(Captured):
                    await _generate(client, stage='schema_test', schema=schema, contents=['test'])
        finally:
            await client.aio.aclose()
            client.close()

    asyncio.run(run())
    assert len(captured) == 1
    config = captured[0]['generationConfig']
    assert 'responseSchema' not in config
    assert config['responseJsonSchema'] == provider_schema(schema)
    if schema is ShotDetectionResult:
        costume = config['responseJsonSchema']['$defs']['CosplayIdentification']
        assert 'additionalProperties' not in costume
        assert 'maxLength' not in costume['properties']['character']
        assert 'maxItems' not in costume['properties']['possible_characters']
        assert costume['properties']['character']['type'] == 'string'
        assert costume['properties']['identification']['enum'] == ['suggested', 'confirmed', 'unknown']
        # Generating a compact schema must not weaken stored-data validation.
        local = schema.model_json_schema()['$defs']['CosplayIdentification']
        assert local['additionalProperties'] is False
        assert local['properties']['character']['maxLength'] == 200
        assert local['properties']['possible_characters']['maxItems'] == 10


def test_schema_error_is_actionable_without_leaking_provider_message(caplog):
    error = ClientError(400, {'error': {'message': 'Invalid JSON payload: Unknown name "additional_properties" in response_schema https://private.test?key=SECRET-KEY'}})
    message = _failure('Shot analysis', error)
    assert 'response schema (HTTP 400)' in message
    assert 'application compatibility' in message
    assert 'SECRET-KEY' not in message + caplog.text
    assert 'private.test' not in message + caplog.text
    assert 'additionalproperties' in caplog.text


@pytest.mark.parametrize('code', [401, 403, 404])
def test_permanent_provider_failures_identify_the_action(code):
    message = _failure('Shot analysis', ClientError(code, {'error': {'message': 'private provider payload'}}))
    assert f'HTTP {code}' in message
    assert 'private provider payload' not in message


@pytest.mark.parametrize("payload", [
    {"character": "x" * 201},
    {"possible_characters": [str(i) for i in range(11)]},
    {"unexpected": "field"},
])
def test_local_validation_retains_limits_omitted_from_provider_schema(payload):
    with pytest.raises(ValidationError):
        CosplayIdentification.model_validate(payload)
