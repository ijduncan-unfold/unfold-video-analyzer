"""Small provider schemas with stronger Pydantic validation retained locally.

Gemini supports a subset of schema properties:
https://ai.google.dev/gemini-api/docs/generate-content/structured-output
"""
from google.genai import types


def provider_schema(model):
    document = model.model_json_schema()
    definitions = document.get('$defs', {})
    def convert(node):
        if '$ref' in node:
            node = definitions[node['$ref'].split('/')[-1]]
        result = {'type': node['type'].upper()}
        if 'properties' in node:
            result['properties'] = {name: convert(value) for name, value in node['properties'].items()}
            result['required'] = node.get('required', [])
        if 'items' in node:
            result['items'] = convert(node['items'])
        if 'enum' in node:
            result['enum'] = node['enum']
        return types.Schema(**result)
    return convert(document)
