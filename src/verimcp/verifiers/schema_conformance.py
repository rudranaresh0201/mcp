"""The generic, zero-setup verifier: unlike every other verifier here, this
one doesn't know anything domain-specific about any particular tool. It only
checks that a tool's structuredContent conforms to the outputSchema that
tool *itself* declared in tools/list -- a real, standard MCP spec field
("if an output schema is provided, servers MUST provide structured results
that conform to this schema", modelcontextprotocol.io/specification, Tools).

This is deliberately a shallower check than the domain verifiers
(FilesystemVerifier, GitCommitVerifier, ...): it can't prove *this specific
claim is true*, only that *the response is shaped the way the tool itself
promised it would be*. What it buys is breadth those verifiers can't: it
applies to any tool on any backend that declares an outputSchema, with zero
verifier-writing required, closing the "completely unverified on an unknown
tool" gap. See docs/adr/0008-schema-conformance-baseline-layer.md.

Unlike every other verifier, this one isn't stateless and isn't discovered
via the verimcp.verifiers entry-point group -- it needs live access to
whatever outputSchemas Proxy has observed from real tools/list responses in
*this* session, so Proxy constructs and owns it directly, sharing the same
dict by reference (see proxy.py's `_tool_output_schemas`).
"""
from pathlib import Path
from typing import Any

import jsonschema

from verimcp.verifiers.base import Verifier


class SchemaConformanceVerifier(Verifier):
    def __init__(self, tool_output_schemas: dict[str, dict]) -> None:
        self._schemas = tool_output_schemas

    def applies_to(self, tool_name: str) -> bool:
        return tool_name in self._schemas

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        tool_name = request.get("params", {}).get("name")
        schema = self._schemas.get(tool_name)
        if schema is None:
            return response  # applies_to() already confirmed this, but don't assume it under a race

        structured = result.get("structuredContent")
        if structured is None:
            return self._override(
                response, f"tool {tool_name!r} declares an outputSchema but the response has no structuredContent"
            )

        try:
            jsonschema.validate(instance=structured, schema=schema)
        except jsonschema.exceptions.ValidationError as exc:
            return self._override(
                response,
                f"structuredContent for {tool_name!r} does not conform to its own declared outputSchema: {exc.message}",
            )
        except jsonschema.exceptions.SchemaError:
            return response  # the tool's own declared schema is malformed -- the backend's bug, not something to block a caller on

        return response  # verified: the response is shaped the way the tool itself promised
