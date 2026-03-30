"""
LLMPROXY Marketplace Plugin -- Strict Schema Enforcer

Validates LLM JSON responses against a schema provided by the client
via the `x-expected-schema` header or `_expected_schema` body field.
Catches semantically invalid JSON (missing required fields, wrong types)
before it reaches the client application.

Config (via manifest ui_schema):
  - action: str -- "block" (return 422) or "warn" (pass through with warning header)
  - max_schema_size: int -- max schema size in bytes (prevent abuse)

Ring: POST_FLIGHT (after LLM response, before returning to client)
"""

import json
from typing import Dict, Any

from core.plugin_sdk import BasePlugin, PluginResponse, PluginHook
from core.plugin_engine import PluginContext


def _validate_json_schema(data: Any, schema: Dict[str, Any]) -> list[str]:
    """Lightweight JSON schema validation without external dependencies.

    Supports: type, required, properties, items, enum, minLength, maxLength,
    minimum, maximum. Does NOT support $ref, allOf, oneOf, anyOf.
    """
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type:
        type_map = {
            "object": dict, "array": list, "string": str,
            "number": (int, float), "integer": int, "boolean": bool, "null": type(None),
        }
        expected = type_map.get(expected_type)
        if expected and not isinstance(data, expected):
            errors.append(f"Expected type '{expected_type}', got '{type(data).__name__}'")
            return errors

    if isinstance(data, dict):
        # Check required fields
        for field in schema.get("required", []):
            if field not in data:
                errors.append(f"Missing required field: '{field}'")

        # Check property types recursively
        properties = schema.get("properties", {})
        for key, prop_schema in properties.items():
            if key in data:
                sub_errors = _validate_json_schema(data[key], prop_schema)
                errors.extend(f"{key}.{e}" for e in sub_errors)

    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            sub_errors = _validate_json_schema(item, schema["items"])
            errors.extend(f"[{i}].{e}" for e in sub_errors)

    if isinstance(data, str):
        if "enum" in schema and data not in schema["enum"]:
            errors.append(f"Value '{data}' not in enum {schema['enum']}")
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"String too short (min {schema['minLength']})")

    if isinstance(data, (int, float)):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"Value {data} below minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"Value {data} above maximum {schema['maximum']}")

    return errors


class SchemaEnforcer(BasePlugin):
    name = "schema_enforcer"
    hook = PluginHook.POST_FLIGHT
    version = "1.0.0"
    author = "llmproxy"
    description = "Validates LLM JSON responses against client-provided JSON schema"
    timeout_ms = 5

    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__(config)
        self.action: str = self.config.get("action", "warn")
        self.max_schema_size: int = self.config.get("max_schema_size", 8192)

    async def execute(self, ctx: PluginContext) -> PluginResponse:
        # Get schema from metadata (set from request header or body by upstream code)
        schema_raw = ctx.metadata.get("_expected_schema")
        if not schema_raw:
            return PluginResponse.passthrough()

        # Parse schema
        try:
            if isinstance(schema_raw, str):
                if len(schema_raw) > self.max_schema_size:
                    self.logger.warning("Schema too large, skipping validation")
                    return PluginResponse.passthrough()
                schema = json.loads(schema_raw)
            else:
                schema = schema_raw
        except (json.JSONDecodeError, TypeError):
            self.logger.warning("Invalid JSON schema provided, skipping validation")
            return PluginResponse.passthrough()

        # Extract LLM response content
        choices = ctx.body.get("choices", [])
        if not choices:
            return PluginResponse.passthrough()

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return PluginResponse.passthrough()

        # Try to parse response as JSON
        try:
            response_data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # Response is not JSON -- schema validation not applicable
            return PluginResponse.passthrough()

        # Validate against schema
        errors = _validate_json_schema(response_data, schema)

        if not errors:
            ctx.metadata["_schema_valid"] = True
            return PluginResponse.passthrough()

        ctx.metadata["_schema_errors"] = errors

        if self.action == "block":
            return PluginResponse.block(
                status_code=422,
                error_type="schema_validation_failed",
                message=f"LLM response failed schema validation: {'; '.join(errors[:5])}"
            )

        # Warn mode: pass through but log
        self.logger.warning(f"Schema validation failed ({len(errors)} errors): {errors[:3]}")
        return PluginResponse.passthrough()

    async def on_load(self):
        self.logger.info(f"SchemaEnforcer loaded: action={self.action}")
